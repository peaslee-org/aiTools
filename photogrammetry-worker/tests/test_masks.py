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
    assert m[40, 60] == 255 and m[159, 199] == 255
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


# ── inference, overlays, make_masks ──────────────────────────────────────────
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
