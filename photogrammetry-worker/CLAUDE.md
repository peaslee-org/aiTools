# photogrammetry-worker

Standalone Python worker: a run-to-completion ECS task on the shared `gpu-<env>` capacity
provider, launched per job by the API's `RunTask` and exiting itself when idle (lifecycle lives in
`../gpu-worker`). No HTTP server. Takes a confirmed scan's photos from S3, runs COLMAP → OpenMVS →
texturing, and writes `output/mesh.glb` + `output/preview.png` back to S3, walking the job row
through `processing/sfm → dense → mesh → texture → complete`. Live in production since
2026-08-28; the robustness batch (resumable stages, mesh budget, no OOM cycling, photo
normalisation, per-photo status) since 2026-08-29; background removal for turntable scans
(`remove_background`: u2netp masks in the dense stage) since 2026-09-06.

## Key Commands

```bash
cd photogrammetry-worker && uv sync --extra dev
uv run pytest -q                    # 149 tests (2026-09-04); no AWS, DB, COLMAP or OpenMVS needed
MASK_MODEL_PATH=~/.u2net/u2netp.onnx uv run pytest -q tests/test_masks.py   # also runs the real-model test when the ONNX file is present

# Build the image (context is the repo root; ~10 GB of layers — CI does this on push). Python packages
# are pinned by constraints.txt (pip freeze of the acceptance-tested image); tests/test_constraints.py guards it.
docker build -f photogrammetry-worker/Dockerfile -t photogrammetry-worker:dev .
docker run --env-file .env photogrammetry-worker:dev
```

### Smoke tests

**Production smoke** (after any worker deploy): start the *Sample* scan (22 photos, ≈100 s on an
A10G/T4 once the worker is up) and re-run the 51-photo set — expect ≈680 k faces → refine skipped
→ **no decimation** (fits the 1 M budget since 2026-08-31; no "Mesh simplified…" warning), a
meshopt-packed `mesh.glb` (`extensionsRequired` lists `EXT_meshopt_compression`), zero
`CAMERA_SINGLE_DIM_ERROR` lines, one attempt. Watch with `scripts/deploy/gpu-status.sh` and
`aws logs tail /ecs/photogrammetry-prod-worker --follow`.

**CPU smoke** (no GPU; slow, tens of minutes) proves the tool chain end to end on the committed
sample (`chat-api/app/assets/photogrammetry/images/`):

```bash
mkdir -p /tmp/pgsmoke/work /tmp/pgsmoke/images && cp chat-api/app/assets/photogrammetry/images/*.jpg /tmp/pgsmoke/images/
docker run --rm -i -e COLMAP_USE_GPU=0 -e LD_LIBRARY_PATH=/opt/cuda-stubs -v /tmp/pgsmoke:/tmp/pgsmoke photogrammetry-worker:dev python - <<'PY'
import threading, time
from pathlib import Path
from pipeline.reconstruct import Reconstruction
from pipeline.runner import Runner
from pipeline.export import obj_to_glb, make_preview
work, images = Path("/tmp/pgsmoke/work"), Path("/tmp/pgsmoke/images")
r = Reconstruction(Runner(time.monotonic() + 7200, threading.Event()), work, use_gpu=False)
m = r.sfm(images); print("registered", m.registered_images, sorted(m.registered_names)[:3])
d = r.dense(images, m); ply, faces = r.reconstruct_mesh(d); obj = r.texture(d, ply)
print(obj_to_glb(obj, work / "mesh.glb").stat().st_size, make_preview(sorted(images.iterdir())[0], work / "preview.png"))
PY
```

Expected: `registered N` with N ≥ 14 (60 % of 22), a non-trivial `mesh.glb`, and `preview.png`.
The OpenMVS seam-leveling bug reproduces the same way (recipe in `docs/TODO.md`).

**Background-removal smoke** (after a worker deploy that touches `pipeline/masks.py` or the model):
upload the 76-frame turntable cat set (`~/Projects/Amigurumi/scans/cat_20260901-174153/`) with
**Remove background** ticked. Expect (measured 2026-09-06, job `7130f4af`, task-def `:24`): all 76
registered, masks 46 s (0.6 s/photo on the g4dn vCPU), `DensifyPointCloud` **47 s** → 547 k points,
ReconstructMesh 855 k faces (refine skipped), TextureMesh 6 min 22 s, ≈19.5 min claim → complete
of which `exhaustive_matcher` is 8 min (`docs/TODO.md`: sequential matching). A mesh with no
backdrop plane, mask previews behind the Photos pane's **Show masks** toggle, and no "Background
could not be separated…" warning.

## Environment Variables

| Variable | Default | Required |
|---|---|---|
| `DATABASE_URL` | — | yes (`postgresql+asyncpg://` or `+psycopg2://`; normalised to psycopg2) |
| `AUDIO_BUCKET_NAME` | — | yes |
| `PHOTOGRAMMETRY_SQS_QUEUE_URL` | — | yes |
| `AWS_REGION` | `us-east-1` | no |
| `IDLE_EXIT_SECONDS` | `900` | no — must match the API's `GPU_IDLE_EXIT_SECONDS` |
| `MAX_LIFETIME_SECONDS` | `10800` | no — must match `GPU_MAX_LIFETIME_SECONDS` |
| `PHOTOGRAMMETRY_JOB_TIMEOUT_SECONDS` | `3600` | no — `JobTimeout` fails the row |
| `SQS_VISIBILITY_TIMEOUT` | `600` | no — extended every `SQS_VISIBILITY_EXTENSION_INTERVAL` (300) while a job runs |
| `WORK_DIR` | `/tmp/pg` | no — in prod a host-path volume (`/var/lib/photogrammetry`) so scratch survives a container restart |
| `COLMAP_USE_GPU` | `1` | no — `0` runs COLMAP SIFT/matching and OpenMVS (`--cuda-device -2`) on CPU. Off a GPU host also set `LD_LIBRARY_PATH=/opt/cuda-stubs`: OpenMVS binaries need `libcuda.so.1` just to load |
| `MASK_MODEL_PATH` | `/opt/models/u2netp.onnx` | no — u2netp ONNX (4.5 MB, baked into the image) for `remove_background`; CPU inference via onnxruntime, 2 threads |
| `TEXTURE_MAX_SIZE` | `4096` | no — pixel budget (this²) per atlas embedded in the GLB after cropping to its used UV box (JPEG q85); a thin strip keeps full resolution, so an edge can still be up to 8192 (OpenMVS's atlas size — three.js downscales above a device's `MAX_TEXTURE_SIZE` with a console warning) |

## Pipeline (as built)

| Step | Row `stage` | What happens | Rule |
|---|---|---|---|
| load | — | `get` the row; if status ∉ {`queued`, `processing`} → ack and return, remove any scratch; first claim stamps `processing_started_at` (billable GPU clock, kept across resumes) | idempotent on redelivery |
| attempts | — | `ApproximateReceiveCount` ≥ `MAX_ATTEMPTS` (5 = the queue's `maxReceiveCount`), or a `stage.started` marker without its `.done` (the previous attempt died mid-stage) → row `failed` ("did not finish after 5 attempts…") | **no OOM cycling**: a stage that crashed is never retried |
| fetch | `sfm` (status → `processing`) | list `input_prefix` (direct children only), download; fail if fewer than `image_count` objects | transient S3 errors (`TRANSIENT_S3_CODES`, connection errors) leave the row `processing` and re-raise (redelivery resumes); permanent ones (`AccessDenied`, `NoSuchKey`) fail the row |
| photos | `sfm` | `pipeline/photos.py`: EXIF orientation applied and stripped (so COLMAP sees upright pixels), unreadable files skipped with a warning, photos whose pixel size differs from the majority skipped (`CAMERA_SINGLE_DIM_ERROR` otherwise); fail if fewer than `MIN_IMAGES` (5) usable | warnings → `job.warnings` |
| SfM | `sfm` | `colmap feature_extractor` → `exhaustive_matcher` → `mapper`; the sub-model with the most registered images wins; `photo_status` written per input (`registered` / `unregistered` / `skipped:<why>`, names read from `images.bin`) **before** the gate | **fail if registered < 60 % of usable** ("Only N of M photos could be matched — add overlap and try again") |
| dense | `dense` | `image_undistorter` → **if `job.remove_background`**: `pipeline/masks.py` runs u2netp (CPU) over the *undistorted* images → `dense/masks/<stem>.mask.png` + 640 px overlay previews in `work/overlays/` → `InterfaceCOLMAP` → `DensifyPointCloud --resolution-level 2` (+ `--mask-path dense/masks --ignore-mask-label 0` when masked); after `dense.done` the overlays are uploaded to `photogrammetry/<user>/<job>/masks/<name>.jpg` | `--resolution-level 2` fixed for the 16 GB T4. **A mask never fails a job**: coverage outside 1–90 % → all-255 mask for that photo + one warning "Background could not be separated on N photos; they were used unmasked"; `dense.done` carries `masked`/`unmasked` |
| mesh | `mesh` | `ReconstructMesh`; then `RefineMesh` **only if** images ≤ `REFINE_MAX_IMAGES` (100) **and** faces ≤ `REFINE_MAX_FACES` (400 k) | refine roughly doubles faces at ~16 GB virtual on 675 k — that OOM-cycled on 2026-08-28 |
| texture | `texture` | `TextureMesh --decimate FACE_BUDGET/faces` when faces > `FACE_BUDGET` (1 M since 2026-08-31 — meshopt keeps the GLB small), warning "Mesh simplified from N to about 1,000,000 faces to fit the viewer"; `--global-seam-leveling 1 --local-seam-leveling 1` (v2.4.0's sampler regression that blackened leveled faces is patched in the image — `openmvs-v2.4.0-seam-leveling.patch`, upstream `eeedab7`) | |
| export/publish | `publish` | OBJ → GLB **per material** via trimesh (no atlas re-pack, so multi-material meshes are correct); each atlas is cropped to the box its UVs use, capped at `TEXTURE_MAX_SIZE`² pixels and embedded as JPEG q85 (`pipeline/export.py::shrink_atlas`); corners sharing position + UV are welded (`merge_vertices`, OpenMVS writes one `vt` per corner — unwelded geometry, not the atlases, was the bulk of the 51-photo 45 MB GLB), rotated into glTF's y-up; the GLB is then packed with `gltfpack -cc` (`pipeline/export.py::pack_glb` — KHR_mesh_quantization + EXT_meshopt_compression, ~4× on the sample, textures pass through; on gltfpack failure the raw GLB ships with a "Mesh compression failed" warning instead of failing the job; decoded in the viewer by model-viewer's meshopt decoder, bundled in chat-vue); `preview.png` = first input photo at 640 px; upload `output/mesh.glb` + `output/preview.png`; row → `complete` with keys and `completed_at` | |

Each stage writes `<stage>.done` (atomic, JSON payload — e.g. the sfm marker carries `sparse`,
`registered_images`, `registered_names`) under `WORK_DIR/<job_id>/`; a redelivered job resumes at
the first incomplete stage. `main.py` sweeps job dirs older than 24 h at start. Scratch is removed
on success and on deterministic failure; kept on transient failure/interruption for the resume.

## Failure mapping

| Event | Row | SQS |
|---|---|---|
| `StageError`, `JobTimeout`, any other exception in a stage | `failed`, `error_message` = the rule's text or the tool's first stderr line (≤ 1000 chars); `photo_status` and `warnings` kept | acked — a deterministic failure must not retry on a GPU box |
| 5th receive, or a `stage.started` marker with no `.done` | `failed` ("did not finish after 5 attempts (interrupted or out of memory)…") | acked |
| Spot interruption (`gpu_worker.sqs.Interrupted`) | back to `queued`, `stage = NULL` | released with `VisibilityTimeout=0` by `SpotWatcher`; next worker resumes from the markers |
| Transient S3 (`ClientError` with a code in `TRANSIENT_S3_CODES` — SlowDown, Throttling*, RequestTimeout, InternalError, ServiceUnavailable — or any `BotoCoreError`) | stays `processing` | not acked; redelivered after the visibility timeout |
| Permanent S3 (`AccessDenied`, `NoSuchKey`, `NoSuchBucket`, …) | `failed`, `error_message` = the boto message with the code | acked — retrying would only spin to the DLQ with the row stuck `processing` |
| Worker dies mid-job (OOM, crash) | stays `processing` | redelivered; the next attempt sees the `stage.started` marker and fails the row |
| Admin `immediate` release | `queued` | released; the runner aborts the tool process |

## File map

| Path | Role |
|---|---|
| `main.py` | Entry: settings, deps, stale-scratch sweep, `gpu_worker.sqs.run_sqs_worker` with `HANDLERS = {"photogrammetry_job": …}`; passes the receive count to the handler |
| `handlers/photogrammetry.py` | The job: attempts gate, fetch, photos, stages with checkpoints, warnings, photo status, publish, failure mapping |
| `pipeline/photos.py` | Orientation normalisation + usable/skipped/unreadable report |
| `pipeline/colmap.py` | `sparse_reconstruct` (best sub-model, `registered_image_names` from `images.bin`/`images.txt`), `undistort` |
| `pipeline/openmvs.py` | Interface/Densify/Reconstruct/Refine/Texture wrappers; parses face counts from tool output |
| `pipeline/reconstruct.py` | `Reconstruction`: sfm / dense(masks) / reconstruct_mesh / refine_mesh / texture(decimate); loads the mask model once, lazily |
| `pipeline/masks.py` | `load_model` (onnxruntime CPU), `predict` (mirrors rembg's U²-Net pre/post-processing — the spike-validated path), `postprocess` (largest component, holes filled, 0.5 % margin), `sanity`, `overlay`, `make_masks` → `MaskReport` |
| `pipeline/export.py` | `obj_to_glb` (per-material, y-up), `make_preview` |
| `pipeline/checkpoints.py` | Stage markers, crash detection, `sweep_stale` |
| `pipeline/runner.py` | Subprocess runner with the job deadline and the abort event |
| `models.py` | `PhotogrammetryJob` (duplicated from chat-api on purpose: `warnings`, `photo_status`, `remove_background`, keys, stage) |

## Deployment

Push to `main` (changes under `photogrammetry-worker/` or `gpu-worker/`) runs
`.github/workflows/photogrammetry-worker.yml` via `deploy.yml` after the API: buildx build (no
provenance/SBOM manifests — the ECR keep-last-2 rule counts them), ECR push, new
`photogrammetry-prod-worker` task-definition revision. The API's `RunTask` uses the latest revision
on the next launch. The image is also baked into the GPU AMI (`scripts/deploy/build-gpu-ami.sh`);
until a re-bake every cold start pulls the changed layers (~5 min). Terraform
(`infra/modules/photogrammetry`) owns the queue (`maxReceiveCount` 5), the task-definition shape
(host-path scratch volume) and IAM; its `photogrammetry_image_tag` must be set to the deployed SHA
before an apply or the replaced revision points at `latest`. Runbook: `docs/runbooks/deploy.md`.
