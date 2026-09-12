"""Photogrammetry jobs: upload a photo set, queue it for the GPU worker, serve the result.

`PhotogrammetryService` is the real path (S3 + ECS via the shared GpuController).
`LocalPhotogrammetryService` (Task 6) is the in-process mock selected by USE_MOCK_PHOTOGRAMMETRY.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Optional
from uuid import UUID, uuid4

from app.core.exceptions import (
    ConcurrentJobLimitExceeded,
    ConflictError,
    ImageCountOutOfRange,
    NotFoundError,
    UploadIncomplete,
    WorkerNotDeployed,
)
from app.models.photogrammetry import STAGES  # noqa: F401  (re-exported for the mock/tests)
from app.repositories.photogrammetry import PhotogrammetryRepository
from app.schemas.photogrammetry import (
    JobCreateRequest,
    JobCreateResponse,
    JobListResponse,
    JobPhotosResponse,
    JobStatusResponse,
    MeshUrlResponse,
    PhotoItem,
    SampleJobResponse,
    SamplePhotosResponse,
    UploadTarget,
    extension_of,
)
from app.services.gpu_controller import GpuCapExceeded
from app.services.thumbnails import ensure_thumbnails, thumb_key_for

logger = logging.getLogger(__name__)

# Thumbnail generation runs as fire-and-forget tasks so /photos and /confirm answer inside
# CloudFront's 30 s origin timeout (147 photos took 2m28s synchronously, 504 on 2026-08-31).
# One task per thumbs prefix at a time; tasks hold their own strong reference here.
_THUMBS_IN_FLIGHT: set[str] = set()
_THUMB_TASKS: set[asyncio.Task] = set()

DOWNLOAD_TTL_SECONDS = 900
ACTIVE_FOR_GPU = ("queued", "processing")


def default_job_name(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"Scan {now:%Y-%m-%d %H:%M}"


MAX_DOWNLOAD_BASENAME = 80


def download_basename(job) -> str:
    """Filename stem for the job's downloads: a slug of its name, or `scan-<id>`."""
    slug = re.sub(r"[^a-z0-9]+", "-", job.name.lower()).strip("-")
    slug = slug[:MAX_DOWNLOAD_BASENAME].rstrip("-")
    return slug or f"scan-{job.id}"


class PhotogrammetryService:
    is_mock = False

    def __init__(self, repo: PhotogrammetryRepository, storage, settings, gpu=None, sqs=None):
        self._repo = repo
        self._storage = storage
        self._settings = settings
        self._gpu = gpu
        self._sqs = sqs

    # ── create / confirm ─────────────────────────────────────────────────────

    async def create_job(self, user_id: str, request: JobCreateRequest) -> JobCreateResponse:
        active = await self._repo.count_active_jobs(user_id)
        if active >= self._settings.max_concurrent_jobs:
            raise ConcurrentJobLimitExceeded()
        if len(request.filenames) > self._settings.photogrammetry_max_images:
            raise ImageCountOutOfRange(
                f"at most {self._settings.photogrammetry_max_images} images per scan"
            )
        job_id = uuid4()
        input_prefix = f"photogrammetry/{user_id}/{job_id}/input/"
        await self._repo.create_job(
            job_id=job_id,
            user_id=user_id,
            name=request.name or default_job_name(),
            image_count=len(request.filenames),
            input_prefix=input_prefix,
            remove_background=request.remove_background,
        )
        uploads = []
        for i, filename in enumerate(request.filenames, start=1):
            key = f"{input_prefix}{i:04d}.{extension_of(filename)}"
            uploads.append(UploadTarget(
                filename=filename,
                key=key,
                url=self._storage.generate_presigned_upload_url(
                    key, ttl_seconds=self._settings.photogrammetry_upload_ttl_seconds
                ),
            ))
        return JobCreateResponse(job_id=job_id, uploads=uploads)

    async def confirm_job(self, user_id: str, job_id: UUID) -> None:
        job = await self._get_or_404(user_id, job_id)
        if job.status != "pending":
            raise ConflictError("Job is not in pending state")
        if self._gpu is None:
            raise WorkerNotDeployed()
        uploaded = self._storage.list_keys_with_prefix(job.input_prefix)
        if len(uploaded) < job.image_count:
            raise UploadIncomplete(f"{len(uploaded)} of {job.image_count} images uploaded")
        await self._queue(job.id, user_id)
        # Start thumbnails now so they're ready before the Photos pane is first opened
        # (filtered like _input_keys; reuses the listing done for the count check above).
        keys = sorted(k for k in uploaded if "/" not in k[len(job.input_prefix):])
        self._kick_thumbnails(keys, self._thumbs_prefix_for(job.input_prefix))

    async def _queue(self, job_id: UUID, user_id: str) -> None:
        await self._repo.update_job_status(job_id, "queued")
        await self._repo.db.commit()
        if self._sqs is not None:
            self._sqs.publish_photogrammetry_job(job_id)  # after the commit so the worker finds the row
        try:
            await self._gpu.ensure_worker("job", user_id, job_id=job_id)
        except GpuCapExceeded:
            pass  # stays queued; the status poll retries via ensure_worker("resume")
        await self._repo.db.commit()

    # ── read ─────────────────────────────────────────────────────────────────

    async def get_job_status(self, user_id: str, job_id: UUID) -> JobStatusResponse:
        job = await self._get_or_404(user_id, job_id)
        gpu_state = None
        if self._gpu is not None and job.status in ACTIVE_FOR_GPU:
            gpu_state = await self._gpu.get_state()
            if gpu_state.worker_state == "off":
                try:
                    gpu_state = await self._gpu.ensure_worker("resume", user_id)
                except GpuCapExceeded as e:
                    gpu_state = gpu_state.model_copy(update={"notice": e.reason})
        return self._to_response(job, gpu_state)

    async def list_jobs(self, user_id: str, cursor: Optional[str], limit: int) -> JobListResponse:
        items, next_cursor = await self._repo.list_jobs(user_id, cursor, limit)
        return JobListResponse(
            items=[self._to_response(j) for j in items], next_cursor=next_cursor
        )

    async def get_mesh_url(self, user_id: str, job_id: UUID) -> MeshUrlResponse:
        job = await self._get_or_404(user_id, job_id)
        if job.status != "complete" or not job.mesh_s3_key:
            raise ConflictError("Mesh not yet available")
        stem = download_basename(job)
        presign = self._storage.generate_presigned_download_url
        return MeshUrlResponse(
            url=presign(job.mesh_s3_key, ttl_seconds=DOWNLOAD_TTL_SECONDS),
            download_url=presign(
                job.mesh_s3_key, ttl_seconds=DOWNLOAD_TTL_SECONDS,
                attachment_filename=f"{stem}.glb",
            ),
            preview_download_url=(
                presign(
                    job.preview_s3_key, ttl_seconds=DOWNLOAD_TTL_SECONDS,
                    attachment_filename=f"{stem}-preview.png",
                )
                if job.preview_s3_key else None
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=DOWNLOAD_TTL_SECONDS),
        )

    async def delete_job(self, user_id: str, job_id: UUID) -> None:
        job = await self._get_or_404(user_id, job_id)
        await self._repo.delete_job(job.id)

    async def set_visibility(
        self, user_id: str, job_id: UUID, is_public: bool
    ) -> JobStatusResponse:
        job = await self._repo.set_is_public(job_id, user_id, is_public)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found")
        return await self.get_job_status(user_id, job_id)

    # ── input photos ─────────────────────────────────────────────────────────

    async def list_job_photos(self, user_id: str, job_id: UUID) -> JobPhotosResponse:
        job = await self._get_or_404(user_id, job_id)
        status = job.photo_status  # None until the worker's SfM pass wrote it
        photos = await self._photos(job.input_prefix, status or {})
        return JobPhotosResponse(
            photos=photos,
            matched=sum(1 for v in status.values() if v == "registered") if status is not None else None,
            total=len(photos),
        )

    async def list_sample_photos(self) -> SamplePhotosResponse:
        photos = await self._photos(f"{self._settings.photogrammetry_sample_prefix}images/")
        if not photos:
            raise ConflictError("Sample photo set has not been uploaded")
        return SamplePhotosResponse(name="Sample scan", image_count=len(photos), photos=photos)

    @staticmethod
    def _thumbs_prefix_for(input_prefix: str) -> str:
        """Sibling of the inputs' own directory: …/<job>/input/ → …/<job>/thumbs/ and
        samples/photogrammetry/images/ → samples/photogrammetry/thumbs/. Never *inside* the
        inputs, where the worker (and the next listing) would take the thumbnails for photos."""
        return f"{PurePosixPath(input_prefix.rstrip('/')).parent}/thumbs/"

    @staticmethod
    def _masks_prefix_for(input_prefix: str) -> str:
        """…/<job>/input/ → …/<job>/masks/ — where the worker puts its mask overlay previews."""
        return f"{PurePosixPath(input_prefix.rstrip('/')).parent}/masks/"

    def _input_keys(self, prefix: str) -> list[str]:
        """The photos directly under `prefix`, sorted; anything nested deeper is not an input."""
        return sorted(
            k for k in self._storage.list_keys_with_prefix(prefix)
            if "/" not in k[len(prefix):]
        )

    def _kick_thumbnails(self, keys: list[str], thumbs_prefix: str) -> None:
        """Generate missing thumbnails in the background — at most one task per prefix.
        ensure_thumbnails skips thumbs that already exist, so re-kicks are cheap and a photo
        whose thumbnail keeps failing is simply retried on the next listing."""
        if not keys or thumbs_prefix in _THUMBS_IN_FLIGHT:
            return
        _THUMBS_IN_FLIGHT.add(thumbs_prefix)

        async def run() -> None:
            try:
                await asyncio.to_thread(ensure_thumbnails, self._storage, keys, thumbs_prefix)
            except Exception:  # noqa: BLE001 — thumbnails are best-effort, never fail a request
                logger.warning("background thumbnail generation failed for %s", thumbs_prefix,
                               exc_info=True)
            finally:
                _THUMBS_IN_FLIGHT.discard(thumbs_prefix)

        task = asyncio.get_running_loop().create_task(run())
        _THUMB_TASKS.add(task)
        task.add_done_callback(_THUMB_TASKS.discard)

    async def _photos(self, images_prefix: str, status: Optional[dict] = None) -> list[PhotoItem]:
        """Presigned originals + thumbnails. A missing thumbnail is null — generation is kicked
        in the background (normally already done at confirm time) and the client refetches;
        blocking here put the first listing of a 150-photo scan past CloudFront's 30 s origin
        timeout (2026-08-31)."""
        status = status or {}
        keys = self._input_keys(images_prefix)
        thumbs_prefix = self._thumbs_prefix_for(images_prefix)
        existing = set(self._storage.list_keys_with_prefix(thumbs_prefix))
        masks_prefix = self._masks_prefix_for(images_prefix)
        masks = set(self._storage.list_keys_with_prefix(masks_prefix))
        wanted = {key: thumb_key_for(key, thumbs_prefix) for key in keys}
        if any(tk not in existing for tk in wanted.values()):
            self._kick_thumbnails(keys, thumbs_prefix)
        presign = self._storage.generate_presigned_download_url
        items = []
        for key in keys:
            name = Path(key).name
            items.append(PhotoItem(
                filename=name,
                url=presign(key, ttl_seconds=DOWNLOAD_TTL_SECONDS),
                thumb_url=(
                    presign(wanted[key], ttl_seconds=DOWNLOAD_TTL_SECONDS)
                    if wanted[key] in existing else None
                ),
                status=status.get(name),
                mask_url=(
                    presign(f"{masks_prefix}{name}", ttl_seconds=DOWNLOAD_TTL_SECONDS)
                    if f"{masks_prefix}{name}" in masks else None
                ),
            ))
        return items

    # ── sample ───────────────────────────────────────────────────────────────

    async def create_sample_job(self, user_id: str) -> SampleJobResponse:
        active = await self._repo.count_active_jobs(user_id)
        if active >= self._settings.max_concurrent_jobs:
            raise ConcurrentJobLimitExceeded()
        if self._gpu is None:
            raise WorkerNotDeployed()
        prefix = f"{self._settings.photogrammetry_sample_prefix}images/"
        keys = self._input_keys(prefix)
        if not keys:
            raise ConflictError("Sample photo set has not been uploaded")
        job_id = uuid4()
        await self._repo.create_job(
            job_id=job_id, user_id=user_id, name="Sample scan",
            image_count=len(keys), input_prefix=prefix,
        )
        await self._queue(job_id, user_id)
        return SampleJobResponse(job_id=job_id)

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _get_or_404(self, user_id: str, job_id: UUID):
        job = await self._repo.get_job(job_id, user_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found")
        return job

    def _to_response(self, job, gpu_state=None) -> JobStatusResponse:
        preview_url = (
            self._storage.generate_presigned_download_url(
                job.preview_s3_key, ttl_seconds=DOWNLOAD_TTL_SECONDS
            )
            if job.preview_s3_key else None
        )
        return JobStatusResponse(
            job_id=job.id,
            name=job.name,
            status=job.status,
            stage=job.stage,
            image_count=job.image_count,
            preview_url=preview_url,
            error_message=job.error_message,
            warnings=list(job.warnings or []),
            mock=self.is_mock,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            worker_state=gpu_state.worker_state if gpu_state else None,
            estimated_wait_seconds=gpu_state.estimated_wait_seconds if gpu_state else None,
            gpu_notice=gpu_state.notice if gpu_state else None,
            is_public=job.is_public,
            remove_background=bool(getattr(job, "remove_background", False)),
        )


ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "photogrammetry"


class LocalPhotogrammetryService(PhotogrammetryService):
    """Mock for local dev (USE_MOCK_PHOTOGRAMMETRY=true): real Postgres, no S3, no ECS.

    confirm_job trusts the dev-upload sink and walks the job
    queued → processing(sfm → dense → mesh → texture) → complete on timers, then copies the
    committed placeholder mesh/preview into the sink under the job's output keys.
    """
    is_mock = True

    def __init__(self, repo: PhotogrammetryRepository, storage, settings):
        super().__init__(repo, storage, settings, gpu=None, sqs=None)

    async def confirm_job(self, user_id: str, job_id: UUID) -> None:
        job = await self._get_or_404(user_id, job_id)
        if job.status != "pending":
            raise ConflictError("Job is not in pending state")
        await self._repo.update_job_status(job.id, "queued")
        await self._repo.db.commit()
        asyncio.create_task(self._mock_process_job(job.id))

    async def create_sample_job(self, user_id: str) -> SampleJobResponse:
        active = await self._repo.count_active_jobs(user_id)
        if active >= self._settings.max_concurrent_jobs:
            raise ConcurrentJobLimitExceeded()
        job_id = uuid4()
        input_prefix = f"photogrammetry/{user_id}/{job_id}/input/"
        images = sorted((ASSET_DIR / "images").glob("*.jpg"))
        for i, path in enumerate(images, start=1):
            self._storage.write_object(f"{input_prefix}{i:04d}.jpg", path.read_bytes())
        await self._repo.create_job(
            job_id=job_id, user_id=user_id, name="Sample scan",
            image_count=len(images), input_prefix=input_prefix,
        )
        await self._repo.update_job_status(job_id, "queued")
        await self._repo.db.commit()
        asyncio.create_task(self._mock_process_job(job_id))
        return SampleJobResponse(job_id=job_id)

    async def list_sample_photos(self) -> SamplePhotosResponse:
        """Seed the committed sample photos into the dev sink once, then list them like prod."""
        images_prefix = f"{self._settings.photogrammetry_sample_prefix}images/"
        if not self._storage.list_keys_with_prefix(images_prefix):
            for i, path in enumerate(sorted((ASSET_DIR / "images").glob("*.jpg")), start=1):
                self._storage.write_object(f"{images_prefix}{i:04d}.jpg", path.read_bytes())
        return await super().list_sample_photos()

    async def _mock_process_job(self, job_id: UUID) -> None:
        try:
            await self._run_mock_process_job(job_id)
        except Exception:
            logger.exception("mock photogrammetry walk failed for job %s", job_id)

    async def _run_mock_process_job(self, job_id: UUID) -> None:
        import app.db.session as db_session

        delay = self._settings.mock_photogrammetry_stage_delay_seconds

        async def set_status(status: str, **kwargs) -> Optional[str]:
            async with db_session.AsyncSessionLocal() as session:
                try:
                    repo = PhotogrammetryRepository(session)
                    await repo.update_job_status(job_id, status, **kwargs)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        await asyncio.sleep(delay)
        for stage in STAGES:
            await set_status("processing", stage=stage)
            await asyncio.sleep(delay)

        async with db_session.AsyncSessionLocal() as session:
            job = await PhotogrammetryRepository(session).get_job_any(job_id)
        if job is None:
            logger.info("mock photogrammetry walk: job %s deleted mid-walk, aborting", job_id)
            return
        output_prefix = job.input_prefix.rsplit("input/", 1)[0] + "output/"
        mesh_key = f"{output_prefix}mesh.glb"
        preview_key = f"{output_prefix}preview.png"
        self._storage.write_object(mesh_key, (ASSET_DIR / "mesh.glb").read_bytes())
        self._storage.write_object(preview_key, (ASSET_DIR / "preview.png").read_bytes())
        await set_status("complete", mesh_s3_key=mesh_key, preview_s3_key=preview_key)
