# Photogrammetry Background Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-scan "Remove background" option that keeps the turntable's dotted backdrop for SfM and masks it out of the dense stage, so the model is the object alone and the GPU only spends time on object pixels.

**Architecture:** The worker runs the u2netp salient-object model on CPU over the *undistorted* images inside the dense stage and hands the masks to OpenMVS `DensifyPointCloud --mask-path … --ignore-mask-label 0`. It uploads a small overlay preview per photo to the job's `masks/` prefix. The API stores one boolean on the job row and presigns the overlays alongside thumbnails; the Vue form has a checkbox and the photo grid a "Show masks" toggle.

**Tech Stack:** Python 3.12, onnxruntime (CPU), opencv-python-headless, numpy, Pillow (worker); FastAPI, SQLAlchemy, Alembic, Pydantic (API); Vue 3, TypeScript, Pinia, Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md`

## Global Constraints

- Mask files: `<image stem>.mask.png`, single-channel 8-bit, `0` = background, `255` = object (OpenMVS v2.4.0 reads `Util::getFileName(image) + ".mask.png"` under `--mask-path`; `--ignore-mask-label 0`).
- Model: `u2netp.onnx` from `https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx`, SHA-256 `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`, 4 574 861 bytes, Apache 2.0. Image path `/opt/models/u2netp.onnx`; env `MASK_MODEL_PATH`.
- Pre-processing must match what the spike validated (rembg's U²-Net session): resize to 320×320 with LANCZOS, divide by the image's max pixel value, subtract mean `(0.485, 0.456, 0.406)`, divide by std `(0.229, 0.224, 0.225)`, NCHW float32; output `[0][:, 0]` min-max scaled to 0..1, resized back with LANCZOS, thresholded at 0.5.
- Post-processing: threshold → largest connected component → fill holes → dilate by `max(2, round(0.005 * max(H, W)))` px.
- Sanity: coverage in `[0.01, 0.90]`; otherwise an all-255 mask and the photo is listed as unmasked. A mask never fails a job.
- Overlay previews: 640 px on the long side, JPEG q80, background darkened to 25 %, object outlined; key `photogrammetry/<user>/<job>/masks/<input filename>` (same basename as the input photo, e.g. `0001.jpg`).
- Worker pins: every package the Dockerfile installs must be an exact pin in `constraints.txt` (`tests/test_constraints.py`).
- Worker tests run with `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest -q` (the editable gpu-worker install fails on old setuptools; see memory).
- API tests: `cd chat-api && uv run pytest -q`. Vue tests: `cd chat-vue && npm test` (two auth specs fail on a checkout with `VITE_DEV_AUTH_BYPASS=true` in `.env.local`; that is env noise, not a regression).
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BdTFBWCCcyN9Xevug1rSxm
  ```
- Never `git add .claude/` (untracked local config).

## Deviations from the spec (decided while planning)

- The demo view renders only a mesh viewer, no photo grid, so there is no demo fixture to add. Dropped.
- The Photos pane refetches the listing when the job reaches a terminal status (existing behaviour), so mask overlays appear when the job completes or fails, or when the pane is reopened, not mid-stage. No new polling.
- The "Show masks" toggle lives inside `PhotoGrid` and is driven by `mask_url` presence alone; `job.remove_background` is still echoed by the API and shown as a small "background removed" note in the scan header.

---

## File map

| Path | Change |
|---|---|
| `photogrammetry-worker/pipeline/masks.py` | **Create.** Model session, prediction, post-processing, sanity, overlays, `make_masks`, `MaskReport`. |
| `photogrammetry-worker/pipeline/openmvs.py` | `densify(..., mask_path=None)` adds `--mask-path`/`--ignore-mask-label`. |
| `photogrammetry-worker/pipeline/reconstruct.py` | `Reconstruction(..., mask_model_path=None)`; `dense(images, model, masks=False)` runs `make_masks` between undistort and interface. |
| `photogrammetry-worker/handlers/photogrammetry.py` | Read `job.remove_background`; pass to `dense`; upload overlays; warning; `dense.done` payload. |
| `photogrammetry-worker/models.py` | `remove_background` column. |
| `photogrammetry-worker/config.py`, `main.py` | `MASK_MODEL_PATH`; pass it to `Reconstruction`. |
| `photogrammetry-worker/Dockerfile`, `constraints.txt`, `pyproject.toml` | onnxruntime + opencv-python-headless; model download with checksum. |
| `photogrammetry-worker/tests/test_masks.py` | **Create.** |
| `photogrammetry-worker/tests/test_openmvs.py`, `test_reconstruct.py`, `test_handler.py` | Extend. |
| `photogrammetry-worker/CLAUDE.md` | Pipeline table, file map, env table, smoke test. |
| `chat-api/app/db/migrations/versions/w3x4y5z6a7b8_add_photogrammetry_remove_background.py` | **Create.** |
| `chat-api/app/models/photogrammetry.py` | Column. |
| `chat-api/app/schemas/photogrammetry.py` | `JobCreateRequest.remove_background`, `JobStatusResponse.remove_background`, `PhotoItem.mask_url`. |
| `chat-api/app/repositories/photogrammetry.py` | `create_job(..., remove_background=False)`. |
| `chat-api/app/services/photogrammetry_service.py` | Pass the flag; `_masks_prefix_for`; `mask_url` in `_photos`; `_to_response`. |
| `chat-api/tests/unit/test_photogrammetry_remove_background_migration.py` | **Create.** |
| `chat-api/tests/unit/test_photogrammetry_schemas.py`, `test_photogrammetry_model.py`, `repositories/test_photogrammetry_repository.py`, `services/test_photogrammetry_service.py` | Extend. |
| `chat-api/CLAUDE.md` | Model line, photos endpoint note. |
| `chat-vue/src/types/index.ts` | `remove_background`, `mask_url`. |
| `chat-vue/src/lib/photogrammetryApi.ts` | `createJob(name, filenames, removeBackground)`. |
| `chat-vue/src/stores/photogrammetry.ts` | `submitScan(name, files, removeBackground)`. |
| `chat-vue/src/components/photogrammetry/NewScanForm.vue` | Checkbox. |
| `chat-vue/src/components/photogrammetry/PhotoGrid.vue` | "Show masks" toggle. |
| `chat-vue/src/components/photogrammetry/ScanDetailView.vue` | "background removed" note. |
| `chat-vue/src/components/photogrammetry/__tests__/NewScanForm.spec.ts` | **Create.** |
| `chat-vue/src/components/photogrammetry/__tests__/PhotoGrid.spec.ts`, `src/lib/__tests__/photogrammetryApi.spec.ts` | Extend. |
| `docs/user-guide.md` | Checkbox and toggle. |

---

### Task 1: Mask post-processing, sanity and report (`pipeline/masks.py`, pure numpy/cv2)

**Files:**
- Create: `photogrammetry-worker/pipeline/masks.py`
- Create: `photogrammetry-worker/tests/test_masks.py`
- Modify: `photogrammetry-worker/pyproject.toml` (add `opencv-python-headless>=4.10`, `onnxruntime>=1.20` to `dependencies`)

**Interfaces:**
- Produces: `postprocess(prob: np.ndarray, margin_px: int) -> np.ndarray` (uint8 0/255), `margin_for(shape) -> int`, `sanity(mask) -> bool`, `MaskReport(masked: int, unmasked: list[str])` with `.warnings() -> list[str]`, constants `MASK_MIN_COVER = 0.01`, `MASK_MAX_COVER = 0.90`, `MASK_SUFFIX = ".mask.png"`.

- [ ] **Step 1: Add the dependencies and sync**

In `photogrammetry-worker/pyproject.toml`, extend `dependencies`:

```toml
    "numpy>=1.26",
    "opencv-python-headless>=4.10",   # masks: connected components, hole fill, dilation, overlay drawing
    "onnxruntime>=1.20",              # masks: u2netp inference on CPU
]
```

Run: `cd photogrammetry-worker && uv sync --extra dev`
Expected: resolves and installs both packages (uv.lock updated).

- [ ] **Step 2: Write the failing tests**

Create `photogrammetry-worker/tests/test_masks.py`:

```python
"""Object masks for the dense stage: post-processing, the sanity fallback and naming."""
import numpy as np
import pytest

from pipeline.masks import (
    MASK_MAX_COVER, MASK_MIN_COVER, MASK_SUFFIX, MaskReport, margin_for, postprocess, sanity,
)


def _prob(h=200, w=300):
    p = np.zeros((h, w), np.float32)
    p[40:160, 60:200] = 0.9          # main blob
    p[90:110, 120:140] = 0.1         # a hole inside it (a black fleck, a googly eye)
    p[10:20, 250:280] = 0.8          # a stray speck
    return p


def test_postprocess_keeps_largest_component_fills_holes_and_is_binary():
    m = postprocess(_prob(), margin_px=0)
    assert m.dtype == np.uint8 and set(np.unique(m)) <= {0, 255}
    assert m[100, 130] == 255         # hole filled
    assert m[15, 260] == 0            # speck dropped
    assert m[100, 130] == 255 and m[40, 60] == 255 and m[159, 199] == 255
    assert m[30, 130] == 0            # outside the blob, no margin


def test_postprocess_dilates_by_the_margin():
    m0 = postprocess(_prob(), margin_px=0)
    m5 = postprocess(_prob(), margin_px=5)
    assert m5[35, 130] == 255 and m0[35, 130] == 0       # 5 px above the blob's top edge
    assert m5[30, 130] == 0                               # not 10 px


def test_postprocess_all_background_is_all_zero():
    m = postprocess(np.zeros((50, 50), np.float32), margin_px=3)
    assert not m.any()


def test_margin_is_half_a_percent_of_the_longer_side_at_least_2():
    assert margin_for((1944, 2592)) == 13
    assert margin_for((100, 100)) == 2


def test_sanity_bounds():
    full = np.full((100, 100), 255, np.uint8)
    empty = np.zeros((100, 100), np.uint8)
    half = empty.copy(); half[:50] = 255
    assert sanity(half)
    assert not sanity(empty) and not sanity(full)
    assert MASK_MIN_COVER == 0.01 and MASK_MAX_COVER == 0.90


def test_report_warning_wording():
    assert MaskReport(masked=5, unmasked=[]).warnings() == []
    assert MaskReport(masked=3, unmasked=["0002.jpg", "0004.jpg"]).warnings() == [
        "Background could not be separated on 2 photos; they were used unmasked"
    ]
    assert MaskReport(masked=3, unmasked=["0002.jpg"]).warnings() == [
        "Background could not be separated on 1 photo; it was used unmasked"
    ]


def test_mask_suffix():
    assert MASK_SUFFIX == ".mask.png"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_masks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.masks'`.

- [ ] **Step 4: Write the module (post-processing half)**

Create `photogrammetry-worker/pipeline/masks.py`:

```python
"""Object masks for the dense stage (spec 2026-09-04): u2netp on CPU over the *undistorted* images,
one `<stem>.mask.png` per image for `DensifyPointCloud --mask-path`, plus a 640 px overlay preview
per photo for the Photos pane. A mask never fails a job: a photo whose mask fails the sanity check
runs unmasked and is reported."""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MASK_SUFFIX = ".mask.png"
MASK_MIN_COVER = 0.01
MASK_MAX_COVER = 0.90
THRESHOLD = 0.5
OVERLAY_MAX_SIDE = 640
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)
_NET_SIZE = (320, 320)


@dataclass
class MaskReport:
    masked: int = 0
    unmasked: list[str] = field(default_factory=list)

    def warnings(self) -> list[str]:
        n = len(self.unmasked)
        if n == 0:
            return []
        if n == 1:
            return ["Background could not be separated on 1 photo; it was used unmasked"]
        return [f"Background could not be separated on {n} photos; they were used unmasked"]


def margin_for(shape: tuple[int, ...]) -> int:
    """Half a percent of the longer side, at least 2 px: the safety margin around the object."""
    return max(2, round(0.005 * max(shape[0], shape[1])))


def postprocess(prob: np.ndarray, margin_px: int) -> np.ndarray:
    """Probability map → binary mask: threshold, largest component, holes filled, dilated."""
    m = (prob >= THRESHOLD).astype(np.uint8)
    if not m.any():
        return np.zeros(prob.shape, np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    m = (labels == largest).astype(np.uint8) * 255
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(m)
    cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
    if margin_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))
        filled = cv2.dilate(filled, k)
    return filled


def sanity(mask: np.ndarray) -> bool:
    cover = float((mask > 0).mean())
    return MASK_MIN_COVER <= cover <= MASK_MAX_COVER
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_masks.py -q`
Expected: all 7 pass.

- [ ] **Step 6: Commit**

```bash
git add photogrammetry-worker/pipeline/masks.py photogrammetry-worker/tests/test_masks.py photogrammetry-worker/pyproject.toml photogrammetry-worker/uv.lock
git commit -m "feat(photogrammetry-worker): mask post-processing, sanity check and report"
```

---

### Task 2: Inference, overlays and `make_masks`

**Files:**
- Modify: `photogrammetry-worker/pipeline/masks.py`
- Modify: `photogrammetry-worker/tests/test_masks.py`

**Interfaces:**
- Consumes: Task 1's `postprocess`, `margin_for`, `sanity`, `MaskReport`, `MASK_SUFFIX`.
- Produces: `load_model(path: Path) -> onnxruntime.InferenceSession`, `predict(session, image_bgr: np.ndarray) -> np.ndarray` (float32 H×W in 0..1), `overlay(image_bgr, mask) -> np.ndarray` (BGR, ≤ 640 px long side), `make_masks(session, images_dir: Path, masks_dir: Path, overlays_dir: Path, predict_fn=predict) -> MaskReport`.

- [ ] **Step 1: Write the failing tests**

Append to `photogrammetry-worker/tests/test_masks.py`:

```python
import os
from pathlib import Path

import cv2

from pipeline.masks import OVERLAY_MAX_SIDE, load_model, make_masks, overlay, predict


def _write_photo(path: Path, w=1280, h=800):
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.circle(img, (w // 2, h // 2), 200, (40, 40, 40), -1)
    cv2.imwrite(str(path), img)


def _blob_predict(session, bgr):
    """Stub inference: 1.0 inside a disc in the middle, 0 elsewhere."""
    h, w = bgr.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    return (((xx - w // 2) ** 2 + (yy - h // 2) ** 2) < (min(h, w) // 4) ** 2).astype(np.float32)


def _empty_predict(session, bgr):
    return np.zeros(bgr.shape[:2], np.float32)


def test_make_masks_writes_one_mask_and_overlay_per_image_named_by_stem(tmp_path):
    images, masks, overlays = tmp_path / "images", tmp_path / "masks", tmp_path / "overlays"
    images.mkdir()
    for name in ("0001.jpg", "0002.jpg"):
        _write_photo(images / name)
    report = make_masks(None, images, masks, overlays, predict_fn=_blob_predict)
    assert report.masked == 2 and report.unmasked == []
    assert sorted(p.name for p in masks.iterdir()) == ["0001.mask.png", "0002.mask.png"]
    assert sorted(p.name for p in overlays.iterdir()) == ["0001.jpg", "0002.jpg"]
    m = cv2.imread(str(masks / "0001.mask.png"), cv2.IMREAD_UNCHANGED)
    assert m.shape == (800, 1280) and m.dtype == np.uint8 and set(np.unique(m)) == {0, 255}
    assert m[400, 640] == 255 and m[5, 5] == 0
    o = cv2.imread(str(overlays / "0001.jpg"))
    assert max(o.shape[:2]) == OVERLAY_MAX_SIDE       # 1280 px photo → 640 px overlay


def test_make_masks_falls_back_to_unmasked_when_sanity_fails(tmp_path):
    images, masks, overlays = tmp_path / "images", tmp_path / "masks", tmp_path / "overlays"
    images.mkdir()
    _write_photo(images / "0001.jpg")
    report = make_masks(None, images, masks, overlays, predict_fn=_empty_predict)
    assert report.masked == 0 and report.unmasked == ["0001.jpg"]
    m = cv2.imread(str(masks / "0001.mask.png"), cv2.IMREAD_UNCHANGED)
    assert (m == 255).all()             # all-255: OpenMVS uses every pixel


def test_make_masks_skips_non_image_files(tmp_path):
    images, masks, overlays = tmp_path / "images", tmp_path / "masks", tmp_path / "overlays"
    images.mkdir()
    _write_photo(images / "0001.jpg")
    (images / "notes.txt").write_text("x")
    report = make_masks(None, images, masks, overlays, predict_fn=_blob_predict)
    assert report.masked == 1 and not (masks / "notes.mask.png").exists()


def test_overlay_darkens_background_and_keeps_object():
    img = np.full((100, 200, 3), 200, np.uint8)
    mask = np.zeros((100, 200), np.uint8); mask[20:80, 50:150] = 255
    o = overlay(img, mask)
    assert o.shape[:2] == (100, 200)     # already under 640 px: no resize
    assert o[5, 5].tolist() == [50, 50, 50]
    assert o[50, 100].tolist() == [200, 200, 200]


MODEL = Path(os.environ.get("MASK_MODEL_PATH", "/opt/models/u2netp.onnx"))


@pytest.mark.skipif(not MODEL.is_file(), reason="u2netp.onnx not present")
def test_real_model_segments_a_dark_disc_on_white(tmp_path):
    img = np.full((480, 640, 3), 245, np.uint8)
    cv2.circle(img, (320, 240), 100, (30, 60, 90), -1)
    session = load_model(MODEL)
    prob = predict(session, img)
    assert prob.shape == (480, 640) and 0.0 <= prob.min() and prob.max() <= 1.0
    m = postprocess(prob, margin_for(prob.shape))
    assert m[240, 320] == 255 and m[10, 10] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_masks.py -q`
Expected: FAIL with `ImportError: cannot import name 'OVERLAY_MAX_SIDE'` (or `load_model`).

- [ ] **Step 3: Implement inference, overlays and `make_masks`**

Append to `photogrammetry-worker/pipeline/masks.py`:

```python
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def load_model(path: Path):
    """onnxruntime CPU session for u2netp. Two intra-op threads: COLMAP keeps its cores."""
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def predict(session, image_bgr: np.ndarray) -> np.ndarray:
    """U²-Net forward pass → probability map in 0..1 at the image's size. Pre/post-processing
    mirrors rembg's U2net session, which is what the 2026-09-04 spike validated on the rig."""
    h, w = image_bgr.shape[:2]
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    small = cv2.resize(rgb, _NET_SIZE, interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
    small = small / max(float(small.max()), 1e-6)
    for c in range(3):
        small[:, :, c] = (small[:, :, c] - _MEAN[c]) / _STD[c]
    x = np.expand_dims(small.transpose(2, 0, 1), 0).astype(np.float32)
    name = session.get_inputs()[0].name
    pred = session.run(None, {name: x})[0][:, 0, :, :]
    lo, hi = float(pred.min()), float(pred.max())
    pred = (pred - lo) / max(hi - lo, 1e-6)
    prob = np.squeeze(pred).astype(np.float32)
    return cv2.resize(prob, (w, h), interpolation=cv2.INTER_LANCZOS4).clip(0.0, 1.0)


def overlay(image_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Background at 25 %, object outlined; ≤ OVERLAY_MAX_SIDE on the long side."""
    out = image_bgr.copy()
    bg = mask < 128
    out[bg] = (out[bg] * 0.25).astype(np.uint8)
    contours, _ = cv2.findContours((mask > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = out.shape[:2]
    cv2.drawContours(out, contours, -1, (0, 0, 255), max(2, round(0.003 * max(h, w))))
    scale = OVERLAY_MAX_SIDE / max(h, w)
    if scale < 1.0:
        out = cv2.resize(out, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return out


def make_masks(session, images_dir: Path, masks_dir: Path, overlays_dir: Path, predict_fn=predict) -> MaskReport:
    """One mask + one overlay per image in `images_dir` (the undistorted images). A photo whose
    mask fails `sanity` gets an all-255 mask — OpenMVS then uses every pixel — and is reported."""
    masks_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    report = MaskReport()
    for path in sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES):
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            report.unmasked.append(path.name)
            continue
        prob = predict_fn(session, bgr)
        mask = postprocess(prob, margin_for(prob.shape))
        if not sanity(mask):
            mask = np.full(bgr.shape[:2], 255, np.uint8)
            report.unmasked.append(path.name)
        else:
            report.masked += 1
        cv2.imwrite(str(masks_dir / f"{path.stem}{MASK_SUFFIX}"), mask)
        cv2.imwrite(str(overlays_dir / f"{path.stem}.jpg"), overlay(bgr, mask), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_masks.py -q`
Expected: 11 pass, 1 skipped (the real-model test) unless the model is present. If `~/.u2net/u2netp.onnx` exists on the dev box (it does after the spike), also run `MASK_MODEL_PATH=~/.u2net/u2netp.onnx PYTHONPATH=../gpu-worker uv run pytest tests/test_masks.py -q -k real_model` and expect it to pass.

- [ ] **Step 5: Commit**

```bash
git add photogrammetry-worker/pipeline/masks.py photogrammetry-worker/tests/test_masks.py
git commit -m "feat(photogrammetry-worker): u2netp inference, overlays and make_masks"
```

---

### Task 3: OpenMVS mask flags and the dense stage in `Reconstruction`

**Files:**
- Modify: `photogrammetry-worker/pipeline/openmvs.py` (`densify`)
- Modify: `photogrammetry-worker/pipeline/reconstruct.py`
- Modify: `photogrammetry-worker/tests/test_openmvs.py`, `photogrammetry-worker/tests/test_reconstruct.py`

**Interfaces:**
- Consumes: `pipeline.masks.load_model`, `make_masks`.
- Produces: `openmvs.densify(runner, dense, scene, use_gpu=True, mask_path: Path | None = None)`; `Reconstruction(runner, work, use_gpu, mask_model_path: Path | None = None)`; `Reconstruction.dense(images, model, masks: bool = False) -> Path`; `Reconstruction.mask_report: MaskReport | None` (set by the last `dense` call that masked).

- [ ] **Step 1: Write the failing tests**

Append to `photogrammetry-worker/tests/test_openmvs.py`:

```python
def test_densify_without_mask_path_passes_no_mask_flags(tmp_path):
    r = FakeRunner()
    densify(r, tmp_path, tmp_path / "scene.mvs")
    cmd = r.calls[0][0]
    assert "--mask-path" not in cmd and "--ignore-mask-label" not in cmd


def test_densify_with_mask_path_adds_mask_flags(tmp_path):
    r = FakeRunner()
    densify(r, tmp_path, tmp_path / "scene.mvs", mask_path=tmp_path / "masks")
    cmd = r.calls[0][0]
    assert cmd[cmd.index("--mask-path") + 1] == str(tmp_path / "masks")
    assert cmd[cmd.index("--ignore-mask-label") + 1] == "0"
```

Append to `photogrammetry-worker/tests/test_reconstruct.py`:

```python
import pipeline.reconstruct as recon_mod
from pipeline.colmap import SparseModel
from pipeline.masks import MaskReport


def _stub_masks(monkeypatch, calls):
    def fake_make_masks(session, images_dir, masks_dir, overlays_dir, predict_fn=None):
        calls.append((session, images_dir, masks_dir, overlays_dir))
        masks_dir.mkdir(parents=True, exist_ok=True)
        return MaskReport(masked=3, unmasked=["0002.jpg"])
    monkeypatch.setattr(recon_mod, "make_masks", fake_make_masks)
    monkeypatch.setattr(recon_mod, "load_model", lambda path: f"session:{path}")


def test_dense_without_masks_runs_undistort_interface_densify(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    r = Runner()
    recon = Reconstruction(r, tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    dense = recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5))
    assert dense == tmp_path / "dense"
    assert [c[0] for c in r.cmds] == ["colmap", "InterfaceCOLMAP", "DensifyPointCloud"]
    assert calls == [] and recon.mask_report is None
    assert "--mask-path" not in r.cmds[2]


def test_dense_with_masks_masks_the_undistorted_images_before_densify(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    r = Runner()
    recon = Reconstruction(r, tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    dense = recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5), masks=True)
    assert calls == [(f"session:{tmp_path / 'm.onnx'}", dense / "images", dense / "masks", tmp_path / "overlays")]
    cmd = r.cmds[2]
    assert cmd[0] == "DensifyPointCloud" and cmd[cmd.index("--mask-path") + 1] == str(dense / "masks")
    assert recon.mask_report == MaskReport(masked=3, unmasked=["0002.jpg"])


def test_model_is_loaded_once_per_reconstruction(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    loads = []
    monkeypatch.setattr(recon_mod, "load_model", lambda path: loads.append(path) or "s")
    recon = Reconstruction(Runner(), tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    model = SparseModel(tmp_path / "sparse" / "0", 5)
    recon.dense(tmp_path / "images", model, masks=True)
    recon.dense(tmp_path / "images", model, masks=True)
    assert loads == [tmp_path / "m.onnx"]


def test_masks_without_a_model_path_is_an_error(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    recon = Reconstruction(Runner(), tmp_path, use_gpu=False)
    with pytest.raises(RuntimeError, match="MASK_MODEL_PATH"):
        recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5), masks=True)
```

Add `import pytest` at the top of `test_reconstruct.py` if it is not there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_openmvs.py tests/test_reconstruct.py -q`
Expected: the two new `densify` tests fail with `TypeError: densify() got an unexpected keyword argument 'mask_path'`; the reconstruct tests fail on `mask_model_path`.

- [ ] **Step 3: Implement**

In `photogrammetry-worker/pipeline/openmvs.py`, replace `densify`:

```python
def densify(runner, dense: Path, scene: Path, use_gpu: bool = True, mask_path: Path | None = None) -> Path:
    out = dense / "scene_dense.mvs"
    cmd = ["DensifyPointCloud", str(scene), "-w", str(dense), "-o", str(out), "--resolution-level", "2"]
    if mask_path is not None:
        # <image stem>.mask.png under mask_path; pixels equal to the label are skipped by the
        # depth-map estimator (masks are nearest-resized to the depth-map size). 0 = background.
        cmd += ["--mask-path", str(mask_path), "--ignore-mask-label", "0"]
    runner.run([*cmd, *_cuda_device(use_gpu)], cwd=dense, tool="DensifyPointCloud")
    return out
```

Replace `photogrammetry-worker/pipeline/reconstruct.py`:

```python
"""Reconstruction stages: one method per tool group; the handler decides refine and decimation."""
from pathlib import Path

from pipeline import colmap, openmvs
from pipeline.colmap import SparseModel
from pipeline.masks import MaskReport, load_model, make_masks


class Reconstruction:
    def __init__(self, runner, work: Path, use_gpu: bool, mask_model_path: Path | None = None):
        self._r = runner
        self._work = work
        self._gpu = use_gpu
        self._mask_model_path = mask_model_path
        self._mask_session = None
        self.mask_report: MaskReport | None = None

    def sfm(self, images: Path) -> SparseModel:
        return colmap.sparse_reconstruct(self._r, self._work, images, self._gpu)

    def dense(self, images: Path, model: SparseModel, masks: bool = False) -> Path:
        """undistort → (masks over the undistorted images) → InterfaceCOLMAP → DensifyPointCloud.
        Masks are made from the undistorted images so they line up with what OpenMVS reads."""
        dense = colmap.undistort(self._r, self._work, images, model)
        mask_path = None
        if masks:
            mask_path = dense / "masks"
            self.mask_report = make_masks(self._session(), dense / "images", mask_path, self._work / "overlays")
        scene = openmvs.interface(self._r, dense)
        openmvs.densify(self._r, dense, scene, self._gpu, mask_path=mask_path)
        return dense

    def _session(self):
        if self._mask_session is None:
            if self._mask_model_path is None:
                raise RuntimeError("background removal requested but MASK_MODEL_PATH is not set")
            self._mask_session = load_model(self._mask_model_path)
        return self._mask_session

    def reconstruct_mesh(self, dense: Path) -> tuple[Path, int]:
        return openmvs.reconstruct_mesh(self._r, dense, dense / "scene_dense.mvs", self._gpu)

    def refine_mesh(self, dense: Path, mesh_ply: Path) -> tuple[Path, int]:
        return openmvs.refine_mesh(self._r, dense, dense / "scene_dense.mvs", mesh_ply, self._gpu)

    def texture(self, dense: Path, mesh_ply: Path, decimate: float | None = None) -> Path:
        return openmvs.texture_mesh(self._r, dense, dense / "scene_dense.mvs", mesh_ply, self._gpu, decimate=decimate)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_openmvs.py tests/test_reconstruct.py -q`
Expected: all pass (existing tests untouched).

- [ ] **Step 5: Commit**

```bash
git add photogrammetry-worker/pipeline/openmvs.py photogrammetry-worker/pipeline/reconstruct.py photogrammetry-worker/tests/test_openmvs.py photogrammetry-worker/tests/test_reconstruct.py
git commit -m "feat(photogrammetry-worker): dense stage masks the undistorted images for DensifyPointCloud"
```

---

### Task 4: Handler — the flag, overlays upload, warning

**Files:**
- Modify: `photogrammetry-worker/models.py`
- Modify: `photogrammetry-worker/handlers/photogrammetry.py`
- Modify: `photogrammetry-worker/tests/test_handler.py`

**Interfaces:**
- Consumes: `Reconstruction.dense(images, model, masks=...)`, `Reconstruction.mask_report`.
- Produces: row column `remove_background`; overlays at `photogrammetry/<user>/<job>/masks/<name>`; `dense.done` payload keys `dense`, `masked`, `unmasked`.

- [ ] **Step 1: Write the failing tests**

In `photogrammetry-worker/tests/test_handler.py`, update `FakeRecon.dense` and add `mask_report`:

```python
    def __init__(self, work, registered=10, fail_at=None, interrupt_at=None, faces=1000, mask_report=None):
        self.work, self.registered, self.fail_at, self.interrupt_at, self.faces = work, registered, fail_at, interrupt_at, faces
        self.calls = []
        self.mask_report = None
        self._mask_report = mask_report
```

```python
    def dense(self, images, model, masks=False):
        self._step(("dense", masks))
        d = self.work / "dense"; d.mkdir(parents=True, exist_ok=True)
        if masks:
            from pipeline.masks import MaskReport
            self.mask_report = self._mask_report or MaskReport(masked=len(list(images.iterdir())), unmasked=[])
            ov = self.work / "overlays"; ov.mkdir(exist_ok=True)
            for p in sorted(images.iterdir()):
                Image.new("RGB", (4, 4)).save(ov / f"{p.stem}.jpg")
        return d
```

Every existing assertion of the form `"dense" in r.calls` or `r.calls == ["sfm", "dense", ...]` must now expect `("dense", False)`. Run the suite after the edit and fix each one (search for `"dense"` in the file).

In `make(...)`, add `remove_background=False` to the parameters and to the `MagicMock(...)` job:

```python
def make(tmp_path, *, status="queued", image_count=10, keys=None, recon_kwargs=None, s3_cls=FakeS3,
         include_placeholder=False, remove_background=False):
    ...
    job = MagicMock(id=job_id, user_id=USER, status=status, stage=None, image_count=image_count,
                    input_prefix=prefix, mesh_s3_key=None, preview_s3_key=None, error_message=None, completed_at=None,
                    warnings=None, processing_started_at=None, remove_background=remove_background)
```

Add tests:

```python
def test_remove_background_masks_dense_and_uploads_overlays_under_masks_prefix(tmp_path):
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert ("dense", True) in recons[0].calls
    mask_keys = sorted(k for k, _, _ in s3.uploaded if "/masks/" in k)
    assert mask_keys == [f"photogrammetry/{USER}/{job.id}/masks/{i:04d}.jpg" for i in range(1, 7)]
    assert all(ct == "image/jpeg" for k, ct, _ in s3.uploaded if "/masks/" in k)
    assert job.status == "complete"


def test_remove_background_off_masks_nothing(tmp_path):
    job, s3, recons, deps = make(tmp_path, image_count=6)
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert ("dense", False) in recons[0].calls
    assert not any("/masks/" in k for k, _, _ in s3.uploaded)


def test_unmasked_photos_become_one_warning(tmp_path):
    from pipeline.masks import MaskReport
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True,
                                 recon_kwargs={"mask_report": MaskReport(masked=4, unmasked=["0002.jpg", "0005.jpg"])})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    assert job.warnings == ["Background could not be separated on 2 photos; they were used unmasked"]
    assert job.status == "complete"


def test_dense_checkpoint_records_mask_counts_and_resume_skips_masking(tmp_path):
    from pipeline.masks import MaskReport
    job, s3, recons, deps = make(tmp_path, image_count=6, remove_background=True,
                                 recon_kwargs={"mask_report": MaskReport(masked=5, unmasked=["0003.jpg"]),
                                               "fail_at": "mesh"})
    process_photogrammetry_job({"job_id": str(job.id)}, deps)
    ck = Checkpoints(deps.work_root / str(job.id))
    done = ck.completed("dense")
    assert done["masked"] == 5 and done["unmasked"] == ["0003.jpg"]
```

Note on the last test: `fail_at="mesh"` fails the job deterministically, which removes scratch — so instead assert the payload *before* the failure by using `interrupt_at="mesh"`:

```python
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
```

Use only this second version (drop the `fail_at` one).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_handler.py -q`
Expected: the four new tests fail (`("dense", True)` never appears; no `/masks/` uploads).

- [ ] **Step 3: Implement**

`photogrammetry-worker/models.py` — add after `photo_status`:

```python
    remove_background: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
```

and extend the SQLAlchemy import line with `Boolean` and `text`.

`photogrammetry-worker/handlers/photogrammetry.py`:

1. Where the row fields are read at claim time, replace
   `user_id, input_prefix, image_count = job.user_id, job.input_prefix, job.image_count` with:

```python
        user_id, input_prefix, image_count = job.user_id, job.input_prefix, job.image_count
        remove_background = bool(getattr(job, "remove_background", False))
```

2. After `output_prefix = ...` add:

```python
    masks_prefix = f"photogrammetry/{user_id}/{job_id}/masks/"
```

3. Replace the dense block:

```python
        # ── dense (undistort → optional object masks → densify) ───────────
        done = ck.completed("dense")
        if done is None:
            _update(deps, job_id, stage="dense"); ck.started("dense")
            dense = recon.dense(images, model, masks=remove_background)
            report = getattr(recon, "mask_report", None) if remove_background else None
            if report is not None:
                warnings.add(*report.warnings())
                ck.done("dense", dense=str(dense), masked=report.masked, unmasked=list(report.unmasked))
            else:
                ck.done("dense", dense=str(dense))
            done = ck.completed("dense")
        dense = Path(done["dense"])
        if remove_background:
            # Overlay previews for the Photos pane, keyed like the input photos (0001.jpg …).
            # Re-run on resume too: the overlays live in scratch and an interrupted upload is cheap.
            overlays = work / "overlays"
            if overlays.is_dir():
                for path in sorted(overlays.iterdir()):
                    deps.s3.upload_file(path, masks_prefix + path.name, "image/jpeg")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest -q`
Expected: whole worker suite green (fix any remaining `"dense"` vs `("dense", False)` assertions).

- [ ] **Step 5: Commit**

```bash
git add photogrammetry-worker/models.py photogrammetry-worker/handlers/photogrammetry.py photogrammetry-worker/tests/test_handler.py
git commit -m "feat(photogrammetry-worker): honour remove_background, upload mask overlays, warn on unmasked photos"
```

---

### Task 5: Worker settings, wiring, image and docs

**Files:**
- Modify: `photogrammetry-worker/config.py`, `photogrammetry-worker/main.py`
- Modify: `photogrammetry-worker/Dockerfile`, `photogrammetry-worker/constraints.txt`
- Modify: `photogrammetry-worker/tests/test_main.py` (if it asserts `build_deps` shape), `photogrammetry-worker/CLAUDE.md`

**Interfaces:**
- Consumes: `Reconstruction(..., mask_model_path=...)`.
- Produces: `Settings.MASK_MODEL_PATH` (default `/opt/models/u2netp.onnx`); the image carries the model and the two packages.

- [ ] **Step 1: Write the failing test**

Append to `photogrammetry-worker/tests/test_main.py`:

```python
def test_build_deps_passes_the_mask_model_path_to_reconstruction(monkeypatch, tmp_path):
    import main
    from config import Settings
    s = Settings(DATABASE_URL="postgresql+psycopg2://u:p@h/db", AUDIO_BUCKET_NAME="b",
                 PHOTOGRAMMETRY_SQS_QUEUE_URL="q", MASK_MODEL_PATH="/opt/models/u2netp.onnx")
    monkeypatch.setattr(main, "make_session_factory", lambda url: object())
    monkeypatch.setattr(main, "S3Client", lambda bucket, region: object())
    deps = main.build_deps(s)
    recon = deps.reconstruction_factory(tmp_path, 0.0)
    assert recon._mask_model_path == Path("/opt/models/u2netp.onnx")
```

(Add `from pathlib import Path` at the top of the test file if missing. If `test_main.py` already patches these names differently, follow its existing pattern.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_main.py -q`
Expected: FAIL — `Settings` rejects `MASK_MODEL_PATH` or `_mask_model_path` is `None`.

- [ ] **Step 3: Implement settings and wiring**

`photogrammetry-worker/config.py` — add:

```python
    MASK_MODEL_PATH: str = "/opt/models/u2netp.onnx"   # u2netp ONNX for remove_background (CPU)
```

`photogrammetry-worker/main.py` — in `build_deps`:

```python
        reconstruction_factory=lambda work, deadline: Reconstruction(
            Runner(deadline=deadline, interrupted=SpotWatcher.interrupted, released=ReleaseWatcher.abort),
            work, use_gpu=bool(s.COLMAP_USE_GPU), mask_model_path=Path(s.MASK_MODEL_PATH)),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_main.py -q`
Expected: PASS.

- [ ] **Step 5: Dockerfile and constraints**

In `photogrammetry-worker/Dockerfile`, in stage 2 just before `WORKDIR /app`:

```dockerfile
# u2netp (U²-Net small, Apache 2.0, 4.5 MB) for the remove_background option: salient-object
# masks over the undistorted images, run on CPU by pipeline/masks.py. Validated on the
# turntable rig 2026-09-04 (spec docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md).
ARG U2NETP_SHA256=309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8
RUN mkdir -p /opt/models \
 && curl -fsSL -o /opt/models/u2netp.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx \
 && echo "${U2NETP_SHA256}  /opt/models/u2netp.onnx" | sha256sum -c -
```

If `curl` is not in the COLMAP runtime image, add `curl ca-certificates` to the stage-2 `apt-get install` line (they are in the build stage already).

Change the pip line to:

```dockerfile
RUN pip install --no-cache-dir -c /app/constraints.txt /app/gpu-worker boto3 pydantic-settings sqlalchemy psycopg2-binary "trimesh>=4.4" pillow numpy onnxruntime opencv-python-headless
```

In `photogrammetry-worker/constraints.txt`, add exact pins for `onnxruntime`, `opencv-python-headless` and their new transitive packages. Take the versions from the dev venv:

Run: `cd photogrammetry-worker && uv pip freeze | grep -i -E "^(onnxruntime|opencv-python-headless|coloredlogs|flatbuffers|humanfriendly|packaging|protobuf|sympy|mpmath)=="`

Add each line that is not already pinned (keep the file sorted). Then:

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest tests/test_constraints.py -q`
Expected: PASS (every Dockerfile package pinned, exact pins only).

- [ ] **Step 6: Build the image locally if disk allows (optional but recommended)**

Run: `docker build -f photogrammetry-worker/Dockerfile -t photogrammetry-worker:masks .`
Expected: the sha256 check line prints `/opt/models/u2netp.onnx: OK` and the build finishes. If the box lacks the ~10 GB, skip and rely on CI (the deploy workflow builds on push to `main`).

If the build succeeds, prove inference inside the image:

```bash
docker run --rm -e LD_LIBRARY_PATH=/opt/cuda-stubs photogrammetry-worker:masks python - <<'PY'
import numpy as np, cv2
from pathlib import Path
from pipeline.masks import load_model, predict, postprocess, margin_for
img = np.full((480, 640, 3), 245, np.uint8); cv2.circle(img, (320, 240), 100, (30, 60, 90), -1)
p = predict(load_model(Path("/opt/models/u2netp.onnx")), img); m = postprocess(p, margin_for(p.shape))
print("centre", m[240, 320], "corner", m[10, 10])
PY
```

Expected: `centre 255 corner 0`.

- [ ] **Step 7: Docs**

`photogrammetry-worker/CLAUDE.md`:

- Pipeline table, `dense` row: `image_undistorter` → **if `remove_background`: `pipeline/masks.py` (u2netp on CPU over the undistorted images → `dense/masks/<stem>.mask.png`, overlays → `work/overlays/`)** → `InterfaceCOLMAP` → `DensifyPointCloud --resolution-level 2` (**+ `--mask-path dense/masks --ignore-mask-label 0`**). Rule column: "a mask never fails a job: coverage outside 1–90 % → all-255 mask + warning 'Background could not be separated on N photos…'; overlays uploaded to `…/<job>/masks/<name>.jpg` after `dense.done`".
- File map: `pipeline/masks.py` — "u2netp session, `predict`, `postprocess` (largest component, holes, 0.5 % margin), `sanity`, `overlay`, `make_masks` → `MaskReport`".
- Env table: `MASK_MODEL_PATH` | `/opt/models/u2netp.onnx` | no — u2netp ONNX for `remove_background`; CPU only.
- Key Commands test count: update the number after running the suite.
- Smoke tests: add "**Background removal smoke**: upload the 76-frame cat set from the rig with *Remove background* ticked; expect the dense stage to log `Mask images` lines, a mesh with no backdrop plane, masks in the Photos pane's *Show masks* toggle, and no 'Background could not be separated' warning."

- [ ] **Step 8: Run the whole worker suite and commit**

Run: `cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest -q`
Expected: all green.

```bash
git add photogrammetry-worker/config.py photogrammetry-worker/main.py photogrammetry-worker/Dockerfile photogrammetry-worker/constraints.txt photogrammetry-worker/tests/test_main.py photogrammetry-worker/CLAUDE.md
git commit -m "feat(photogrammetry-worker): MASK_MODEL_PATH, u2netp in the image, docs"
```

---

### Task 6: API — migration, model, schemas, repository, service

**Files:**
- Create: `chat-api/app/db/migrations/versions/w3x4y5z6a7b8_add_photogrammetry_remove_background.py`
- Create: `chat-api/tests/unit/test_photogrammetry_remove_background_migration.py`
- Modify: `chat-api/app/models/photogrammetry.py`, `chat-api/app/schemas/photogrammetry.py`, `chat-api/app/repositories/photogrammetry.py`, `chat-api/app/services/photogrammetry_service.py`
- Modify: `chat-api/tests/unit/test_photogrammetry_model.py`, `tests/unit/test_photogrammetry_schemas.py`, `tests/unit/repositories/test_photogrammetry_repository.py`, `tests/unit/services/test_photogrammetry_service.py`
- Modify: `chat-api/CLAUDE.md`

**Interfaces:**
- Produces: `JobCreateRequest.remove_background: bool = False`; `JobStatusResponse.remove_background: bool = False`; `PhotoItem.mask_url: Optional[str] = None`; `PhotogrammetryRepository.create_job(..., remove_background: bool = False)`; `PhotogrammetryService._masks_prefix_for(input_prefix) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `chat-api/tests/unit/test_photogrammetry_remove_background_migration.py`:

```python
"""The remove_background migration chains from the compiled_transcripts head and imports cleanly."""
import importlib.util
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "app" / "db" / "migrations" / "versions"


def _load(name: str):
    path = next(VERSIONS.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_remove_background_migration_chains_from_compiled_transcripts():
    mod = _load("w3x4y5z6a7b8")
    assert mod.revision == "w3x4y5z6a7b8"
    assert mod.down_revision == "v2w3x4y5z6a7"
    assert callable(mod.upgrade) and callable(mod.downgrade)
```

Append to `chat-api/tests/unit/test_photogrammetry_model.py`:

```python
def test_remove_background_is_a_non_null_boolean_defaulting_false():
    import app.models  # noqa: F401
    from sqlalchemy import Boolean
    col = Base.metadata.tables["photogrammetry_jobs"].columns["remove_background"]
    assert isinstance(col.type, Boolean) and not col.nullable
    assert str(col.server_default.arg) == "false"
```

Append to `chat-api/tests/unit/test_photogrammetry_schemas.py`:

```python
def test_create_request_remove_background_defaults_false_and_round_trips():
    files = [f"{i}.jpg" for i in range(MIN_IMAGES)]
    assert JobCreateRequest(filenames=files).remove_background is False
    assert JobCreateRequest(filenames=files, remove_background=True).remove_background is True


def test_status_response_remove_background_defaults_false():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(job_id=uuid4(), name="n", status="queued", image_count=5, created_at=now, updated_at=now)
    assert r.remove_background is False


def test_photo_item_mask_url_defaults_none():
    from app.schemas.photogrammetry import PhotoItem
    assert PhotoItem(filename="0001.jpg", url="u").mask_url is None
```

Append to `chat-api/tests/unit/repositories/test_photogrammetry_repository.py`:

```python
async def test_create_job_stores_remove_background():
    repo, db = make_repo()
    job = await repo.create_job(job_id=uuid4(), user_id="u", name="n", image_count=5,
                                input_prefix="p/", remove_background=True)
    assert job.remove_background is True
    default = await repo.create_job(job_id=uuid4(), user_id="u", name="n", image_count=5, input_prefix="p/")
    assert default.remove_background is False
```

In `chat-api/tests/unit/services/test_photogrammetry_service.py`:

- In `make_job(...)`, add `job.remove_background = overrides.get("remove_background", False)`.
- In `make_service(...)`, change the `repo.create_job` side effect to accept the new kwarg:

```python
    repo.create_job = AsyncMock(
        side_effect=lambda job_id, user_id, name, image_count, input_prefix, remove_background=False: make_job(
            id=job_id, image_count=image_count, remove_background=remove_background
        )
    )
```

- Add to the create-job test class:

```python
    async def test_remove_background_reaches_the_row(self):
        svc, repo, _ = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=[f"{i}.jpg" for i in range(5)], remove_background=True))
        assert repo.create_job.await_args.kwargs["remove_background"] is True

    async def test_remove_background_defaults_false(self):
        svc, repo, _ = make_service()
        await svc.create_job("user1", JobCreateRequest(filenames=[f"{i}.jpg" for i in range(5)]))
        assert repo.create_job.await_args.kwargs["remove_background"] is False
```

- Add to the status test class:

```python
    async def test_status_echoes_remove_background(self):
        job = make_job(status="queued", remove_background=True)
        svc, *_ = make_service(job=job)
        assert (await svc.get_job_status("user1", job.id)).remove_background is True
```

- Add to `TestListJobPhotos`:

```python
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

    async def test_sample_photos_have_no_mask_url(self):
        svc, _, storage = make_service(keys=["samples/photogrammetry/images/0001.jpg"])
        with patch.object(ps, "ensure_thumbnails"):
            res = await svc.list_sample_photos()
            await drain_thumbs()
        assert res.photos[0].mask_url is None
```

Also add `test_walk_ignores_remove_background` to the mock class only if the mock's `create_job` path breaks; otherwise the inherited `create_job` covers it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd chat-api && uv run pytest tests/unit/test_photogrammetry_remove_background_migration.py tests/unit/test_photogrammetry_model.py tests/unit/test_photogrammetry_schemas.py tests/unit/repositories/test_photogrammetry_repository.py tests/unit/services/test_photogrammetry_service.py -q`
Expected: the new tests fail (`StopIteration` for the missing migration, `KeyError: 'remove_background'`, `ValidationError`/`AttributeError`).

- [ ] **Step 3: Implement**

Create `chat-api/app/db/migrations/versions/w3x4y5z6a7b8_add_photogrammetry_remove_background.py`:

```python
"""add remove_background to photogrammetry_jobs (turntable background removal, spec 2026-09-04)

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "photogrammetry_jobs",
        sa.Column("remove_background", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("photogrammetry_jobs", "remove_background")
```

`chat-api/app/models/photogrammetry.py` — after `is_public`:

```python
    # Mask the backdrop out of the dense stage (worker: pipeline/masks.py). Off by default;
    # the sample scan and the mock never set it.
    remove_background: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
```

`chat-api/app/schemas/photogrammetry.py`:

```python
class JobCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    filenames: List[str] = Field(..., min_length=MIN_IMAGES)
    # Turntable scans: keep the backdrop for SfM, mask it out of the dense stage.
    remove_background: bool = False
```

In `JobStatusResponse`, after `is_public: bool = False`:

```python
    remove_background: bool = False
```

In `PhotoItem`, after `status`:

```python
    # Presigned GET of the worker's mask overlay (…/<job>/masks/<name>.jpg) once the dense stage
    # has run with remove_background; None otherwise (and always for the sample set).
    mask_url: Optional[str] = None
```

`chat-api/app/repositories/photogrammetry.py`:

```python
    async def create_job(
        self, job_id: UUID, user_id: str, name: str, image_count: int, input_prefix: str,
        remove_background: bool = False,
    ) -> PhotogrammetryJob:
        job = PhotogrammetryJob(
            id=job_id,
            user_id=user_id,
            name=name,
            status="pending",
            image_count=image_count,
            input_prefix=input_prefix,
            remove_background=remove_background,
        )
```

`chat-api/app/services/photogrammetry_service.py`:

- `create_job`: pass `remove_background=request.remove_background` to `self._repo.create_job(...)`.
- `_to_response`: add `remove_background=bool(getattr(job, "remove_background", False)),`.
- Add beside `_thumbs_prefix_for`:

```python
    @staticmethod
    def _masks_prefix_for(input_prefix: str) -> str:
        """…/<job>/input/ → …/<job>/masks/ — where the worker puts its overlay previews."""
        return f"{PurePosixPath(input_prefix.rstrip('/')).parent}/masks/"
```

- In `_photos`, after `existing = set(...)`:

```python
        masks_prefix = self._masks_prefix_for(images_prefix)
        masks = set(self._storage.list_keys_with_prefix(masks_prefix))
```

and in the `PhotoItem(...)` construction:

```python
                mask_url=(
                    presign(f"{masks_prefix}{name}", ttl_seconds=DOWNLOAD_TTL_SECONDS)
                    if f"{masks_prefix}{name}" in masks else None
                ),
```

Note the sample listing calls `_photos` with `samples/photogrammetry/images/`, so the masks prefix is `samples/photogrammetry/masks/`, which never exists: one empty listing, no behaviour change. In the mock (`LocalPhotogrammetryService`), `self._storage.list_keys_with_prefix` on the dev sink already returns `[]` for unknown prefixes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd chat-api && uv run pytest -q`
Expected: whole API suite green. Existing service tests that pin `storage.list_keys_with_prefix.side_effect` to a two-way `if p == job.input_prefix ... else thumbs` now also answer the masks prefix with the thumbs list; check they still pass (a thumbs key never equals `<masks_prefix><name>`, so `mask_url` stays `None`). If one asserts the *number* of `list_keys_with_prefix` calls, update it by one.

- [ ] **Step 5: Docs and commit**

`chat-api/CLAUDE.md`: on the `photogrammetry.py PhotogrammetryJob (...)` model line add `remove_background`; in the endpoint/flow paragraph that mentions `photo_status`, add "`GET /jobs/{id}/photos` also presigns `mask_url` from `…/<job>/masks/<name>.jpg` when the worker ran with `remove_background`".

```bash
git add chat-api/app chat-api/tests chat-api/CLAUDE.md
git commit -m "feat(chat-api): remove_background on photogrammetry jobs; mask_url on photos"
```

---

### Task 7: Vue — types, API client, store, New Scan checkbox

**Files:**
- Modify: `chat-vue/src/types/index.ts`, `chat-vue/src/lib/photogrammetryApi.ts`, `chat-vue/src/stores/photogrammetry.ts`, `chat-vue/src/components/photogrammetry/NewScanForm.vue`
- Modify: `chat-vue/src/lib/__tests__/photogrammetryApi.spec.ts`
- Create: `chat-vue/src/components/photogrammetry/__tests__/NewScanForm.spec.ts`

**Interfaces:**
- Produces: `createJob(name: string | null, filenames: string[], removeBackground = false)`; `store.submitScan(name, files, removeBackground = false)`; `PhotogrammetryJob.remove_background?: boolean`; `PhotoItem.mask_url?: string | null`.

- [ ] **Step 1: Write the failing tests**

In `chat-vue/src/lib/__tests__/photogrammetryApi.spec.ts`, find the existing `createJob` test (it asserts `apiClient.post` was called with `{ name, filenames }`) and add beside it:

```ts
  it("createJob sends remove_background, false by default", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { job_id: "j", uploads: [] } })
    await createJob("n", ["a.jpg"])
    expect(apiClient.post).toHaveBeenLastCalledWith("/api/v1/photogrammetry/jobs", { name: "n", filenames: ["a.jpg"], remove_background: false })
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { job_id: "j", uploads: [] } })
    await createJob("n", ["a.jpg"], true)
    expect(apiClient.post).toHaveBeenLastCalledWith("/api/v1/photogrammetry/jobs", { name: "n", filenames: ["a.jpg"], remove_background: true })
  })
```

(Match the file's existing mock setup for `apiClient` — reuse whatever `vi.mock("@/lib/axios", …)` it already has.)

Create `chat-vue/src/components/photogrammetry/__tests__/NewScanForm.spec.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/lib/photogrammetryApi", () => ({
  fetchJobPhotos: vi.fn(),
  fetchSamplePhotos: vi.fn(),
  deleteJob: vi.fn(),
  listJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  confirmJob: vi.fn(),
  createSampleJob: vi.fn(),
  getMeshUrl: vi.fn(),
  uploadToS3: vi.fn(),
  setJobVisibility: vi.fn(),
}))

import * as api from "@/lib/photogrammetryApi"
import { usePhotogrammetryStore } from "@/stores/photogrammetry"
import NewScanForm from "../NewScanForm.vue"

function file(name: string): File {
  return new File(["x"], name, { type: "image/jpeg" })
}

describe("NewScanForm — Remove background", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.createJob).mockReset().mockResolvedValue({ job_id: "j1", uploads: [] })
    vi.mocked(api.confirmJob).mockReset().mockResolvedValue(undefined)
    vi.mocked(api.getJob).mockReset().mockResolvedValue({
      job_id: "j1", name: "n", status: "queued", stage: null, image_count: 1, preview_url: null,
      error_message: null, warnings: [], mock: false, created_at: "", updated_at: "", completed_at: null,
    })
  })

  it("is unchecked by default and passes false", async () => {
    const w = mount(NewScanForm, { props: { sample: false } })
    const box = w.find('input[type="checkbox"][name="remove_background"]')
    expect(box.exists()).toBe(true)
    expect((box.element as HTMLInputElement).checked).toBe(false)
    const store = usePhotogrammetryStore()
    const spy = vi.spyOn(store, "submitScan").mockResolvedValue("j1")
    ;(w.vm as any).files = [file("a.jpg")]
    await w.vm.$nextTick()
    await w.find("form").trigger("submit")
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(expect.any(String), [expect.any(File)], false)
  })

  it("ticked, passes true to submitScan", async () => {
    const w = mount(NewScanForm, { props: { sample: false } })
    await w.find('input[type="checkbox"][name="remove_background"]').setValue(true)
    const store = usePhotogrammetryStore()
    const spy = vi.spyOn(store, "submitScan").mockResolvedValue("j1")
    ;(w.vm as any).files = [file("a.jpg")]
    await w.vm.$nextTick()
    await w.find("form").trigger("submit")
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(expect.any(String), [expect.any(File)], true)
  })

  it("is hidden in sample mode", async () => {
    vi.mocked(api.fetchSamplePhotos).mockResolvedValue({ name: "Sample scan", image_count: 1, photos: [] })
    const w = mount(NewScanForm, { props: { sample: true } })
    await flushPromises()
    expect(w.find('input[name="remove_background"]').exists()).toBe(false)
  })
})
```

If `(w.vm as any).files` is not reachable because `<script setup>` does not expose refs, add `defineExpose({ files })` in `NewScanForm.vue` for the test, or drive the `ImageDropzone` `files-changed` emit: `await w.findComponent({ name: "ImageDropzone" }).vm.$emit("files-changed", [file("a.jpg")])`. Prefer the emit.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd chat-vue && npx vitest run src/lib/__tests__/photogrammetryApi.spec.ts src/components/photogrammetry/__tests__/NewScanForm.spec.ts`
Expected: FAIL (no `remove_background` in the post body; no checkbox).

- [ ] **Step 3: Implement**

`chat-vue/src/types/index.ts`:

- In `PhotogrammetryJob`, after `is_public?: boolean`: `remove_background?: boolean`.
- In `PhotoItem`, after `status`:

```ts
  /** Presigned mask-overlay URL (worker's masks/<name>.jpg) once the dense stage ran with background removal; null/absent otherwise. */
  mask_url?: string | null
```

`chat-vue/src/lib/photogrammetryApi.ts`:

```ts
export async function createJob(name: string | null, filenames: string[], removeBackground = false): Promise<PhotogrammetryJobCreateResponse> {
  const res = await apiClient.post(`${BASE}/jobs`, { name, filenames, remove_background: removeBackground })
  return res.data
}
```

`chat-vue/src/stores/photogrammetry.ts` — `submitScan`:

```ts
  async function submitScan(name: string, files: File[], removeBackground = false): Promise<string> {
    let job_id: string
    try {
      const created = await api.createJob(name || null, files.map(f => f.name), removeBackground)
```

and in `placeholder(...)`'s returned object add `remove_background: removeBackground` only if `placeholder` gains the parameter; simpler: leave `placeholder` alone (the flag is optional in the type and arrives with the next poll).

`chat-vue/src/components/photogrammetry/NewScanForm.vue`:

- script: `const removeBackground = ref(false)`; in `submit()`: `await store.submitScan(name.value.trim(), files.value, removeBackground.value)`; in the `watch(() => props.sample, …)` else-branch add `removeBackground.value = false`.
- template, directly after the Name `<label>` block:

```vue
    <label v-if="!props.sample" class="flex items-start gap-2 text-sm">
      <input v-model="removeBackground" type="checkbox" name="remove_background" class="mt-0.5" />
      <span>
        <span class="text-gray-700">Remove background</span>
        <span class="block text-xs text-gray-400">For turntable scans. Keeps only the object in the finished model.</span>
      </span>
    </label>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd chat-vue && npx vitest run src/lib/__tests__/photogrammetryApi.spec.ts src/components/photogrammetry/__tests__/NewScanForm.spec.ts src/stores/__tests__/photogrammetry.spec.ts && npx vue-tsc --noEmit`
Expected: PASS, and type-check clean.

- [ ] **Step 5: Commit**

```bash
git add chat-vue/src/types/index.ts chat-vue/src/lib/photogrammetryApi.ts chat-vue/src/stores/photogrammetry.ts chat-vue/src/components/photogrammetry/NewScanForm.vue chat-vue/src/lib/__tests__/photogrammetryApi.spec.ts chat-vue/src/components/photogrammetry/__tests__/NewScanForm.spec.ts
git commit -m "feat(chat-vue): Remove background checkbox on New Scan"
```

---

### Task 8: Vue — "Show masks" toggle, header note, user guide

**Files:**
- Modify: `chat-vue/src/components/photogrammetry/PhotoGrid.vue`, `chat-vue/src/components/photogrammetry/ScanDetailView.vue`
- Modify: `chat-vue/src/components/photogrammetry/__tests__/PhotoGrid.spec.ts`
- Modify: `docs/user-guide.md`

**Interfaces:**
- Consumes: `PhotoItem.mask_url`, `PhotogrammetryJob.remove_background`.

- [ ] **Step 1: Write the failing tests**

Append to `chat-vue/src/components/photogrammetry/__tests__/PhotoGrid.spec.ts`:

```ts
describe("PhotoGrid — Show masks", () => {
  const masked = [
    { filename: "0001.jpg", url: "https://s3/full/0001.jpg", thumb_url: "https://s3/thumbs/0001.jpg", mask_url: "https://s3/masks/0001.jpg" },
    { filename: "0002.jpg", url: "https://s3/full/0002.jpg", thumb_url: "https://s3/thumbs/0002.jpg", mask_url: null },
  ]

  it("offers no toggle when no photo has a mask", () => {
    const w = mount(PhotoGrid, { props: { photos } })
    expect(w.find('[data-testid="show-masks"]').exists()).toBe(false)
  })

  it("toggle swaps tiles with a mask to the overlay and leaves the rest", async () => {
    const w = mount(PhotoGrid, { props: { photos: masked } })
    const toggle = w.find('[data-testid="show-masks"]')
    expect(toggle.exists()).toBe(true)
    expect((toggle.element as HTMLInputElement).checked).toBe(false)
    await toggle.setValue(true)
    const imgs = w.findAll('[data-testid="photo-tile"] img')
    expect(imgs[0].attributes("src")).toBe("https://s3/masks/0001.jpg")
    expect(imgs[1].attributes("src")).toBe("https://s3/thumbs/0002.jpg")
    await toggle.setValue(false)
    expect(w.findAll('[data-testid="photo-tile"] img')[0].attributes("src")).toBe("https://s3/thumbs/0001.jpg")
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd chat-vue && npx vitest run src/components/photogrammetry/__tests__/PhotoGrid.spec.ts`
Expected: the toggle test fails (`show-masks` not found).

- [ ] **Step 3: Implement**

`chat-vue/src/components/photogrammetry/PhotoGrid.vue` script — after `stillLoading`:

```ts
// ── mask overlays (remove_background scans): the worker's darkened-background preview per photo ──
const showMasks = ref(false)
const hasMasks = computed(() => props.photos.some(p => !!p.mask_url))
function tileSrc(photo: PhotoItem): string | null {
  return showMasks.value && photo.mask_url ? photo.mask_url : photo.thumb_url
}
```

Template: in the status line `<p … data-testid="photo-status">`, append after `<span>{{ status }}</span>`:

```vue
        <label v-if="hasMasks" class="ml-auto flex items-center gap-1 text-xs text-gray-600">
          <input v-model="showMasks" type="checkbox" data-testid="show-masks" />
          Show masks
        </label>
```

Because the `<p>` only renders when `status` is non-empty, and `status` is empty only with zero photos, the toggle always has a home when there are photos. Change the tile `<img>` to use `tileSrc`:

```vue
          <img
            v-if="tileSrc(photo)"
            :src="tileSrc(photo)!"
```

(Everything else on the `<img>` stays. The overlay on click still shows `open.url`, the original.)

`chat-vue/src/components/photogrammetry/ScanDetailView.vue` header — after the `{{ job.image_count }} photos` span:

```vue
        <span v-if="job.remove_background" class="text-xs text-gray-500" title="Backdrop masked out of the dense stage">· background removed</span>
```

`docs/user-guide.md`:

- Under **Starting a scan**, add a bullet: "**Remove background** (tick it for turntable scans on a patterned backdrop): the backdrop stays in the photos for camera matching and is masked out before the dense reconstruction, so the model is the object alone and the scan runs faster. Leave it off for scenes, rooms and anything where the surroundings *are* the subject."
- Under **The result**, in the Photos bullet, add: "For a scan with *Remove background*, a **Show masks** toggle above the grid swaps each thumbnail for the mask preview (background dimmed, object outlined). If a mask clips part of the object, start a new scan with the box unticked."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd chat-vue && npm test -- --run && npx vue-tsc --noEmit`
Expected: all photogrammetry specs pass; only the two known `.env.local` auth specs may fail.

- [ ] **Step 5: Commit**

```bash
git add chat-vue/src/components/photogrammetry/PhotoGrid.vue chat-vue/src/components/photogrammetry/ScanDetailView.vue chat-vue/src/components/photogrammetry/__tests__/PhotoGrid.spec.ts docs/user-guide.md
git commit -m "feat(chat-vue): Show masks toggle in the Photos pane; user guide"
```

---

### Task 9: Whole-tree verification

- [ ] **Step 1: Run every suite**

```bash
cd photogrammetry-worker && PYTHONPATH=../gpu-worker uv run pytest -q
cd ../chat-api && uv run pytest -q
cd ../chat-vue && npm test -- --run && npx vue-tsc --noEmit && npm run lint
```

Expected: green (the two `.env.local` auth specs excepted).

- [ ] **Step 2: Review the diff against the spec**

`git log --oneline main..HEAD` shows eight commits; `git diff main --stat` touches only the files in the file map. Confirm no `.claude/` is staged.

- [ ] **Step 3: Update the spec status line**

In `docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md`, change `**Status:** approved design, awaiting implementation plan.` to `**Status:** implemented 2026-09-04 (plan docs/superpowers/plans/2026-09-04-photogrammetry-background-removal.md); awaiting production smoke on the rig set.` and commit:

```bash
git add docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md
git commit -m "docs: background-removal spec status"
```
