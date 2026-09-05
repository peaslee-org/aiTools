import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("AUDIO_BUCKET_NAME", "b")
os.environ.setdefault("PHOTOGRAMMETRY_SQS_QUEUE_URL", "https://sqs.test/q")

import main  # noqa: E402
from gpu_worker.spot_watcher import SpotWatcher  # noqa: E402
from pipeline.reconstruct import Reconstruction  # noqa: E402


def test_handlers_and_deps_wiring(tmp_path):
    assert set(main.HANDLERS) == {"photogrammetry_job"}
    with patch("main.S3Client"), patch("main.make_session_factory"):
        deps = main.build_deps(main.settings)
    recon = deps.reconstruction_factory(tmp_path, 10.0)
    assert isinstance(recon, Reconstruction)
    assert recon._r._deadline == 10.0
    assert recon._r._interrupted is SpotWatcher.interrupted
    from gpu_worker.release_watcher import ReleaseWatcher
    assert recon._r._released is ReleaseWatcher.abort      # immediate admin release aborts the child
    assert recon._gpu is True
    assert recon._mask_model_path == Path("/opt/models/u2netp.onnx")   # MASK_MODEL_PATH default
    assert deps.job_timeout_seconds == 3600 and deps.use_gpu is True


def test_handler_passes_receive_count(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "process_photogrammetry_job", lambda body, deps, receive_count=1: seen.update(rc=receive_count))
    main.HANDLERS["photogrammetry_job"]({"job_id": "x"}, {"Attributes": {"ApproximateReceiveCount": "2"}})
    assert seen["rc"] == 2


def test_run_sweeps_stale_scratch_before_polling(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "sweep_stale", lambda root, **kw: calls.append(root) or [])
    monkeypatch.setattr(main, "run_sqs_worker", lambda **kw: "idle")
    monkeypatch.setattr(main, "GpuSessionStore", lambda *a, **k: object())
    monkeypatch.setattr(main, "task_arn", lambda: "t"); monkeypatch.setattr(main, "instance_id", lambda: "i")
    with patch("main.S3Client"), patch("main.make_session_factory"):
        main.run()
    assert calls == [Path(main.settings.WORK_DIR)]
