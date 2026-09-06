# Photogrammetry: background removal for turntable scans

**Date:** 2026-09-04. **Status:** implemented 2026-09-04 (plan `docs/superpowers/plans/2026-09-04-photogrammetry-background-removal.md`), merged to `main` and deployed 2026-09-06 (Deploy run 34040475702: migration `w3x4y5z6a7b8`, worker task-def `:24` on image `721860c`). Production smoke on the 76-frame cat set passed: all 76 masked, dense stage 47 s, no backdrop in the mesh, masks visible in the Photos pane. Deviations from this spec are listed at the top of the plan (no demo fixture; masks appear at job end or on reopening the Photos pane; the toggle keys off `mask_url`).

## Why

The amigurumi rig is a fixed Raspberry Pi camera (ov5647, 2592×1944) pointed at a turntable. The
object sits on a black-dots-on-white backdrop that rotates with it and fills the frame. The dots
are what let COLMAP register 76 views of a near-textureless crochet toy, so they must stay in the
photos for SfM. Once the cameras are posed, the backdrop is pure cost: every pixel of it goes
through depth-map estimation, becomes points, faces and texture, and then needs cutting out of
the model.

Goal: an option that keeps the backdrop for matching and removes it before the dense stage, so
the finished model is the object alone and the GPU spends its time only on object pixels.

## What the spike found (2026-09-04, throwaway)

Run on the 76-frame `cat_20260901-174153` set on the fitlet (Celeron J3455, CPU only):

| Method | Result | CPU s/photo (fitlet) |
|---|---|---|
| HSV colour key on the dot pattern | Poor: anti-aliased dot edges and the paper tint read as object | 1 |
| u2net (176 MB) | 3 of 76 frames selected the backdrop instead of the cat; dropped an ear on one, cropped the shadowed body on another | 5.5 |
| **u2netp (4.5 MB)** | **Clean on all 76 frames: coverage 24–46 %, no jumps between consecutive frames, stray blobs ≤ 0.2 % of the object** | 2.4 |
| isnet-general-use | Missed the cat entirely on 1 of 4 frames | 15 |
| BiRefNet lite / general | Untestable on an 8 GB box (OOM-killed at ~3 GB RSS) | — |

OpenMVS v2.4.0 (the version in the worker image) confirmed from source:
`DensifyPointCloud --mask-path <dir> --ignore-mask-label 0` reads `<image stem>.mask.png`
(`Util::getFileName` strips the extension), resizes it with nearest-neighbour to the depth-map
size, and skips every pixel whose value equals the ignore label. COLMAP masks are not needed for
this rig: nothing static is in frame.

Not a mask problem, out of scope: the wire stand under the object and its shadow are segmented as
object in several frames and will appear in the mesh.

## Decisions

1. **Per-scan checkbox, off by default.** The sample scan, the mock service and every existing
   flow are unchanged. A saved rig profile can grow out of the flag later.
2. **Worker-side, CPU, u2netp.** No API image growth, no new upload path, no GPU inference (no
   cuDNN in the image, no contention with COLMAP). ~1 s/photo on a g4dn vCPU is under 10 % of a
   76-photo job and small next to the dense-stage saving.
3. **Mask the undistorted images, sequentially, inside the dense stage.** COLMAP's
   `image_undistorter` rewrites every photo before OpenMVS reads it; a mask of the original is
   misaligned near the edges of a wide lens. Masking the undistorted JPEGs is exact and keeps the
   checkpoint model sequential. Rejected: masking originals in a thread during SfM and warping
   the masks (own SIMPLE_RADIAL + crop/scale maths, a parallel stage), and ignoring the
   misalignment (wrong in general, hard to diagnose).
4. **A bad mask never fails a job.** Per-photo sanity check; a photo that fails runs unmasked and
   the job gets one warning.
5. **Masks are visible, retries are manual.** The worker uploads an overlay preview per photo; the
   Photos pane gets a "Show masks" toggle. To retry, the user starts a new scan with the box
   unticked. No re-run endpoint.

## Section 1: worker

### `pipeline/masks.py`

- `load_model(path) -> Session`: onnxruntime CPU session for u2netp with `intra_op_num_threads=2`
  so COLMAP keeps its cores. The model path comes from `Settings.MASK_MODEL_PATH`
  (default `/opt/models/u2netp.onnx`). Loaded once per worker process, on the first job that
  asks for masks; a job without the flag never touches onnxruntime.
- `predict(session, image_bgr) -> float32 (H, W)`: resize to 320×320, RGB, ImageNet
  mean/std normalisation as U²-Net expects, run, take the first output, resize the probability
  map back to the image size (bilinear).
- `postprocess(prob, margin_px) -> uint8 (H, W)`: threshold at 0.5; keep the largest connected
  component; fill holes; dilate by `margin_px`. Values are 0 (background) and 255 (object).
- `margin_px = max(2, round(0.005 * max(H, W)))` — half a percent of the longer side (13 px at
  2592).
- `sanity(mask) -> bool`: coverage between `MASK_MIN_COVER` (1 %) and `MASK_MAX_COVER` (90 %).
- `make_masks(session, images_dir, masks_dir, overlays_dir) -> MaskReport`: for each image
  in `images_dir` (the undistorted `dense/images/`), write `masks_dir/<stem>.mask.png`. A photo
  that fails `sanity` gets an all-255 mask (unmasked) and is listed in `MaskReport.unmasked`.
  Also writes `overlays_dir/<stem>.jpg`: the image at 640 px on the long side with the
  background darkened to 25 % and the object outlined — the same rendering as the spike's
  comparison images.
- `MaskReport(masked: int, unmasked: list[str])`; `warnings()` returns
  `["Background could not be separated on N photos; they were used unmasked"]` when `unmasked`
  is non-empty, else `[]`.

Inference is behind `predict` so tests stub it; nothing else in the module needs the model.

### Dense stage (`pipeline/reconstruct.py`, `pipeline/openmvs.py`, handler)

`Reconstruction.dense(images, model, masks: bool)` becomes:

1. `colmap.undistort` (unchanged) → `dense/`
2. if `masks`: `masks.make_masks(session, dense / "images", dense / "masks", work / "overlays")`
3. `openmvs.interface` (unchanged)
4. `openmvs.densify(..., mask_path=dense / "masks" if masks else None)` — adds
   `--mask-path <dir> --ignore-mask-label 0` when set.

The handler reads `job.remove_background` with the other row fields at claim time and passes it
to `dense`. The stage name stays `dense` for checkpoints and the stage strip; the `dense.done`
payload gains `masked` and `unmasked` counts. After `dense.done` the handler uploads
`work/overlays/*.jpg` to `photogrammetry/<user>/<job>/masks/<name>.jpg`, where `<name>` is the
input photo's filename (`0001.jpg` — the API renames uploads on create, and the undistorter keeps
names). Upload failures are transient-S3 like any other (`_is_transient_s3`), and the overlays
directory is part of the job scratch, so a resume re-uploads. `MaskReport.warnings()` goes to the
job's warnings.

Resume semantics are unchanged: masks live inside the dense stage, so a crash during masking is
"crashed during the dense stage" and fails the job under the no-cycling rule, as any other dense
crash does today.

### Image and dependencies

- `pip install` adds `onnxruntime` (CPU wheel) and `opencv-python-headless`, both pinned in
  `constraints.txt` (regenerate with `pip freeze` from the built image, as the header says;
  `tests/test_constraints.py` guards it).
- Dockerfile downloads `u2netp.onnx` from the rembg release assets
  (`https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx`, Apache 2.0)
  into `/opt/models/` and checks its SHA-256 (record the hash in the Dockerfile). ~4.5 MB;
  no new AMI bake needed.
- `models.py` (the worker's copy of `PhotogrammetryJob`) gains `remove_background`.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `MASK_MODEL_PATH` | `/opt/models/u2netp.onnx` | ONNX model file |

Thresholds and the margin are module constants, not settings.

### Tests (no model, no AWS)

- `postprocess`: a probability map with two blobs and a hole → one filled blob, dilated by the
  margin; the output is 0/255 only.
- `sanity` and the fallback: a photo with 0 % and one with 95 % coverage produce an all-255 mask
  and appear in `unmasked`; `warnings()` wording.
- Naming: `frame_0001.jpg` → `frame_0001.mask.png`; overlays keep the photo's name with `.jpg`.
- `openmvs.densify` command line with and without `mask_path`.
- Handler: with `remove_background=True`, `dense` is called with masks, overlays are uploaded
  under `masks/`, the warning appears; with `False`, no masks directory and no uploads.
- Integration (skipped unless `MASK_MODEL_PATH` exists): a synthetic image (a bright blob on a
  dotted background) yields one component covering the blob.

## Section 2: API and data

- Migration: `remove_background BOOLEAN NOT NULL DEFAULT false` on `photogrammetry_jobs`. Runs
  at container start as every migration does.
- `JobCreateRequest.remove_background: bool = False`; `PhotogrammetryService.create_job` writes
  it to the row. `create_sample_job` and `LocalPhotogrammetryService` leave it false. The SQS
  message body is unchanged (the worker reads the row).
- `JobStatusResponse.remove_background: bool` (default false) so the detail view knows whether
  to offer the mask toggle.
- `PhotoItem.mask_url: Optional[str]`: presigned GET of `<job>/masks/<name>.jpg` when the object
  exists. `_photos` already lists the thumbs prefix; it lists the masks prefix the same way
  (`_masks_prefix_for`, sibling of `input/`, mirroring `_thumbs_prefix_for`) and presigns
  matches. Sample photos never have masks.
- Delete removes the job prefix, so masks go with it; the 30-day lifecycle on `photogrammetry/`
  already covers them. The worker task role's existing put on the bucket covers the upload. No
  IAM or Terraform change.
- Tests: request default and round trip to the row; status echoes the flag; `mask_url` set only
  for photos with a masks object; the mock service ignores the flag.

## Section 3: Vue

- `NewScanForm`: a "Remove background" checkbox under the name field, unchecked by default,
  hidden in sample mode, with help text "For turntable scans. Keeps only the object in the
  finished model." `store.submitScan(name, files, removeBackground)` →
  `api.createJob(name, filenames, removeBackground)` → request body `remove_background`.
- `PhotogrammetryJob` type gains `remove_background`; `PhotoItem` type gains `mask_url`.
- Scan detail: when `job.remove_background` and at least one photo has `mask_url`, the Photos
  pane shows a "Show masks" toggle. On, each tile with a `mask_url` shows the overlay instead of
  the thumbnail (tiles without one keep the thumbnail). Masks exist only after the dense stage
  has run, so the toggle appears mid-job once the worker passes it.
- Demo view fixtures: one job with `remove_background: true` and mask URLs on its photos.
- Tests: the checkbox sets the flag on submit and is absent in sample mode; the toggle renders
  only when a mask URL exists and swaps the tile's image source.

## Docs

- `photogrammetry-worker/CLAUDE.md`: pipeline table (dense row), file map (`pipeline/masks.py`),
  env var table (`MASK_MODEL_PATH`), and a line in the smoke-test section: re-run the cat set with
  the box ticked and expect a mesh with no backdrop and masks in the Photos pane.
- `chat-api/CLAUDE.md`: the new column and `mask_url`.
- `docs/user-guide.md`: the checkbox and the mask toggle.

## Follow-ups (not in this spec)

- Saved rig profile (masking on, camera settings from the rig's `session.json`, a static-region
  SfM mask for rigs where the room is in frame).
- Re-running a job with the flag flipped from the same uploaded photos.
- GPU inference or a stronger model (BiRefNet) if u2netp proves too weak on other objects;
  evaluate on the GPU host.
- Geometry clean-up of the stand and its shadow (ROI or plane cut after meshing).
