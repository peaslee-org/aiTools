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
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


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
