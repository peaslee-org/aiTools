"""Handler walks the stages, writes outputs under the job's own prefix, and maps failures per spec §1."""
import math
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image

import handlers.photogrammetry as handler_mod
from gpu_worker.sqs import Interrupted
from handlers.photogrammetry import Deps, process_photogrammetry_job
from handlers.photogrammetry import FACE_BUDGET, MAX_ATTEMPTS, REFINE_MAX_FACES
from pipeline.checkpoints import Checkpoints
from pipeline.colmap import SparseModel
from pipeline.runner import StageError

USER = "user-1"


class FakeRecon:
    """Records stage calls; `registered` drives the threshold; `fail_at` raises in that stage;
    `faces` is what ReconstructMesh reports (RefineMesh reports 2×)."""
    def __init__(self, work, registered=10, fail_at=None, interrupt_at=None, faces=1000, mask_report=None):
        self.work, self.registered, self.fail_at, self.interrupt_at, self.faces = work, registered, fail_at, interrupt_at, faces
        self.calls = []
        self.dense_masks = None          # the `masks` flag the handler passed to dense()
        self.mask_report = None
        self._mask_report = mask_report

    def _step(self, name):
        self.calls.append(name)
        if name == self.interrupt_at: raise Interrupted()
        if name == self.fail_at: raise StageError("tool", f"{name} exploded")

    def sfm(self, images):
        self._step("sfm")
        names = frozenset(sorted(p.name for p in images.iterdir())[:self.registered])
        return SparseModel(self.work / "sparse" / "0", self.registered, names)
    def dense(self, images, model, masks=False):
        self._step("dense"); self.dense_masks = masks
        d = self.work / "dense"; d.mkdir(parents=True, exist_ok=True)
        if masks:
            from pipeline.masks import MaskReport
            self.mask_report = self._mask_report or MaskReport(masked=len(list(images.iterdir())), unmasked=[])
            ov = self.work / "overlays"; ov.mkdir(exist_ok=True)
            for p in sorted(images.iterdir()):
                Image.new("RGB", (4, 4)).save(ov / f"{p.stem}.jpg")
        return d
    def reconstruct_mesh(self, dense):
        self._step("mesh"); return dense / "m.ply", self.faces
    def refine_mesh(self, dense, ply):
        self._step("refine"); return dense / "r.ply", self.faces * 2
    def texture(self, dense, ply, decimate=None):
        self._step(("texture", decimate))
        Image.new("RGB", (2, 2)).save(dense / "tex.png")
        (dense / "scene_textured.mtl").write_text("newmtl m\nmap_Kd tex.png\n")
        obj = dense / "scene_textured.obj"
        obj.write_text("mtllib scene_textured.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl m\nf 1/1 2/2 3/3\n")
        return obj


class FakeS3:
    def __init__(self, keys):
        self.keys, self.uploaded, self.downloaded = keys, [], []
    def list_keys(self, prefix): return [k for k in self.keys if k.startswith(prefix)]
    def download(self, key, dest):
        self.downloaded.append(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 800)).save(dest)
    def upload_file(self, path, key, content_type): self.uploaded.append((key, content_type, Path(path).stat().st_size > 0))


class FailingDownloadS3(FakeS3):
    """download() raises a transient S3 ClientError (M15)."""
    def download(self, key, dest):
        raise ClientError({"Error": {"Code": "SlowDown", "Message": "x"}}, "GetObject")


class ConnectionFailingS3(FakeS3):
    """download() raises a connection-level BotoCoreError — no response, so no error code."""
    def download(self, key, dest):
        raise EndpointConnectionError(endpoint_url="https://s3.amazonaws.com")


class DeniedDownloadS3(FakeS3):
    """download() raises a permanent S3 ClientError — retrying cannot help."""
    def download(self, key, dest):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")


def make(tmp_path, *, status="queued", image_count=10, keys=None, recon_kwargs=None, s3_cls=FakeS3,
         include_placeholder=False, remove_background=False):
    job_id = uuid.uuid4()
    prefix = f"photogrammetry/{USER}/{job_id}/input/"
    keys = keys if keys is not None else [f"{prefix}{i:04d}.jpg" for i in range(1, image_count + 1)]
    if include_placeholder:
        keys = [prefix] + keys
    job = MagicMock(id=job_id, user_id=USER, status=status, stage=None, image_count=image_count,
                    input_prefix=prefix, mesh_s3_key=None, preview_s3_key=None, error_message=None, completed_at=None,
                    warnings=None, processing_started_at=None, remove_background=remove_background)
    session = MagicMock()
    session.get.return_value = job

    @contextmanager
    def factory():
        yield session

    recons = []
    def recon_factory(work, deadline):
        r = FakeRecon(work, **(recon_kwargs or {})); recons.append(r); return r

    s3 = s3_cls(keys)
    deps = Deps(session_factory=factory, s3=s3, reconstruction_factory=recon_factory,
                work_root=tmp_path / "work", use_gpu=False, job_timeout_seconds=3600)
    return job, s3, recons, deps


@pytest.fixture(autouse=True)
def passthrough_pack(monkeypatch):
    """No gltfpack on the test host: publish gets a copy-through pack by default. The real
    subprocess wiring is covered in test_export.py; tests below override this to steer it."""
    def _pack(glb, out, timeout=120):
        out.write_bytes(Path(glb).read_bytes())
        return out
    monkeypatch.setattr(handler_mod, "pack_glb", _pack, raising=False)


class MeshBytesS3(FakeS3):
    """Also captures the bytes uploaded as mesh.glb."""
    def upload_file(self, path, key, content_type):
        super().upload_file(path, key, content_type)
        if key.endswith("mesh.glb"):
            self.mesh_bytes = Path(path).read_bytes()


def test_publish_uploads_the_packed_glb(tmp_path, monkeypatch):
    """mesh.glb on S3 is gltfpack's output, not the raw trimesh export."""
    monkeypatch.setattr(handler_mod, "pack_glb",
                        lambda glb, out, **kw: (out.write_bytes(b"PACKED-GLB"), out)[1], raising=False)
    job, s3, recons, deps = make(tmp_path, s3_cls=MeshBytesS3)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert s3.mesh_bytes == b"PACKED-GLB"


def test_publish_falls_back_to_uncompressed_glb_when_pack_fails(tmp_path, monkeypatch):
    """Compression is an optimization: a gltfpack failure ships the raw GLB with a warning
    instead of failing the job."""
    def _boom(glb, out, **kw):
        raise subprocess.CalledProcessError(1, "gltfpack")
    monkeypatch.setattr(handler_mod, "pack_glb", _boom, raising=False)
    job, s3, recons, deps = make(tmp_path, s3_cls=MeshBytesS3)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert s3.mesh_bytes[:4] == b"glTF"
    assert any("compress" in w.lower() for w in job.warnings)


def test_claim_stamps_processing_started_at(tmp_path):
    """The first claim starts the job's billable GPU clock (cost-per-job in the usage panel)."""
    job, _, _, deps = make(tmp_path)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert isinstance(job.processing_started_at, datetime)


def test_processing_started_at_survives_a_resume(tmp_path):
    """A resumed job keeps its original claim time: the bill spans all attempts, and the
    stamp must not move on redelivery."""
    stamp = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    job, _, _, deps = make(tmp_path)
    job.processing_started_at = stamp
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.processing_started_at == stamp


def test_face_budget_is_one_million():
    """Raised 500 k → 1 M with meshopt compression (2026-08-31): a packed 1 M-face GLB is
    smaller than the old uncompressed 500 k one was."""
    assert FACE_BUDGET == 1_000_000


def test_happy_path_walks_stages_and_writes_outputs_under_job_prefix(tmp_path):
    job, s3, recons, deps = make(tmp_path)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons[0].calls == ["sfm", "dense", "mesh", "refine", ("texture", None)]
    assert job.status == "complete" and job.stage is None and isinstance(job.completed_at, datetime)
    assert job.mesh_s3_key == f"photogrammetry/{USER}/{job.id}/output/mesh.glb"
    assert job.preview_s3_key == f"photogrammetry/{USER}/{job.id}/output/preview.png"
    assert sorted(k for k, _, ok in s3.uploaded if ok) == [job.mesh_s3_key, job.preview_s3_key]
    assert dict((k, ct) for k, ct, _ in s3.uploaded)[job.mesh_s3_key] == "model/gltf-binary"
    assert not (tmp_path / "work" / str(job.id)).exists()   # scratch removed


def test_sample_job_outputs_go_under_job_prefix_not_input_prefix(tmp_path):
    job, s3, _, deps = make(tmp_path, keys=[f"samples/photogrammetry/images/{i:04d}.jpg" for i in range(1, 11)])
    job.input_prefix = "samples/photogrammetry/images/"
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.mesh_s3_key.startswith(f"photogrammetry/{USER}/{job.id}/output/")


def test_stage_progression_is_written_before_each_stage(tmp_path):
    job, _, recons, deps = make(tmp_path)
    seen = []
    orig = FakeRecon._step
    FakeRecon._step = lambda self, name: (seen.append(job.stage), orig(self, name))
    try:
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    finally:
        FakeRecon._step = orig
    assert seen == ["sfm", "dense", "mesh", "mesh", "texture"]


def test_refine_skipped_over_100_images(tmp_path):
    # registered must clear the 60% threshold (ceil(0.6*101)=61) so the job reaches the mesh stage
    job, _, recons, deps = make(tmp_path, image_count=101, recon_kwargs={"registered": 101})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert "refine" not in recons[0].calls and ("texture", None) in recons[0].calls


def test_registration_threshold_fails_job_with_message(tmp_path):
    job, _, recons, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 5})   # < ceil(6)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message == "Only 5 of 10 photos could be matched — add overlap and try again"
    assert recons[0].calls == ["sfm"]


def test_threshold_boundary_passes_at_ceil(tmp_path):
    job, _, _, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 6})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"


def test_missing_uploads_fail_before_any_stage(tmp_path):
    job, _, recons, deps = make(tmp_path, image_count=10, keys=[])
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message == "0 of 10 photos found in storage"
    assert recons == []


def test_stage_error_marks_failed_and_returns_normally(tmp_path):
    job, _, _, deps = make(tmp_path, recon_kwargs={"fail_at": "dense"})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)     # no raise → message acked
    assert job.status == "failed" and job.error_message == "dense exploded" and job.stage is None


def test_interrupt_resets_to_queued_and_raises(tmp_path):
    job, _, _, deps = make(tmp_path, recon_kwargs={"interrupt_at": "dense"})
    with pytest.raises(Interrupted):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "queued" and job.stage is None
    assert (tmp_path / "work" / str(job.id)).exists()   # scratch kept: the next attempt resumes


def test_redelivered_terminal_job_is_skipped(tmp_path):
    job, _, recons, deps = make(tmp_path, status="complete")
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons == [] and job.status == "complete"


def test_skipped_job_removes_its_scratch(tmp_path):
    """A job deleted (or already terminal) mid-run must not leave its dense workspace for the
    24 h sweep — rule 1 removes scratch just like the crash/receive-count and failure paths."""
    job, _, recons, deps = make(tmp_path, status="complete")
    w = work_dir(deps, job)
    (w / "dense").mkdir(parents=True)
    (w / "dense" / "x").write_text("x")
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons == [] and job.status == "complete"
    assert not w.exists()


def test_processing_job_is_restarted(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons and job.status == "complete"


def test_unknown_job_is_skipped(tmp_path):
    job, _, recons, deps = make(tmp_path)
    with deps.session_factory() as sess:
        sess.get.return_value = None
    process_photogrammetry_job({"job_id": str(uuid.uuid4())}, deps)
    assert recons == []


def test_error_message_is_capped_at_1000_chars(tmp_path):
    job, _, _, deps = make(tmp_path, recon_kwargs={"fail_at": "sfm"})
    class LongRecon(FakeRecon):
        def sfm(self, images): raise StageError("tool", "x" * 5000)
    deps = Deps(session_factory=deps.session_factory, s3=deps.s3,
                reconstruction_factory=lambda work, deadline: LongRecon(work),
                work_root=deps.work_root, use_gpu=False, job_timeout_seconds=3600)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert len(job.error_message) == 1000


def test_directory_placeholder_key_is_ignored_and_never_downloaded(tmp_path):
    """M11: S3 "folders" show up in list_keys as a zero-byte object ending in "/"."""
    job, s3, recons, deps = make(tmp_path, image_count=10, include_placeholder=True)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert job.input_prefix not in s3.downloaded
    assert len(s3.downloaded) == 10


def test_transient_s3_error_during_download_leaves_job_processing_for_redelivery(tmp_path):
    """M15: a ClientError/BotoCoreError while listing or downloading must not be swallowed by
    the generic except Exception (which would fail the job); it re-raises so SQS redelivers,
    and the row is left `processing` so the next attempt resumes from the markers."""
    job, s3, recons, deps = make(tmp_path, s3_cls=FailingDownloadS3)
    with pytest.raises(ClientError):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "processing"
    assert recons == []
    assert (tmp_path / "work" / str(job.id)).exists()   # scratch kept for redelivery


def test_connection_error_during_download_leaves_job_processing_for_redelivery(tmp_path):
    job, s3, recons, deps = make(tmp_path, s3_cls=ConnectionFailingS3)
    with pytest.raises(EndpointConnectionError):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "processing"
    assert (tmp_path / "work" / str(job.id)).exists()


def test_permanent_s3_error_during_download_fails_the_job(tmp_path):
    """AccessDenied / NoSuchKey never get better on redelivery: spinning to the DLQ would leave
    the row `processing` forever. Fail it with the S3 code so the user (and we) can see why."""
    job, s3, recons, deps = make(tmp_path, s3_cls=DeniedDownloadS3)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)     # swallowed → SQS acks
    assert job.status == "failed"
    assert job.stage is None
    assert "AccessDenied" in job.error_message
    assert recons == []
    assert not (tmp_path / "work" / str(job.id)).exists()


CRASH = "Reconstruction crashed during the {} stage (probably out of memory) — try fewer photos or one object per scan."


def work_dir(deps, job):
    return deps.work_root / str(job.id)


# ── mesh budget ──────────────────────────────────────────────────────────────

def test_refine_skipped_when_reconstructed_mesh_is_large(tmp_path):
    job, _, recons, deps = make(tmp_path, recon_kwargs={"faces": REFINE_MAX_FACES + 1})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert "refine" not in recons[0].calls and job.status == "complete"


def test_over_budget_mesh_is_decimated_with_warning(tmp_path):
    faces = 1_261_288
    job, _, recons, deps = make(tmp_path, recon_kwargs={"faces": faces})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    (_, ratio), = [c for c in recons[0].calls if isinstance(c, tuple) and c[0] == "texture"]
    assert ratio == pytest.approx(FACE_BUDGET / faces)
    assert job.warnings == [f"Mesh simplified from {faces:,} to about {FACE_BUDGET:,} faces to fit the viewer"]


def test_refined_meshes_always_fit_the_budget(tmp_path):
    """Refine roughly doubles faces and only runs at ≤ REFINE_MAX_FACES, so its output stays
    within FACE_BUDGET (≥ 2 × REFINE_MAX_FACES since the 1 M budget) — no decimation after
    refine, even at the gate's edge."""
    assert FACE_BUDGET >= 2 * REFINE_MAX_FACES
    job, _, recons, deps = make(tmp_path, recon_kwargs={"faces": REFINE_MAX_FACES})  # refine → 800k
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert "refine" in recons[0].calls
    (_, ratio), = [c for c in recons[0].calls if isinstance(c, tuple) and c[0] == "texture"]
    assert ratio is None


# ── checkpoints / resume ─────────────────────────────────────────────────────

def test_stage_markers_written_and_scratch_removed_on_complete(tmp_path):
    job, _, recons, deps = make(tmp_path)
    seen = {}
    orig = FakeRecon._step
    FakeRecon._step = lambda self, name: (seen.__setitem__(name if isinstance(name, str) else name[0],
                                                          sorted(p.name for p in work_dir(deps, job).glob("*.done"))), orig(self, name))
    try:
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    finally:
        FakeRecon._step = orig
    assert seen["dense"] == ["sfm.done"] and seen["mesh"] == ["dense.done", "sfm.done"]
    assert seen["texture"] == ["dense.done", "mesh.done", "sfm.done"]
    assert not work_dir(deps, job).exists()


def test_resume_skips_completed_stages_and_reuses_outputs(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    w = work_dir(deps, job); (w / "dense").mkdir(parents=True)
    ck = Checkpoints(w)
    ck.done("sfm", sparse=str(w / "sparse" / "0"), registered_images=10)
    ck.done("dense", dense=str(w / "dense"))
    ck.done("mesh", ply=str(w / "dense" / "m.ply"), faces=1000)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons[0].calls == [("texture", None)]
    assert job.status == "complete"


def test_resume_sets_db_stage_to_first_stage_run(tmp_path, monkeypatch):
    """job.stage must already read "dense" at receipt (before any stage runs), not merely by the
    time the dense stage's own `_update(stage="dense")` fires — that write would mask a broken
    `job.stage = ...` assignment at receipt. `work.mkdir()` is the very first filesystem call the
    handler makes after the receipt-time assignment (no earlier stage's markers exist to check
    once resuming is decided), so hooking it captures the value at that exact moment."""
    job, _, _, deps = make(tmp_path, status="processing")
    w = work_dir(deps, job); Checkpoints(w).done("sfm", sparse="s", registered_images=10)

    receipt_stage = []
    orig_mkdir = Path.mkdir
    def mkdir_recorder(self, *a, **kw):
        if not receipt_stage:
            receipt_stage.append(job.stage)
        return orig_mkdir(self, *a, **kw)
    monkeypatch.setattr(Path, "mkdir", mkdir_recorder)

    stage_updates = []
    orig_update = handler_mod._update
    def update_recorder(deps, job_id, **values):
        if "stage" in values:
            stage_updates.append(values["stage"])
        return orig_update(deps, job_id, **values)
    monkeypatch.setattr(handler_mod, "_update", update_recorder)

    process_photogrammetry_job({"job_id": str(job.id)}, deps)

    assert receipt_stage == ["dense"]              # set at receipt, before fetch/sfm/dense run
    assert stage_updates[0] == "dense" and "sfm" not in stage_updates


def test_resume_into_publish_reports_texture_stage_and_completes(tmp_path, monkeypatch):
    """All four `.done` markers exist and there is no `stage.started` — first_incomplete() is
    "publish", so no reconstruction stage runs at all; only export/upload/complete. Covers the
    `job.stage = "texture" if first_stage == "publish" else first_stage` mapping: while `obj_to_glb`
    (the export step) runs, the DB-facing stage must read "texture", the last real pipeline stage,
    not "publish"."""
    job, s3, recons, deps = make(tmp_path, status="processing")
    w = work_dir(deps, job)
    dense = w / "dense"; dense.mkdir(parents=True)
    Image.new("RGB", (2, 2)).save(dense / "tex.png")
    (dense / "scene_textured.mtl").write_text("newmtl m\nmap_Kd tex.png\n")
    obj = dense / "scene_textured.obj"
    obj.write_text("mtllib scene_textured.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl m\nf 1/1 2/2 3/3\n")
    ck = Checkpoints(w)
    ck.done("sfm", sparse=str(w / "sparse" / "0"), registered_images=10)
    ck.done("dense", dense=str(dense))
    ck.done("mesh", ply=str(dense / "m.ply"), faces=1000)
    ck.done("texture", obj=str(obj))

    stages_at_export = []
    orig_obj_to_glb = handler_mod.obj_to_glb
    def wrapper(obj_path, out, **kw):
        stages_at_export.append(job.stage)
        return orig_obj_to_glb(obj_path, out, **kw)
    monkeypatch.setattr("handlers.photogrammetry.obj_to_glb", wrapper)

    process_photogrammetry_job({"job_id": str(job.id)}, deps)

    assert recons[0].calls == []   # reconstruction_factory still runs (unconditional) but no stage method fires
    assert job.status == "complete"
    assert job.mesh_s3_key == f"photogrammetry/{USER}/{job.id}/output/mesh.glb"
    assert stages_at_export == ["texture"]


def test_fetch_always_reruns_but_warnings_do_not_duplicate(tmp_path):
    job, s3, _, deps = make(tmp_path, status="processing")
    job.warnings = ["1 photo was rotated to match the others (phone auto-rotate)"]
    w = work_dir(deps, job); Checkpoints(w).done("sfm", sparse="s", registered_images=10)

    def download(key, dest):   # one landscape frame among portrait ones
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 600) if key.endswith("0001.jpg") else (600, 800)).save(dest)
    s3.download = download
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.warnings == ["1 photo was rotated to match the others (phone auto-rotate)"]


# ── the no-cycling rule ──────────────────────────────────────────────────────

def test_crashed_stage_fails_without_running_anything(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    w = work_dir(deps, job); ck = Checkpoints(w)
    ck.done("sfm", sparse="s", registered_images=10); ck.done("dense", dense="d"); ck.started("mesh")
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons == [] and job.status == "failed" and job.stage is None
    assert job.error_message == CRASH.format("mesh")
    assert not w.exists()


def test_crash_in_publish_reads_as_export(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    w = work_dir(deps, job); ck = Checkpoints(w)
    for s, d in (("sfm", {"sparse": "s", "registered_images": 10}), ("dense", {"dense": "d"}),
                 ("mesh", {"ply": "p", "faces": 1}), ("texture", {"obj": "o"})):
        ck.done(s, **d)
    ck.started("publish")
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message == CRASH.format("export")


def test_receive_count_over_max_attempts_fails(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    process_photogrammetry_job({"job_id": str(job.id)}, deps, receive_count=MAX_ATTEMPTS + 1)
    assert recons == [] and job.status == "failed"
    assert job.error_message == (
        "Reconstruction did not finish after 5 attempts (interrupted or out of memory)"
        " — try again with fewer photos or one object per scan.")


def test_receive_count_at_max_attempts_fails(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    process_photogrammetry_job({"job_id": str(job.id)}, deps, receive_count=MAX_ATTEMPTS)
    assert recons == [] and job.status == "failed"


def test_receive_count_below_max_attempts_runs(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing")
    process_photogrammetry_job({"job_id": str(job.id)}, deps, receive_count=MAX_ATTEMPTS - 1)
    assert job.status == "complete"


def test_interrupted_clears_started_and_keeps_scratch(tmp_path):
    job, _, _, deps = make(tmp_path, recon_kwargs={"interrupt_at": "dense"})
    with pytest.raises(Interrupted):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    w = work_dir(deps, job)
    assert job.status == "queued" and w.exists()
    assert Checkpoints(w).crashed_stage() is None and Checkpoints(w).completed("sfm") is not None


def test_stage_error_removes_scratch(tmp_path):
    job, _, _, deps = make(tmp_path, recon_kwargs={"fail_at": "dense"})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and not work_dir(deps, job).exists()


def test_transient_s3_error_keeps_scratch_for_redelivery(tmp_path):
    job, _, _, deps = make(tmp_path, s3_cls=FailingDownloadS3)
    w = work_dir(deps, job)
    Checkpoints(w).done("sfm", sparse="s", registered_images=10)
    with pytest.raises(ClientError):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "processing" and w.exists()
    assert Checkpoints(w).completed("sfm") is not None   # markers survive, not just the directory


# ── photos + warnings ────────────────────────────────────────────────────────

def test_rotated_photos_warn_and_count_as_usable(tmp_path):
    job, s3, recons, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 6})

    def download(key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 600) if key.endswith(("0009.jpg", "0010.jpg")) else (600, 800)).save(dest)
    s3.download = download
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert job.warnings[0] == "2 photos were rotated to match the others (phone auto-rotate)"


def test_skipped_photos_lower_the_registration_denominator(tmp_path):
    # 10 uploaded, 2 skipped → 8 usable → ceil(0.6*8)=5 needed; 5 registered passes
    job, s3, _, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 5})

    def download(key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100) if key.endswith(("0009.jpg", "0010.jpg")) else (600, 800)).save(dest)
    s3.download = download
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert job.warnings == ["2 photos have a different resolution and were skipped: 0009.jpg, 0010.jpg"]


def test_too_few_usable_photos_fails(tmp_path):
    job, s3, _, deps = make(tmp_path, image_count=6)

    def download(key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100) if key.endswith(("0005.jpg", "0006.jpg")) else (600, 800)).save(dest)
    s3.download = download
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message == "Only 4 photos could be used — at least 5 are needed"


def test_too_few_usable_photos_fails_singular(tmp_path):
    # 4 of the 5 uploads are unreadable, leaving exactly 1 usable photo (singular wording).
    job, s3, _, deps = make(tmp_path, image_count=5)

    def download(key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if key.endswith("0001.jpg"):
            Image.new("RGB", (600, 800)).save(dest)
        else:
            dest.write_bytes(b"not an image")
    s3.download = download
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message == "Only 1 photo could be used — at least 5 are needed"


def test_fresh_start_clears_old_warnings(tmp_path):
    job, _, _, deps = make(tmp_path)
    job.warnings = ["stale"]
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.warnings == []


def test_warnings_are_written_as_they_arise(tmp_path):
    faces = 1_900_000
    job, _, _, deps = make(tmp_path, recon_kwargs={"faces": faces})
    seen = []
    orig = FakeRecon._step
    FakeRecon._step = lambda self, name: (seen.append((name, list(job.warnings or []))), orig(self, name))
    try:
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    finally:
        FakeRecon._step = orig
    texture_entry = [w for n, w in seen if isinstance(n, tuple) and n[0] == "texture"][0]
    assert texture_entry and texture_entry[0].startswith("Mesh simplified")


# ── per-photo status: which inputs the model used ────────────────────────────

def test_photo_status_written_after_sfm_on_success(tmp_path):
    job, _, _, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 7})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "complete"
    assert job.photo_status == {**{f"{i:04d}.jpg": "registered" for i in range(1, 8)},
                                **{f"{i:04d}.jpg": "unregistered" for i in range(8, 11)}}


def test_photo_status_written_before_registration_failure(tmp_path):
    job, _, _, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 5})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.status == "failed" and job.error_message.startswith("Only 5 of 10 photos could be matched")
    assert sum(v == "registered" for v in job.photo_status.values()) == 5
    assert set(job.photo_status) == {f"{i:04d}.jpg" for i in range(1, 11)}


class OddSizeS3(FakeS3):
    """0003.jpg comes down at a different resolution (normalise skips it); 0004.jpg is not an image."""
    def download(self, key, dest):
        self.downloaded.append(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if key.endswith("0003.jpg"):
            Image.new("RGB", (100, 100)).save(dest)
        elif key.endswith("0004.jpg"):
            dest.write_bytes(b"not an image")
        else:
            Image.new("RGB", (600, 800)).save(dest)


def test_photo_status_marks_skipped_photos_with_a_reason(tmp_path):
    job, _, _, deps = make(tmp_path, image_count=10, recon_kwargs={"registered": 8}, s3_cls=OddSizeS3)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.photo_status["0003.jpg"] == "skipped:different resolution"
    assert job.photo_status["0004.jpg"] == "skipped:unreadable"
    assert job.photo_status["0001.jpg"] == "registered"
    assert len(job.photo_status) == 10


def test_resume_writes_photo_status_from_the_sfm_checkpoint(tmp_path):
    job, _, recons, deps = make(tmp_path, status="processing", image_count=10)
    w = work_dir(deps, job)
    Checkpoints(w).done("sfm", sparse=str(w / "sparse" / "0"), registered_images=10,
                        registered_names=[f"{i:04d}.jpg" for i in range(1, 10)])
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert "sfm" not in recons[0].calls
    assert job.photo_status["0009.jpg"] == "registered" and job.photo_status["0010.jpg"] == "unregistered"


# ── remove_background: masks in the dense stage, overlays under masks/ ────────
def test_remove_background_masks_dense_and_uploads_overlays_under_masks_prefix(tmp_path):
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons[0].dense_masks is True
    mask_keys = sorted(k for k, _, _ in s3.uploaded if "/masks/" in k)
    assert mask_keys == [f"photogrammetry/{USER}/{job.id}/masks/{i:04d}.jpg" for i in range(1, 7)]
    assert all(ct == "image/jpeg" for k, ct, _ in s3.uploaded if "/masks/" in k)
    assert job.status == "complete"


def test_remove_background_off_masks_nothing(tmp_path):
    job, s3, recons, deps = make(tmp_path, image_count=6)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert recons[0].dense_masks is False
    assert not any("/masks/" in k for k, _, _ in s3.uploaded)


def test_unmasked_photos_become_one_warning(tmp_path):
    from pipeline.masks import MaskReport
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True,
                                 recon_kwargs={"mask_report": MaskReport(masked=4, unmasked=["0002.jpg", "0005.jpg"])})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.warnings == ["Background could not be separated on 2 photos; they were used unmasked"]
    assert job.status == "complete"


def test_dense_checkpoint_records_mask_counts(tmp_path):
    from pipeline.masks import MaskReport
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True,
                                 recon_kwargs={"mask_report": MaskReport(masked=5, unmasked=["0003.jpg"]),
                                               "interrupt_at": "mesh"})
    with pytest.raises(Interrupted):
        process_photogrammetry_job({"job_id": str(job.id)}, deps)
    done = Checkpoints(deps.work_root / str(job.id)).completed("dense")
    assert done["masked"] == 5 and done["unmasked"] == ["0003.jpg"]
    assert job.warnings == ["Background could not be separated on 1 photo; it was used unmasked"]
