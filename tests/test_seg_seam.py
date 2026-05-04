"""Unit tests for scripts/jetson/seg_seam.py.

These tests don't require a Jetson, VPI, GStreamer, or Ultralytics. They
exercise the SegmentationProvider interface and each lightweight provider
on synthetic 256x256 BGR frames so the full machinery can be verified on
the Windows / Linux CI machine before code lands on the car.

Adds the scripts/jetson directory to sys.path the same way bench_stitch.py
does, since seg_seam.py is intentionally a script-mode module rather than
a packaged import.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

# cv2 is a hard requirement for everything but OffProvider. Tests that
# need it skip cleanly when running on a machine without OpenCV.
cv2 = pytest.importorskip("cv2")

# Add scripts/jetson/ to sys.path so we can import seg_seam directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "jetson"))

import seg_seam  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_seg_env(monkeypatch):
    """Each test gets a clean env so prior tests don't leak settings."""
    for var in list(os.environ):
        if var.startswith("RCPILOT_STITCH_"):
            monkeypatch.delenv(var, raising=False)


def _solid_frame(h: int, w: int, color: tuple) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color  # BGR
    return frame


def _frame_with_blob(h: int, w: int, color: tuple,
                     blob_color: tuple,
                     blob_box: tuple) -> np.ndarray:
    """Solid frame with a rectangular blob of `blob_color` at blob_box (x, y, w, h)."""
    frame = _solid_frame(h, w, color)
    x, y, bw, bh = blob_box
    frame[y:y + bh, x:x + bw] = blob_color
    return frame


# ---------------------------------------------------------------------------
# Factory and listing
# ---------------------------------------------------------------------------


def test_list_providers_contains_all_known_backends():
    names = seg_seam.list_providers()
    assert set(names) == {"off", "absdiff", "skin", "yolo26"}


def test_unknown_provider_falls_back_to_absdiff(monkeypatch, capsys):
    monkeypatch.setenv("RCPILOT_STITCH_SEG", "does-not-exist")
    p = seg_seam.get_provider()
    assert p.name == "absdiff"
    err = capsys.readouterr().err
    assert "unknown" in err.lower()


def test_get_provider_explicit_name_overrides_env(monkeypatch):
    monkeypatch.setenv("RCPILOT_STITCH_SEG", "absdiff")
    p = seg_seam.get_provider("off")
    assert p.name == "off"


# ---------------------------------------------------------------------------
# OffProvider
# ---------------------------------------------------------------------------


def test_off_provider_always_returns_none():
    p = seg_seam.OffProvider()
    left = _solid_frame(64, 64, (10, 10, 10))
    right = _solid_frame(64, 64, (200, 200, 200))
    for idx in range(5):
        assert p.mask(left, right, idx) is None


# ---------------------------------------------------------------------------
# AbsdiffProvider
# ---------------------------------------------------------------------------


def test_absdiff_self_cadences(monkeypatch):
    """every_n=3 means refreshes only on frame_idx 0, 3, 6, ..."""
    monkeypatch.setenv("RCPILOT_STITCH_ABSDIFF_EVERY_N", "3")
    p = seg_seam.AbsdiffProvider()
    same = _solid_frame(64, 64, (50, 50, 50))
    assert p.mask(same, same, 0) is not None
    assert p.mask(same, same, 1) is None
    assert p.mask(same, same, 2) is None
    assert p.mask(same, same, 3) is not None


def test_absdiff_no_disagreement_yields_empty_mask():
    p = seg_seam.AbsdiffProvider()
    same = _solid_frame(64, 64, (100, 50, 25))
    out = p.mask(same, same, 0)
    assert out is not None
    assert int(out.sum()) == 0


def test_absdiff_detects_synthetic_disagreement():
    """A bright blob in only the LEFT image should mask in the same region."""
    p = seg_seam.AbsdiffProvider()
    bg = (40, 40, 40)
    left = _frame_with_blob(128, 128, bg, (255, 255, 255), (40, 40, 40, 40))
    right = _solid_frame(128, 128, bg)
    out = p.mask(left, right, 0)
    assert out is not None
    # The blob region should be masked. Allow dilation slop.
    blob_pixels = int((out[40:80, 40:80] > 0).sum())
    assert blob_pixels >= 40 * 40 * 0.9, (
        f"expected blob region to be masked, got only {blob_pixels} pixels"
    )


def test_absdiff_respects_overlap_mask():
    """If overlap_mask is zero outside a region, the output mask should also be zero there."""
    p = seg_seam.AbsdiffProvider()
    left = _frame_with_blob(128, 128, (40, 40, 40), (255, 255, 255),
                            (40, 40, 40, 40))
    right = _solid_frame(128, 128, (40, 40, 40))
    overlap = np.zeros((128, 128), dtype=np.uint8)
    overlap[64:, :] = 255  # only bottom half is overlap
    out = p.mask(left, right, 0, overlap_mask=overlap)
    assert out is not None
    assert int(out[:64, :].sum()) == 0  # top half must be empty


# ---------------------------------------------------------------------------
# SkinColorProvider
# ---------------------------------------------------------------------------


def test_skin_detects_skin_colored_blob():
    """A skin-toned BGR blob in the left image should be detected."""
    p = seg_seam.SkinColorProvider()
    bg = (40, 40, 40)  # dark gray, definitely not skin
    skin = (100, 140, 200)  # warm BGR — typical skin tone in HSV default range
    left = _frame_with_blob(256, 256, bg, skin, (60, 60, 80, 80))
    right = _solid_frame(256, 256, bg)  # right is unused on default source=left
    out = p.mask(left, right, 0)
    assert out is not None
    blob_pixels = int((out[60:140, 60:140] > 0).sum())
    assert blob_pixels >= 80 * 80 * 0.5, (
        f"expected skin blob to register, got {blob_pixels}"
    )


def test_skin_rejects_non_skin_colors():
    p = seg_seam.SkinColorProvider()
    blue = (200, 50, 30)  # BGR blue, not skin
    bg = (40, 40, 40)
    left = _frame_with_blob(256, 256, bg, blue, (60, 60, 80, 80))
    right = _solid_frame(256, 256, bg)
    out = p.mask(left, right, 0)
    assert out is not None
    assert int(out.sum()) == 0


def test_skin_min_area_suppresses_noise(monkeypatch):
    """A tiny skin blob below min_area should be suppressed."""
    monkeypatch.setenv("RCPILOT_STITCH_SKIN_MIN_AREA", "10000")
    monkeypatch.setenv("RCPILOT_STITCH_SKIN_DILATE_PX", "0")
    p = seg_seam.SkinColorProvider()
    bg = (40, 40, 40)
    skin = (100, 140, 200)
    # 30x30 = 900 pixels, below 10000.
    left = _frame_with_blob(256, 256, bg, skin, (10, 10, 30, 30))
    right = _solid_frame(256, 256, bg)
    out = p.mask(left, right, 0)
    assert out is not None
    assert int(out.sum()) == 0


def test_skin_self_cadences(monkeypatch):
    monkeypatch.setenv("RCPILOT_STITCH_SKIN_EVERY_N", "4")
    p = seg_seam.SkinColorProvider()
    bg = (40, 40, 40)
    left = _solid_frame(64, 64, bg)
    right = _solid_frame(64, 64, bg)
    assert p.mask(left, right, 0) is not None
    assert p.mask(left, right, 1) is None
    assert p.mask(left, right, 4) is not None


# ---------------------------------------------------------------------------
# Yolo26SegProvider
# ---------------------------------------------------------------------------


def test_yolo26_missing_ultralytics_raises_helpful_error(monkeypatch):
    """Without ultralytics installed, instantiation should fail with a clear
    message, not a confusing ImportError stack trace at first frame.

    On a machine that DOES have ultralytics, this test is skipped — we still
    need RCPILOT_STITCH_YOLO_ENGINE for it to construct, and we don't want
    to download a real engine in CI.
    """
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        # Expected path: ultralytics absent, helpful error.
        with pytest.raises(RuntimeError, match="ultralytics is not installed"):
            seg_seam.Yolo26SegProvider()
        return
    # Ultralytics IS installed — verify the missing-engine path instead.
    monkeypatch.delenv("RCPILOT_STITCH_YOLO_ENGINE", raising=False)
    with pytest.raises(RuntimeError, match="RCPILOT_STITCH_YOLO_ENGINE"):
        seg_seam.Yolo26SegProvider()


def test_get_provider_yolo26_falls_back_to_absdiff_on_init_failure(
    monkeypatch, capsys,
):
    monkeypatch.setenv("RCPILOT_STITCH_SEG", "yolo26")
    # Don't set RCPILOT_STITCH_YOLO_ENGINE so init will fail either way
    p = seg_seam.get_provider()
    # Should fall back gracefully, not crash.
    assert p.name == "absdiff"
    err = capsys.readouterr().err
    assert "yolo26" in err.lower()


# ---------------------------------------------------------------------------
# Determinism — same input → same output
# ---------------------------------------------------------------------------


def test_absdiff_is_deterministic():
    p1 = seg_seam.AbsdiffProvider()
    p2 = seg_seam.AbsdiffProvider()
    bg = (40, 40, 40)
    left = _frame_with_blob(128, 128, bg, (255, 255, 255), (40, 40, 40, 40))
    right = _solid_frame(128, 128, bg)
    m1 = p1.mask(left, right, 0)
    m2 = p2.mask(left, right, 0)
    assert m1 is not None and m2 is not None
    assert np.array_equal(m1, m2)
