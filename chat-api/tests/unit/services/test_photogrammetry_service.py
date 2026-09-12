"""Unit tests for PhotogrammetryService — no real DB, S3 or ECS."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import (
    ConcurrentJobLimitExceeded,
    ConflictError,
    ImageCountOutOfRange,
    NotFoundError,
    UploadIncomplete,
    WorkerNotDeployed,
)
from app.schemas.photogrammetry import JobCreateRequest
from app.services import photogrammetry_service as ps
from app.services.gpu_controller import GpuCapExceeded
from app.services.photogrammetry_service import (
    ASSET_DIR,
    LocalPhotogrammetryService,
    PhotogrammetryService,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def make_job(**overrides):
    job = MagicMock()
    job.id = overrides.get("id", uuid4())
    job.user_id = "user1"
    job.name = "Scan"
    job.status = overrides.get("status", "pending")
    job.stage = overrides.get("stage")
    job.image_count = overrides.get("image_count", 6)
    job.input_prefix = f"photogrammetry/user1/{job.id}/input/"
    job.mesh_s3_key = overrides.get("mesh_s3_key")
    job.preview_s3_key = overrides.get("preview_s3_key")
    job.error_message = None
    job.warnings = overrides.get("warnings")
    job.photo_status = overrides.get("photo_status")
    job.remove_background = overrides.get("remove_background", False)
    job.created_at = job.updated_at = NOW
    job.completed_at = None
    return job


def make_service(*, active_jobs=0, max_images=150, gpu=None, job=None, keys=None, sqs=None):
    repo = MagicMock()
    repo.count_active_jobs = AsyncMock(return_value=active_jobs)
    repo.create_job = AsyncMock(
        side_effect=lambda job_id, user_id, name, image_count, input_prefix, remove_background=False: make_job(
            id=job_id, image_count=image_count, remove_background=remove_background
        )
    )
    repo.get_job = AsyncMock(return_value=job)
    repo.get_job_any = AsyncMock(side_effect=lambda job_id: job)
    repo.update_job_status = AsyncMock()
    repo.list_jobs = AsyncMock(return_value=([], None))
    repo.delete_job = AsyncMock()
    repo.db = MagicMock()
    repo.db.commit = AsyncMock()

    storage = MagicMock()
    storage.generate_presigned_upload_url = MagicMock(
        side_effect=lambda k, ttl_seconds=900: f"https://up/{k}"
    )
    storage.generate_presigned_download_url = MagicMock(
        side_effect=lambda k, ttl_seconds=900, attachment_filename=None: (
            f"https://dl/{k}" + (f"?dl={attachment_filename}" if attachment_filename else "")
        )
    )
    storage.list_keys_with_prefix = MagicMock(return_value=keys if keys is not None else [])

    settings = MagicMock()
    settings.max_concurrent_jobs = 3
    settings.photogrammetry_max_images = max_images
    settings.photogrammetry_sample_prefix = "samples/photogrammetry/"
    settings.photogrammetry_upload_ttl_seconds = 3600

    return PhotogrammetryService(repo, storage, settings, gpu, sqs), repo, storage


def test_to_response_maps_null_warnings_to_empty_list():
    service, *_ = make_service()
    assert service._to_response(make_job(status="queued")).warnings == []
    job = make_job(status="processing", warnings=["Mesh simplified"])
    assert service._to_response(job).warnings == ["Mesh simplified"]


FILES = ["IMG_1.JPG", "b.png", "c.jpeg", "d.jpg", "e.jpg", "f.jpg"]


class TestCreateJob:
    async def test_remove_background_reaches_the_row(self):
        svc, repo, _ = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=FILES, remove_background=True))
        assert repo.create_job.await_args.kwargs["remove_background"] is True

    async def test_remove_background_defaults_false(self):
        svc, repo, _ = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=FILES))
        assert repo.create_job.await_args.kwargs["remove_background"] is False

    async def test_upload_urls_outlive_a_phone_sized_upload(self):
        # 15 minutes is the transcribe default, sized for one audio file. A 150-photo scan over
        # cellular outlives it, and an expired signature fails the PUT with nothing to resume
        # from — the job just sits in `pending`.
        svc, _, storage = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=FILES))
        ttls = {
            call.kwargs.get("ttl_seconds")
            for call in storage.generate_presigned_upload_url.call_args_list
        }
        assert ttls == {3600}

    async def test_429_at_cap(self):
        svc, *_ = make_service(active_jobs=3)
        with pytest.raises(ConcurrentJobLimitExceeded):
            await svc.create_job("user1", JobCreateRequest(filenames=FILES))

    async def test_422_over_max_images(self):
        svc, *_ = make_service(max_images=5)
        with pytest.raises(ImageCountOutOfRange):
            await svc.create_job("user1", JobCreateRequest(filenames=FILES))

    async def test_one_upload_per_file_with_padded_keys(self):
        svc, repo, storage = make_service()
        res = await svc.create_job("user1", JobCreateRequest(name="Mug", filenames=FILES))
        assert len(res.uploads) == 6
        prefix = f"photogrammetry/user1/{res.job_id}/input/"
        assert res.uploads[0].key == f"{prefix}0001.jpg"
        assert res.uploads[1].key == f"{prefix}0002.png"
        assert res.uploads[5].key == f"{prefix}0006.jpg"
        assert res.uploads[0].filename == "IMG_1.JPG"
        assert res.uploads[0].url == f"https://up/{prefix}0001.jpg"
        repo.create_job.assert_awaited_once()
        kwargs = repo.create_job.await_args.kwargs
        assert kwargs["name"] == "Mug"
        assert kwargs["image_count"] == 6
        assert kwargs["input_prefix"] == prefix

    async def test_default_name_when_omitted(self):
        svc, repo, _ = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=FILES))
        assert repo.create_job.await_args.kwargs["name"].startswith("Scan ")


class TestConfirmJob:
    async def test_404_unknown(self):
        svc, *_ = make_service(job=None)
        with pytest.raises(NotFoundError):
            await svc.confirm_job("user1", uuid4())

    async def test_409_not_pending(self):
        svc, *_ = make_service(job=make_job(status="queued"))
        with pytest.raises(ConflictError):
            await svc.confirm_job("user1", uuid4())

    async def test_503_when_worker_not_deployed_and_job_stays_pending(self):
        job = make_job()
        svc, repo, _ = make_service(job=job, gpu=None, keys=[f"k{i}" for i in range(6)])
        with pytest.raises(WorkerNotDeployed):
            await svc.confirm_job("user1", job.id)
        repo.update_job_status.assert_not_awaited()

    async def test_409_when_uploads_incomplete(self):
        job = make_job(image_count=6)
        gpu = MagicMock()
        svc, repo, _ = make_service(job=job, gpu=gpu, keys=["a", "b"])
        with pytest.raises(UploadIncomplete):
            await svc.confirm_job("user1", job.id)
        repo.update_job_status.assert_not_awaited()

    async def test_queues_and_ensures_worker(self):
        job = make_job(image_count=6)
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock()
        svc, repo, storage = make_service(job=job, gpu=gpu, keys=[f"k{i}" for i in range(6)])
        await svc.confirm_job("user1", job.id)
        storage.list_keys_with_prefix.assert_called_once_with(job.input_prefix)
        repo.update_job_status.assert_awaited_once_with(job.id, "queued")
        repo.db.commit.assert_awaited()
        gpu.ensure_worker.assert_awaited_once_with("job", "user1", job_id=job.id)

    async def test_cap_exceeded_leaves_job_queued(self):
        job = make_job(image_count=6)
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock(side_effect=GpuCapExceeded("daily cap"))
        svc, repo, _ = make_service(job=job, gpu=gpu, keys=[f"k{i}" for i in range(6)])
        await svc.confirm_job("user1", job.id)  # must not raise
        repo.update_job_status.assert_awaited_once_with(job.id, "queued")

    async def test_confirm_kicks_thumbnail_generation_in_the_background(self):
        """Generation starts at confirm so the thumbs are usually ready before the Photos pane
        is first opened; /photos only kicks whatever is still missing."""
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock()
        job = make_job(image_count=2)
        keys = [f"{job.input_prefix}0001.jpg", f"{job.input_prefix}0002.jpg"]
        svc, _, storage = make_service(job=job, gpu=gpu, keys=keys)
        with patch.object(ps, "ensure_thumbnails") as ensure:
            await svc.confirm_job("user1", job.id)
            ensure.assert_not_called()
            await drain_thumbs()
        ensure.assert_called_once_with(storage, keys, f"photogrammetry/user1/{job.id}/thumbs/")

    async def test_confirm_publishes_after_commit_then_ensures_worker(self):
        order = []
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock(side_effect=lambda *a, **k: order.append("ensure"))
        sqs = MagicMock()
        sqs.publish_photogrammetry_job = MagicMock(side_effect=lambda job_id: order.append("publish"))
        job = make_job(status="pending", image_count=6)
        svc, repo, storage = make_service(gpu=gpu, job=job, keys=[f"k{i}" for i in range(6)], sqs=sqs)
        repo.db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
        await svc.confirm_job("user1", job.id)
        assert order[:3] == ["commit", "publish", "ensure"]
        sqs.publish_photogrammetry_job.assert_called_once_with(job.id)


class TestStatusAndMesh:
    async def test_status_echoes_remove_background(self):
        job = make_job(status="queued", remove_background=True)
        svc, *_ = make_service(job=job)
        assert (await svc.get_job_status("user1", job.id)).remove_background is True

    async def test_status_includes_preview_url_and_mock_false(self):
        job = make_job(status="complete", preview_s3_key="p/preview.png", mesh_s3_key="p/mesh.glb")
        svc, *_ = make_service(job=job)
        res = await svc.get_job_status("user1", job.id)
        assert res.preview_url == "https://dl/p/preview.png"
        assert res.mock is False
        assert res.name == "Scan" and res.image_count == 6

    async def test_status_resumes_worker_when_off(self):
        job = make_job(status="queued")
        gpu = MagicMock()
        off = MagicMock(worker_state="off", estimated_wait_seconds=180, notice=None)
        gpu.get_state = AsyncMock(return_value=off)
        gpu.ensure_worker = AsyncMock(
            return_value=MagicMock(worker_state="starting", estimated_wait_seconds=120, notice=None)
        )
        svc, *_ = make_service(job=job, gpu=gpu)
        res = await svc.get_job_status("user1", job.id)
        gpu.ensure_worker.assert_awaited_once_with("resume", "user1")
        assert res.worker_state == "starting"

    async def test_mesh_url_409_until_complete(self):
        svc, *_ = make_service(job=make_job(status="processing"))
        with pytest.raises(ConflictError):
            await svc.get_mesh_url("user1", uuid4())

    async def test_mesh_url_when_complete(self):
        job = make_job(status="complete", mesh_s3_key="p/mesh.glb")
        svc, *_ = make_service(job=job)
        res = await svc.get_mesh_url("user1", job.id)
        assert res.url == "https://dl/p/mesh.glb"
        assert res.expires_at > datetime.now(timezone.utc)

    async def test_mesh_url_includes_attachment_download_urls(self):
        job = make_job(status="complete", mesh_s3_key="p/mesh.glb", preview_s3_key="p/preview.png")
        job.name = "Scan 2026-08-28 10:15"
        svc, *_ = make_service(job=job)
        res = await svc.get_mesh_url("user1", job.id)
        assert res.url == "https://dl/p/mesh.glb"  # the viewer URL stays a plain GET
        assert res.download_url == "https://dl/p/mesh.glb?dl=scan-2026-08-28-10-15.glb"
        assert res.preview_download_url == "https://dl/p/preview.png?dl=scan-2026-08-28-10-15-preview.png"

    async def test_mesh_url_preview_download_absent_without_preview(self):
        job = make_job(status="complete", mesh_s3_key="p/mesh.glb")
        svc, *_ = make_service(job=job)
        res = await svc.get_mesh_url("user1", job.id)
        assert res.download_url == "https://dl/p/mesh.glb?dl=scan.glb"
        assert res.preview_download_url is None

    def test_download_basename_slugs_and_falls_back_to_job_id(self):
        job = make_job()
        job.name = "  Kitchen / table (v2) "
        assert ps.download_basename(job) == "kitchen-table-v2"
        job.name = "日本語"  # nothing ASCII survives the slug
        assert ps.download_basename(job) == f"scan-{job.id}"
        job.name = "x" * 300
        assert len(ps.download_basename(job)) <= 80

    async def test_delete_404_for_other_user(self):
        svc, repo, _ = make_service(job=None)
        with pytest.raises(NotFoundError):
            await svc.delete_job("user1", uuid4())
        repo.delete_job.assert_not_awaited()


class TestSampleJob:
    async def test_image_count_ignores_keys_nested_below_images(self):
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock()
        svc, repo, *_ = make_service(
            gpu=gpu, sqs=MagicMock(),
            keys=["samples/photogrammetry/images/0001.jpg", "samples/photogrammetry/images/thumbs/0001.jpg"],
        )
        await svc.create_sample_job("user1")
        assert repo.create_job.call_args.kwargs["image_count"] == 1

    async def test_409_when_sample_set_missing(self):
        svc, *_ = make_service(gpu=MagicMock(), keys=[])
        with pytest.raises(ConflictError):
            await svc.create_sample_job("user1")

    async def test_creates_queued_job_from_shared_prefix(self):
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock()
        svc, repo, storage = make_service(
            gpu=gpu,
            keys=[f"samples/photogrammetry/images/{i:04d}.jpg" for i in range(1, 8)],
        )
        res = await svc.create_sample_job("user1")
        kwargs = repo.create_job.await_args.kwargs
        assert kwargs["input_prefix"] == "samples/photogrammetry/images/"
        assert kwargs["image_count"] == 7
        assert kwargs["name"] == "Sample scan"
        repo.update_job_status.assert_awaited_once_with(res.job_id, "queued")
        gpu.ensure_worker.assert_awaited_once_with("job", "user1", job_id=res.job_id)

    async def test_sample_job_publishes(self):
        gpu = MagicMock()
        gpu.ensure_worker = AsyncMock()
        sqs = MagicMock()
        svc, repo, storage = make_service(
            gpu=gpu, keys=["samples/photogrammetry/images/0001.jpg"] * 8, sqs=sqs
        )
        r = await svc.create_sample_job("user1")
        sqs.publish_photogrammetry_job.assert_called_once_with(r.job_id)


def make_local(*, job=None, active_jobs=0):
    svc, repo, storage = make_service(job=job, active_jobs=active_jobs)
    storage.write_object = MagicMock()
    local = LocalPhotogrammetryService(svc._repo, storage, svc._settings)
    local._settings.mock_photogrammetry_stage_delay_seconds = 0
    return local, repo, storage


class FakeSessionFactory:
    """Stands in for app.db.session.AsyncSessionLocal; records the repo calls the walk makes."""
    def __init__(self, repo):
        self.repo = repo
        self.session = MagicMock()
        self.session.commit = AsyncMock()
        self.session.rollback = AsyncMock()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *a):
        return False


class TestLocalService:
    async def test_is_mock_flag_reaches_status(self):
        job = make_job(status="complete", preview_s3_key="p/preview.png")
        local, *_ = make_local(job=job)
        res = await local.get_job_status("user1", job.id)
        assert res.mock is True

    async def test_confirm_queues_without_gpu_and_schedules_walk(self):
        job = make_job()
        local, repo, _ = make_local(job=job)
        with patch.object(local, "_mock_process_job", new=AsyncMock()) as walk:
            await local.confirm_job("user1", job.id)
            await asyncio.sleep(0)
        repo.update_job_status.assert_awaited_once_with(job.id, "queued")
        walk.assert_awaited_once_with(job.id)

    async def test_walk_visits_every_stage_then_writes_outputs_and_completes(self):
        job = make_job()
        local, repo, storage = make_local(job=job)
        factory = FakeSessionFactory(repo)
        with patch.object(ps, "PhotogrammetryRepository", return_value=repo), \
             patch("app.db.session.AsyncSessionLocal", factory):
            await local._mock_process_job(job.id)
        calls = [
            c.args[1:] + (c.kwargs.get("stage"),) for c in repo.update_job_status.await_args_list
        ]
        assert calls[:4] == [
            ("processing", "sfm"),
            ("processing", "dense"),
            ("processing", "mesh"),
            ("processing", "texture"),
        ]
        final = repo.update_job_status.await_args_list[-1]
        assert final.args[1] == "complete"
        assert final.kwargs["mesh_s3_key"] == f"photogrammetry/user1/{job.id}/output/mesh.glb"
        assert final.kwargs["preview_s3_key"] == f"photogrammetry/user1/{job.id}/output/preview.png"
        written = {c.args[0] for c in storage.write_object.call_args_list}
        assert written == {final.kwargs["mesh_s3_key"], final.kwargs["preview_s3_key"]}
        assert factory.session.commit.await_count >= 5

    async def test_walk_aborts_quietly_when_job_deleted(self):
        # job=None (default) → repo.get_job_any returns None, simulating deletion mid-walk.
        local, repo, storage = make_local()
        factory = FakeSessionFactory(repo)
        with patch.object(ps, "PhotogrammetryRepository", return_value=repo), \
             patch("app.db.session.AsyncSessionLocal", factory):
            await local._mock_process_job(uuid4())
        storage.write_object.assert_not_called()
        calls = [
            c.args[1:] + (c.kwargs.get("stage"),) for c in repo.update_job_status.await_args_list
        ]
        assert calls == [
            ("processing", "sfm"),
            ("processing", "dense"),
            ("processing", "mesh"),
            ("processing", "texture"),
        ]

    async def test_sample_copies_assets_into_sink_and_queues(self):
        local, repo, storage = make_local()
        with patch.object(local, "_mock_process_job", new=AsyncMock()) as walk:
            res = await local.create_sample_job("user1")
            await asyncio.sleep(0)
        n = len(list((ASSET_DIR / "images").glob("*.jpg")))
        assert n >= 5
        assert storage.write_object.call_count == n
        first_key = storage.write_object.call_args_list[0].args[0]
        assert first_key == f"photogrammetry/user1/{res.job_id}/input/0001.jpg"
        kwargs = repo.create_job.await_args.kwargs
        assert kwargs["image_count"] == n and kwargs["name"] == "Sample scan"
        repo.update_job_status.assert_awaited_once_with(res.job_id, "queued")
        walk.assert_awaited_once_with(res.job_id)


async def drain_thumbs():
    """Run the background thumbnail tasks to completion (they discard themselves when done)."""
    while ps._THUMB_TASKS:
        await asyncio.gather(*list(ps._THUMB_TASKS), return_exceptions=True)


@pytest.fixture(autouse=True)
async def _clean_thumb_state():
    ps._THUMBS_IN_FLIGHT.clear()
    yield
    await drain_thumbs()
    ps._THUMBS_IN_FLIGHT.clear()


class TestListJobPhotos:
    async def test_mask_url_presigned_when_the_overlay_exists_beside_input(self):
        job = make_job(status="complete", remove_background=True)
        keys = [f"{job.input_prefix}0001.jpg", f"{job.input_prefix}0002.jpg"]
        thumbs_prefix = f"photogrammetry/user1/{job.id}/thumbs/"
        masks_prefix = f"photogrammetry/user1/{job.id}/masks/"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: {
            job.input_prefix: keys,
            thumbs_prefix: [f"{thumbs_prefix}0001.jpg", f"{thumbs_prefix}0002.jpg"],
            masks_prefix: [f"{masks_prefix}0002.jpg"],
        }.get(p, [])
        with patch.object(ps, "ensure_thumbnails"):
            res = await svc.list_job_photos("user1", job.id)
            await drain_thumbs()
        assert res.photos[0].mask_url is None
        assert res.photos[1].mask_url == f"https://dl/{masks_prefix}0002.jpg"

    async def test_404_when_not_owner(self):
        svc, *_ = make_service(job=None)
        with pytest.raises(NotFoundError):
            await svc.list_job_photos("user1", uuid4())

    async def test_presigns_photo_and_existing_thumbnail_under_the_job_thumbs_prefix(self):
        job = make_job(status="processing")
        keys = [f"{job.input_prefix}0002.jpg", f"{job.input_prefix}0001.png"]
        thumbs_prefix = f"photogrammetry/user1/{job.id}/thumbs/"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: (
            keys if p == job.input_prefix
            else [f"{thumbs_prefix}0002.jpg", f"{thumbs_prefix}0001.jpg"]
        )
        with patch.object(ps, "ensure_thumbnails") as ensure:
            res = await svc.list_job_photos("user1", job.id)
            await drain_thumbs()
        ensure.assert_not_called()      # everything already stored: nothing to generate
        assert [p.filename for p in res.photos] == ["0001.png", "0002.jpg"]
        assert res.photos[0].url == f"https://dl/{job.input_prefix}0001.png"
        assert res.photos[0].thumb_url == f"https://dl/{thumbs_prefix}0001.jpg"

    async def test_missing_thumbnail_is_null_and_generated_in_the_background(self):
        """/photos must answer inside CloudFront's 30 s origin timeout: 147 photos took 2m28s
        to thumbnail synchronously (504 on 2026-08-31). Missing thumbs come back null and the
        generation runs after the response; the client refetches while thumbs are pending."""
        job = make_job(status="complete")
        key = f"{job.input_prefix}0001.jpg"
        thumbs_prefix = f"photogrammetry/user1/{job.id}/thumbs/"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: [key] if p == job.input_prefix else []
        with patch.object(ps, "ensure_thumbnails") as ensure:
            res = await svc.list_job_photos("user1", job.id)
            ensure.assert_not_called()  # not before the response
            assert res.photos[0].thumb_url is None
            assert res.photos[0].url == f"https://dl/{key}"
            await drain_thumbs()
        ensure.assert_called_once_with(storage, [key], thumbs_prefix)

    async def test_thumbnail_generation_not_duplicated_while_in_flight(self):
        job = make_job(status="complete")
        key = f"{job.input_prefix}0001.jpg"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: [key] if p == job.input_prefix else []
        with patch.object(ps, "ensure_thumbnails") as ensure:
            await svc.list_job_photos("user1", job.id)
            await svc.list_job_photos("user1", job.id)   # first task not yet run
            await drain_thumbs()
        ensure.assert_called_once()

    async def test_photo_status_from_the_job_row_is_mapped_by_filename(self):
        job = make_job(status="failed")
        job.photo_status = {"0001.jpg": "registered", "0002.jpg": "unregistered", "0003.jpg": "skipped:unreadable"}
        keys = [f"{job.input_prefix}{n}" for n in ("0001.jpg", "0002.jpg", "0003.jpg", "0004.jpg")]
        svc, *_ = make_service(job=job, keys=keys)
        with patch.object(ps, "ensure_thumbnails", return_value={}):
            res = await svc.list_job_photos("user1", job.id)
        assert [p.status for p in res.photos] == ["registered", "unregistered", "skipped:unreadable", None]
        assert res.matched == 1 and res.total == 4

    async def test_photo_status_absent_means_no_statuses_and_no_matched_count(self):
        job = make_job(status="processing")
        job.photo_status = None
        svc, *_ = make_service(job=job, keys=[f"{job.input_prefix}0001.jpg"])
        with patch.object(ps, "ensure_thumbnails", return_value={}):
            res = await svc.list_job_photos("user1", job.id)
        assert res.photos[0].status is None
        assert res.matched is None and res.total == 1

    async def test_sample_job_thumbs_go_beside_images_not_inside(self):
        """A sample job's inputs are the shared samples/photogrammetry/images/ prefix. Its thumbs
        must land in the sibling thumbs/ — writing them under images/ makes the next sample job
        (and this listing) see 44 'photos' (2026-08-29)."""
        job = make_job(status="complete")
        job.input_prefix = "samples/photogrammetry/images/"
        key = "samples/photogrammetry/images/0001.jpg"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: [key] if p == job.input_prefix else []
        with patch.object(ps, "ensure_thumbnails") as ensure:
            await svc.list_job_photos("user1", job.id)
            await drain_thumbs()
        ensure.assert_called_once_with(storage, [key], "samples/photogrammetry/thumbs/")

    async def test_keys_nested_below_the_input_prefix_are_not_photos(self):
        job = make_job(status="complete")
        photo = f"{job.input_prefix}0001.jpg"
        stray = f"{job.input_prefix}thumbs/0001.jpg"
        svc, _, storage = make_service(job=job)
        storage.list_keys_with_prefix.side_effect = lambda p: (
            [stray, photo] if p == job.input_prefix else []
        )
        with patch.object(ps, "ensure_thumbnails") as ensure:
            res = await svc.list_job_photos("user1", job.id)
            await drain_thumbs()
        assert [p.filename for p in res.photos] == ["0001.jpg"]
        ensure.assert_called_once_with(storage, [photo], f"photogrammetry/user1/{job.id}/thumbs/")

    async def test_no_uploads_yet_is_an_empty_list_and_kicks_nothing(self):
        job = make_job(status="pending")
        svc, *_ = make_service(job=job, keys=[])
        with patch.object(ps, "ensure_thumbnails") as ensure:
            res = await svc.list_job_photos("user1", job.id)
            await drain_thumbs()
        assert res.photos == []
        ensure.assert_not_called()


class TestListSamplePhotos:
    async def test_409_when_sample_set_missing(self):
        svc, *_ = make_service(keys=[])
        with pytest.raises(ConflictError):
            await svc.list_sample_photos()

    async def test_sample_photos_have_no_mask_url(self):
        svc, *_ = make_service(keys=["samples/photogrammetry/images/0001.jpg"])
        with patch.object(ps, "ensure_thumbnails", return_value={}):
            res = await svc.list_sample_photos()
        assert res.photos[0].mask_url is None

    async def test_sample_photos_carry_no_status(self):
        svc, *_ = make_service(keys=["samples/photogrammetry/images/0001.jpg"])
        with patch.object(ps, "ensure_thumbnails", return_value={}):
            res = await svc.list_sample_photos()
        assert res.photos[0].status is None

    async def test_lists_under_the_sample_prefix_with_thumbs_beside_images(self):
        keys = ["samples/photogrammetry/images/0001.jpg", "samples/photogrammetry/images/0002.jpg"]
        svc, _, storage = make_service()
        storage.list_keys_with_prefix.side_effect = lambda p: (
            keys if p == "samples/photogrammetry/images/"
            else [k.replace("images/", "thumbs/") for k in keys]
        )
        with patch.object(ps, "ensure_thumbnails") as ensure:
            res = await svc.list_sample_photos()
            await drain_thumbs()
        ensure.assert_not_called()
        assert res.name == "Sample scan" and res.image_count == 2
        assert res.photos[1].thumb_url == "https://dl/samples/photogrammetry/thumbs/0002.jpg"

    async def test_needs_no_gpu_controller(self):
        keys = ["samples/photogrammetry/images/0001.jpg"]
        svc, *_ = make_service(keys=keys, gpu=None)
        with patch.object(ps, "ensure_thumbnails", return_value={}):
            res = await svc.list_sample_photos()
        assert res.image_count == 1


class TestLocalSamplePhotos:
    async def test_seeds_local_storage_from_the_committed_assets_once(self, tmp_path):
        from app.services.audio_storage import LocalAudioStorageService

        storage = LocalAudioStorageService("http://localhost:8000", str(tmp_path))
        settings = MagicMock(photogrammetry_sample_prefix="samples/photogrammetry/")
        svc = LocalPhotogrammetryService(MagicMock(), storage, settings)
        res = await svc.list_sample_photos()
        n = len(list((ASSET_DIR / "images").glob("*.jpg")))
        assert res.image_count == n == len(res.photos)
        assert res.photos[0].url.endswith("/dev-upload/samples/photogrammetry/images/0001.jpg")
        assert res.photos[0].thumb_url is None  # generation kicked in the background
        await drain_thumbs()                    # real Pillow thumbnails into local storage
        assert (tmp_path / "samples/photogrammetry/thumbs/0001.jpg").exists()
        again = await svc.list_sample_photos()
        assert again.image_count == n  # idempotent: no duplicate seeding
        assert again.photos[0].thumb_url.endswith("/dev-upload/samples/photogrammetry/thumbs/0001.jpg")
