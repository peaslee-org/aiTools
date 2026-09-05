import logging
from pathlib import Path

from config import Settings
from gpu_worker.db import make_session_factory
from gpu_worker.ecs_metadata import instance_id, task_arn
from gpu_worker.release_watcher import ReleaseWatcher
from gpu_worker.session import GpuSessionStore
from gpu_worker.spot_watcher import SpotWatcher
from gpu_worker.sqs import receive_count, run_sqs_worker
from handlers.photogrammetry import Deps, process_photogrammetry_job
from pipeline.checkpoints import sweep_stale
from pipeline.reconstruct import Reconstruction
from pipeline.runner import Runner
from services.s3 import S3Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()


def build_deps(s: Settings) -> Deps:
    return Deps(
        session_factory=make_session_factory(s.DATABASE_URL),
        s3=S3Client(s.AUDIO_BUCKET_NAME, s.AWS_REGION),
        reconstruction_factory=lambda work, deadline: Reconstruction(
            Runner(deadline=deadline, interrupted=SpotWatcher.interrupted, released=ReleaseWatcher.abort),
            work, use_gpu=bool(s.COLMAP_USE_GPU), mask_model_path=Path(s.MASK_MODEL_PATH)),
        work_root=Path(s.WORK_DIR),
        use_gpu=bool(s.COLMAP_USE_GPU),
        job_timeout_seconds=s.PHOTOGRAMMETRY_JOB_TIMEOUT_SECONDS,
        max_texture_size=s.TEXTURE_MAX_SIZE,
    )


DEPS = None
HANDLERS = {"photogrammetry_job": lambda body, msg: process_photogrammetry_job(body, DEPS, receive_count=receive_count(msg))}


def run() -> None:
    global DEPS
    DEPS = build_deps(settings)
    try:
        removed = sweep_stale(Path(settings.WORK_DIR))
        if removed:
            logger.info("Removed %d stale scratch dir(s)", len(removed))
    except Exception:
        # A sweep error must never stop the worker from polling for jobs.
        logger.warning("Scratch sweep failed", exc_info=True)
    logger.info("Photogrammetry worker started (idle_exit=%ss, max_lifetime=%ss, job_timeout=%ss)",
                settings.IDLE_EXIT_SECONDS, settings.MAX_LIFETIME_SECONDS, settings.PHOTOGRAMMETRY_JOB_TIMEOUT_SECONDS)
    run_sqs_worker(
        queue_url=settings.PHOTOGRAMMETRY_SQS_QUEUE_URL, region=settings.AWS_REGION, handlers=HANDLERS,
        session_store=GpuSessionStore(task_arn(), instance_id(), DEPS.session_factory),
        idle_exit_seconds=settings.IDLE_EXIT_SECONDS, max_lifetime_seconds=settings.MAX_LIFETIME_SECONDS,
        visibility_timeout=settings.SQS_VISIBILITY_TIMEOUT,
        visibility_extension_interval=settings.SQS_VISIBILITY_EXTENSION_INTERVAL,
    )


if __name__ == "__main__":
    run()
