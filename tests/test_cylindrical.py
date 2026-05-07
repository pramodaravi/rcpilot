"""Unit tests for cylindrical_stitcher math.

These don't require a Jetson, VPI, or live cameras. They pin the projection
maths so future edits can't silently regress the canvas geometry the way
the prior planar-homography path did.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "jetson"))

import cylindrical_stitcher as cs  # noqa: E402


def _typical_intrinsics(width=1280, height=720) -> cs.Intrinsics:
    """IMX219 with the stock V1 lens at 1280x720 — f≈1100px, principal point
    near image centre, mild radial distortion. Numbers approximate; this is
    enough for math tests, not a calibration substitute."""
    return cs.Intrinsics(
        width=width, height=height,
        f=1100.0, cx=width / 2.0, cy=height / 2.0,
        distortion=np.zeros(5, dtype=np.float64),
    )


# ---------------------------------------------------------------------------
# rotation_y
# ---------------------------------------------------------------------------


def test_rotation_y_zero_is_identity():
    assert np.allclose(cs.rotation_y(0.0), np.eye(3))


def test_rotation_y_90deg_swaps_x_and_z():
    R = cs.rotation_y(np.deg2rad(90.0))
    # (1, 0, 0) maps to (0, 0, -1)
    out = R @ np.array([1.0, 0.0, 0.0])
    assert np.allclose(out, [0.0, 0.0, -1.0], atol=1e-9)


# ---------------------------------------------------------------------------
# build_cylindrical_warpmap
# ---------------------------------------------------------------------------


def test_warpmap_shape_matches_output_dimensions():
    intr = _typical_intrinsics()
    R = np.eye(3)
    map_x, map_y, cov = cs.build_cylindrical_warpmap(
        intr, R, output_w=2560, output_h=720,
        cyl_focal=1500.0, cyl_yaw_offset_rad=0.0,
    )
    assert map_x.shape == (720, 2560)
    assert map_y.shape == (720, 2560)
    assert cov.shape == (720, 2560)
    assert map_x.dtype == np.float32
    assert map_y.dtype == np.float32


def test_warpmap_centre_pixel_maps_to_camera_centre_when_aligned():
    """At the cylinder's centre and with R=identity, the central output pixel
    should sample from the camera's principal point."""
    intr = _typical_intrinsics()
    out_w, out_h = 2560, 720
    cyl_focal = 1500.0
    map_x, map_y, _ = cs.build_cylindrical_warpmap(
        intr, np.eye(3), out_w, out_h, cyl_focal, cyl_yaw_offset_rad=0.0,
    )
    # The central output pixel corresponds to theta=0, h=0 → ray (0, 0, 1)
    # which projects to (cx, cy) on the source.
    cy_centre, cx_centre = out_h // 2, out_w // 2
    assert abs(map_x[cy_centre, cx_centre] - intr.cx) < 1.0
    assert abs(map_y[cy_centre, cx_centre] - intr.cy) < 1.0


def test_warpmap_coverage_is_zero_behind_camera():
    """A camera rotated 180° away from the panorama centre should have
    coverage 0 in the canvas centre — every ray points behind it."""
    intr = _typical_intrinsics()
    R = cs.rotation_y(np.deg2rad(180.0))
    out_w, out_h = 2560, 720
    cyl_focal = 1500.0
    _, _, cov = cs.build_cylindrical_warpmap(
        intr, R, out_w, out_h, cyl_focal, cyl_yaw_offset_rad=0.0,
    )
    # At least the central pixel should be uncovered (camera looks backward).
    assert cov[out_h // 2, out_w // 2] == 0.0


def test_warpmap_yaw_offset_shifts_centre():
    """If we set cyl_yaw_offset to +30°, the cylinder samples from a ray
    pointing 30° to the right of the camera. The mapped source pixel for
    the central output should move toward the right edge of the source."""
    intr = _typical_intrinsics()
    R = np.eye(3)
    out_w, out_h = 2560, 720
    cyl_focal = 1500.0
    map_x_zero, _, _ = cs.build_cylindrical_warpmap(
        intr, R, out_w, out_h, cyl_focal, cyl_yaw_offset_rad=0.0,
    )
    map_x_off, _, _ = cs.build_cylindrical_warpmap(
        intr, R, out_w, out_h, cyl_focal,
        cyl_yaw_offset_rad=np.deg2rad(15.0),
    )
    # Centre of canvas now samples a ray 15° right of camera's optical
    # axis → projects to a column right of cx.
    centre_v, centre_u = out_h // 2, out_w // 2
    assert map_x_off[centre_v, centre_u] > map_x_zero[centre_v, centre_u]


# ---------------------------------------------------------------------------
# build_blend_weights
# ---------------------------------------------------------------------------


def test_blend_weights_sum_to_one_in_overlap():
    h, w = 100, 400
    cov_l = np.zeros((h, w), dtype=np.float32)
    cov_r = np.zeros((h, w), dtype=np.float32)
    # Left covers cols 0..299, right covers cols 100..399. Overlap = 100..299.
    cov_l[:, :300] = 1.0
    cov_r[:, 100:] = 1.0
    wl, wr = cs.build_blend_weights(cov_l, cov_r, feather_px=8)
    # Convert back to 0..1 floats.
    fl = wl[..., 0].astype(np.float32) / 255.0
    fr = wr[..., 0].astype(np.float32) / 255.0
    covered = (cov_l > 0) | (cov_r > 0)
    sums = fl[covered] + fr[covered]
    assert (sums > 0.95).all()
    assert (sums < 1.05).all()


def test_blend_weights_solo_columns_have_full_weight():
    h, w = 100, 400
    cov_l = np.zeros((h, w), dtype=np.float32)
    cov_r = np.zeros((h, w), dtype=np.float32)
    cov_l[:, :300] = 1.0
    cov_r[:, 100:] = 1.0
    wl, wr = cs.build_blend_weights(cov_l, cov_r, feather_px=8)
    # Columns 0..99 are left-only.
    assert (wl[:, 50, 0] > 250).all()
    assert (wr[:, 50, 0] < 5).all()
    # Columns 300..399 are right-only.
    assert (wl[:, 350, 0] < 5).all()
    assert (wr[:, 350, 0] > 250).all()


# ---------------------------------------------------------------------------
# Intrinsics.load
# ---------------------------------------------------------------------------


def test_intrinsics_load_round_trip(tmp_path):
    payload = {
        "width": 1280, "height": 720,
        "f": 1100.0, "cx": 632.1, "cy": 358.4,
        "distortion": [0.01, -0.02, 0.0, 0.0, 0.0],
    }
    p = tmp_path / "intrinsics.json"
    p.write_text(json.dumps(payload))
    intr = cs.Intrinsics.load(p)
    assert intr.width == 1280 and intr.height == 720
    assert abs(intr.f - 1100.0) < 1e-6
    assert intr.distortion.shape == (5,)


def test_intrinsics_load_missing_key_exits(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"width": 1280}))  # missing height/f/cx/cy
    with pytest.raises(SystemExit):
        cs.Intrinsics.load(p)


def test_intrinsics_load_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        cs.Intrinsics.load(tmp_path / "does_not_exist.json")
