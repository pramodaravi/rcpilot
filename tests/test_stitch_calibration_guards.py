"""Guard rails added 2026-05-04 after the singular-matrix crash loop.

Background: a degenerate affine-RANSAC fit collapsed to (near-)identity,
got saved to stitch_calibration.json with reproj_error=0.0 and
inlier_ratio=0.11. On every service restart, load_calibration accepted it
without re-validation, then bake_fast_path called np.linalg.inv on a
near-singular composed homography and crashed. systemd respawned into the
same crash 572 times.

These tests pin the gates that prevent it from recurring:
  - load_calibration rejects degenerate cached homographies on load.
  - estimate_homography rejects too-low inlier ratios and zero-error fits.
  - bake_fast_path raises SystemExit (not LinAlgError) on a singular plan.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "jetson"))

import stitch_video as sv  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_stitch_env(monkeypatch):
    for var in list(os.environ):
        if var.startswith("RCPILOT_STITCH_"):
            monkeypatch.delenv(var, raising=False)


def test_blend_homography_smooths_live_updates():
    current = sv.approximate_homography(1280, overlap_px=180)
    target = current.copy()
    target[0, 2] += 100.0

    blended = sv.blend_homography(current, target, alpha=0.25)

    assert blended[2, 2] == pytest.approx(1.0)
    assert blended[0, 2] == pytest.approx(current[0, 2] + 25.0)


def test_homography_corner_delta_reports_largest_corner_motion():
    current = sv.approximate_homography(1280, overlap_px=180)
    target = current.copy()
    target[0, 2] += 42.0

    assert sv.homography_corner_delta_px(current, target, 1280, 720) == pytest.approx(42.0)


def _write_cache(tmp_path: Path, **overrides) -> Path:
    """Write a stitch_calibration.json with the named overrides on top of a
    plausible default. Returns the path."""
    base = {
        "width": 1280,
        "height": 720,
        # Default: a reasonable translation+slight-rotation homography that
        # places the right camera 1100px to the right with 180px overlap.
        "h_right_to_left": [
            [1.0, 0.0, 1100.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        "inliers": 80,
        "matches": 120,
        "inlier_ratio": 0.67,
        "detector": "SIFT",
        "model": "homography",
        "reproj_error_px": 3.5,
        "created_at": 0,
    }
    base.update(overrides)
    p = tmp_path / "stitch_calibration.json"
    p.write_text(json.dumps(base))
    return p


# ---------------------------------------------------------------------------
# load_calibration health gates
# ---------------------------------------------------------------------------


def test_load_accepts_a_sane_calibration(tmp_path):
    log = __import__("logging").getLogger("test")
    p = _write_cache(tmp_path)
    result = sv.load_calibration(p, 1280, 720, log)
    assert result is not None
    assert result.detector == "cached"
    assert abs(result.h_right_to_left[0, 2] - 1100.0) < 1e-6


def test_load_rejects_zero_reproj_error(tmp_path, caplog):
    """A reproj error of exactly 0.0 means RANSAC found a trivial identity-
    like fit. The historical bug saved exactly this."""
    log = __import__("logging").getLogger("test")
    p = _write_cache(tmp_path, reproj_error_px=0.0)
    with caplog.at_level("WARNING"):
        result = sv.load_calibration(p, 1280, 720, log)
    assert result is None
    assert any("impossibly clean" in rec.getMessage().lower()
               or "rejecting" in rec.getMessage().lower()
               for rec in caplog.records)


def test_load_rejects_low_inlier_ratio(tmp_path, caplog):
    """0.11 ratio is what the broken cache had on 2026-05-04."""
    log = __import__("logging").getLogger("test")
    p = _write_cache(tmp_path, inlier_ratio=0.11, inliers=18, matches=160)
    with caplog.at_level("WARNING"):
        result = sv.load_calibration(p, 1280, 720, log)
    assert result is None


def test_load_rejects_identity_shaped_homography(tmp_path, caplog):
    """Identity h means the right camera lies on top of the left — exactly
    the failure that made canvas come out as 1280x720 instead of ~2380x720."""
    log = __import__("logging").getLogger("test")
    p = _write_cache(
        tmp_path,
        h_right_to_left=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        # Make the other gates pass so we know it's the identity check that fires.
        reproj_error_px=2.0,
        inlier_ratio=0.6,
    )
    with caplog.at_level("WARNING"):
        result = sv.load_calibration(p, 1280, 720, log)
    assert result is None


def test_load_rejects_near_singular_homography(tmp_path, caplog):
    """A homography with det ≈ 0 will crash np.linalg.inv at bake time."""
    log = __import__("logging").getLogger("test")
    near_singular = [
        [1.0, 1.0, 0.0],
        [1.0, 1.0 + 1e-12, 0.0],  # rows nearly identical
        [0.0, 0.0, 1.0],
    ]
    p = _write_cache(
        tmp_path,
        h_right_to_left=near_singular,
        reproj_error_px=2.0,
        inlier_ratio=0.6,
    )
    with caplog.at_level("WARNING"):
        result = sv.load_calibration(p, 1280, 720, log)
    assert result is None


def test_load_health_gate_rationale_in_message(tmp_path, caplog):
    """The rejection log line should explain WHY — makes diagnosing the
    next regression in the field much faster."""
    log = __import__("logging").getLogger("test")
    p = _write_cache(tmp_path, reproj_error_px=0.0)
    with caplog.at_level("WARNING"):
        sv.load_calibration(p, 1280, 720, log)
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "rejecting" in msgs.lower()
    # One of the gate-specific phrases must appear:
    assert any(s in msgs.lower() for s in (
        "impossibly clean", "near-singular", "ill-conditioned",
        "inlier ratio", "identity-shaped",
    ))


# ---------------------------------------------------------------------------
# bake_fast_path → SystemExit (not LinAlgError) on singular plan
# ---------------------------------------------------------------------------


def test_bake_fast_path_raises_systemexit_on_singular_h_canvas():
    """A truly singular plan.h_canvas should produce a clear SystemExit
    with operator-actionable guidance, not a numpy.linalg.LinAlgError stack
    trace dropped into systemd."""
    log = __import__("logging").getLogger("test")
    # Build a plausible plan but corrupt h_canvas to be singular.
    h_ok = np.array([[1.0, 0.0, 1100.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    plan = sv.make_plan(
        h_ok, 1280, 720,
        max_canvas_w=1280 * 4, max_canvas_h=720 * 3,
        output_aspect=2560.0 / 720.0,
        use_cuda=False, log=log,
    )
    plan.h_canvas = np.array(
        [[1.0, 1.0, 0.0],
         [1.0, 1.0, 0.0],   # rank deficient - no third linearly independent row
         [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    with pytest.raises(SystemExit) as exc_info:
        sv.bake_fast_path(plan, 1280, 720, 2560, 720, "cpu", log)
    msg = str(exc_info.value)
    assert "singular" in msg.lower()
    # Must hint at remediation so an operator can fix it without reading source.
    assert "stitch_calibration.json" in msg or "RCPILOT_STITCH_RECALIBRATE" in msg


# ---------------------------------------------------------------------------
# vpi_blend_fast — VIC/CUDA backend with cv2 fallback
# ---------------------------------------------------------------------------


def test_vpi_blend_resolver_returns_none_or_dict():
    """The op resolver should never raise; it should return None when VPI
    isn't installed, or a dict with mul/add/backend when it is."""
    result = sv._resolve_vpi_blend_ops()
    assert result is None or (
        isinstance(result, dict)
        and {"vpi", "mul", "add", "backend"}.issubset(result)
    )


def test_vpi_blend_fast_raises_helpfully_when_unavailable(monkeypatch):
    """When VPI ops can't be resolved, vpi_blend_fast must raise RuntimeError
    with a clear message — finish_fast_frame() catches it and falls back to
    the cv2 path permanently. Never silently return garbage."""
    monkeypatch.setattr(sv, "_VPI_BLEND_OPS", None)
    monkeypatch.setattr(sv, "_resolve_vpi_blend_ops", lambda: None)
    h = 64
    w = 128
    warped = np.zeros((h, w, 3), dtype=np.uint8)
    plan = type("FakePlan", (), {})()
    plan.fast_weight_left_bgr = np.full((h, w, 3), 128, dtype=np.uint8)
    plan.fast_weight_right_bgr = 255 - plan.fast_weight_left_bgr
    with pytest.raises(RuntimeError, match="VPI blend ops not available"):
        sv.vpi_blend_fast(warped, warped, plan)
