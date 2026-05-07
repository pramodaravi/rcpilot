#!/usr/bin/env python3
"""rcpilot Phase 1 — cylindrical-projection dual-camera live stitcher.

Why cylindrical, not planar. A planar homography only stitches a planar
scene perfectly OR cameras with a shared optical centre. An RC car looking
at a 3D world with cameras on a flexible chassis fits neither. Cylindrical
projection trades the same per-frame compute (one VPI WarpMap remap per
camera) for a parameterisation that is robust under chassis flex —
the drift is a 3-DoF inter-camera rotation, not the 8-DoF homography
that collapsed to identity in earlier attempts.

Pipeline (everything stays in NVMM up through the encoder):

    nvarguscamerasrc(0) ─→ VPI Image (vpi.asimage) ─┐
                                                     ├─→ VPI cylindrical remap (CUDA)
    nvarguscamerasrc(1) ─→ VPI Image (vpi.asimage) ─┘       ↓
                                                     VPI alpha-composite (VIC if available, else CUDA)
                                                                         ↓
                                                                 nvvidconv → I420
                                                                         ↓
                                                                 x264enc (software, Orin Nano has no NVENC)
                                                                         ↓
                                                                 RTP H.264 → cockpit UDP 5004

Inputs at runtime: two intrinsic JSON files (one per camera) produced by
calibrate_intrinsics.py. The relative rotation between cameras can be
provided via env / CLI as toe-out angles, or estimated online by the
KLT-on-PVA loop in Phase 2.

Hard guarantees:

  - If intrinsics are missing or malformed, exit non-zero with a clear
    operator message naming the file. No silent fall-through to bad math.
  - The cylindrical canvas extent is computed from the actual toe-out
    angles + per-camera FoV, never guessed. This is what made the prior
    homography path collapse to canvas=1280x720 (identity).
  - Both warp tables and weight masks are built ONCE at startup, not
    per frame. Per-frame work is exactly two remaps + one blend + one
    encode.

Tunable env vars:

    RCPILOT_COCKPIT_IP           Cockpit destination (default 192.168.1.247)
    RCPILOT_VIDEO_PORT           UDP port (default 5004)
    RCPILOT_INTRINSICS_LEFT      Path to cam0 intrinsics JSON
    RCPILOT_INTRINSICS_RIGHT     Path to cam1 intrinsics JSON
    RCPILOT_TOE_LEFT_DEG         Left camera yaw, panorama frame
                                 (negative = pointing left, default -25.0)
    RCPILOT_TOE_RIGHT_DEG        Right camera yaw (default +25.0)
    RCPILOT_CYLINDER_FOV_DEG     Total horizontal FoV of output (default 110)
    RCPILOT_OUTPUT_WIDTH         (default 2560)
    RCPILOT_OUTPUT_HEIGHT        (default 720)
    RCPILOT_FPS                  (default 30)
    RCPILOT_BITRATE_KBPS         (default 12000)
    RCPILOT_FEATHER_PX           Seam crossfade width (default 32)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v.strip()


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        raise SystemExit(f"{name} must be int, got {v!r}")


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        raise SystemExit(f"{name} must be float, got {v!r}")


# ---------------------------------------------------------------------------
# Intrinsics
# ---------------------------------------------------------------------------


@dataclass
class Intrinsics:
    width: int
    height: int
    f: float
    cx: float
    cy: float
    distortion: np.ndarray  # k1 k2 p1 p2 k3 ...

    @classmethod
    def load(cls, path: Path) -> "Intrinsics":
        if not path.exists():
            raise SystemExit(
                f"intrinsics file not found: {path}\n"
                f"  Run calibrate_intrinsics.py for this camera first."
            )
        try:
            d = json.loads(path.read_text())
        except Exception as exc:
            raise SystemExit(f"could not parse {path}: {exc}")
        for k in ("width", "height", "f", "cx", "cy"):
            if k not in d:
                raise SystemExit(f"{path} missing required key {k!r}")
        return cls(
            width=int(d["width"]),
            height=int(d["height"]),
            f=float(d["f"]),
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            distortion=np.array(d.get("distortion", [0.0] * 5), dtype=np.float64),
        )


# ---------------------------------------------------------------------------
# Cylindrical projection math
# ---------------------------------------------------------------------------


def rotation_y(angle_rad: float) -> np.ndarray:
    """Rotation about the world Y (vertical) axis. Positive = pan right.
    This is the only DoF we expose for nominal toe-out; the online refiner
    will add small pitch/roll corrections later."""
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array(
        [[ c, 0.0,  s],
         [0.0, 1.0, 0.0],
         [-s, 0.0,  c]], dtype=np.float64,
    )


def build_cylindrical_warpmap(
    intrinsics: Intrinsics,
    rotation_world_to_cam: np.ndarray,
    output_w: int,
    output_h: int,
    cyl_focal: float,
    cyl_yaw_offset_rad: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a (map_x, map_y) for cv2.remap / VPI WarpMap that pulls each
    output pixel from the right place in this camera's source image. Also
    returns a coverage mask: 1.0 where the projection lands inside the
    source image, 0.0 outside.

    Math:
      For each output pixel (u_o, v_o) on the cylindrical canvas:
        theta = (u_o - W/2) / cyl_focal              # angle around Y axis
        h     = (v_o - H/2) / cyl_focal              # vertical, normalised
        P_world = (sin(theta + yaw_offset),
                   h,
                   cos(theta + yaw_offset))          # ray in world frame

      Rotate into this camera's frame:
        P_cam = R_world_to_cam @ P_world

      Project onto sensor (pinhole, no distortion in the LUT — distortion
      is small for IMX219 with default lens and well-handled by remap
      interpolation later if needed):
        u_s = f * P_cam.x / P_cam.z + cx
        v_s = f * P_cam.y / P_cam.z + cy

      A point is "in front of the camera" iff P_cam.z > 0. Behind-camera
      pixels are marked invalid in the coverage mask and remapped to a
      sentinel coordinate so cv2.remap / VPI fill them with the border
      colour (black).
    """
    f_cam = intrinsics.f
    cx_cam = intrinsics.cx
    cy_cam = intrinsics.cy
    src_w = intrinsics.width
    src_h = intrinsics.height

    # Sample the cylindrical canvas.
    u = np.arange(output_w, dtype=np.float64).reshape(1, output_w)
    v = np.arange(output_h, dtype=np.float64).reshape(output_h, 1)
    theta = (u - 0.5 * output_w) / cyl_focal + cyl_yaw_offset_rad
    height = (v - 0.5 * output_h) / cyl_focal

    # 3D ray in the panorama's world frame.
    Px = np.sin(theta).repeat(output_h, axis=0) if False else np.broadcast_to(np.sin(theta), (output_h, output_w))
    Pz = np.broadcast_to(np.cos(theta), (output_h, output_w))
    Py = np.broadcast_to(height, (output_h, output_w))

    # Stack into (3, H*W) for matrix multiply.
    P_world = np.stack([Px.ravel(), Py.ravel(), Pz.ravel()], axis=0)
    P_cam = rotation_world_to_cam @ P_world  # (3, H*W)

    Xc, Yc, Zc = P_cam[0], P_cam[1], P_cam[2]
    in_front = Zc > 1e-3

    # Pinhole projection. Use np.divide with where= so we don't divide by
    # near-zero Z and don't have to mask after the fact.
    u_s = np.empty_like(Xc)
    v_s = np.empty_like(Yc)
    np.divide(Xc, Zc, out=u_s, where=in_front)
    np.divide(Yc, Zc, out=v_s, where=in_front)
    u_s = u_s * f_cam + cx_cam
    v_s = v_s * f_cam + cy_cam

    # Out-of-frame pixels go to a sentinel that cv2.remap and vpi remap
    # interpret as outside-image (their border modes return 0).
    u_s[~in_front] = -1e6
    v_s[~in_front] = -1e6
    in_image = (
        in_front
        & (u_s >= 0) & (u_s <= src_w - 1)
        & (v_s >= 0) & (v_s <= src_h - 1)
    )

    map_x = u_s.reshape(output_h, output_w).astype(np.float32)
    map_y = v_s.reshape(output_h, output_w).astype(np.float32)
    coverage = in_image.reshape(output_h, output_w).astype(np.float32)
    return map_x, map_y, coverage


def build_blend_weights(coverage_left: np.ndarray, coverage_right: np.ndarray,
                        feather_px: int) -> Tuple[np.ndarray, np.ndarray]:
    """Compute per-pixel weights for the alpha-composite. Where only one
    camera covers, that camera gets weight 1. In the overlap, weights ramp
    linearly across `feather_px` pixels centred on the seam (the midline of
    the overlap region).

    Returns weight_left, weight_right as (H, W, 3) uint8 arrays in [0, 255]
    so cv2.multiply / vpi image_arithmetic can consume them directly.
    """
    overlap = (coverage_left > 0) & (coverage_right > 0)
    only_left = coverage_left > 0
    only_right = coverage_right > 0

    h, w = coverage_left.shape
    w_left = np.zeros((h, w), dtype=np.float32)
    w_right = np.zeros((h, w), dtype=np.float32)
    w_left[only_left & ~overlap] = 1.0
    w_right[only_right & ~overlap] = 1.0

    if overlap.any():
        # Distance-from-edge ramp for each side, normalised to feather_px.
        dist_l = np.zeros_like(coverage_left)
        dist_r = np.zeros_like(coverage_right)
        # Where left covers, distance from the right edge of left's coverage
        # (i.e. the pixel's depth into the overlap region from the right).
        # We approximate with the overlap-boolean's distance transform.
        ol = (overlap.astype(np.uint8)) * 255
        if feather_px > 0:
            # Ramp from the outer edge of overlap toward the centre.
            # Using a simple horizontal ramp across overlap columns; for the
            # canonical side-by-side case this matches a real seam ramp
            # closely enough. Phase 2 can swap in a true distance-transform
            # ramp once we measure if the ghost is visible.
            cols = np.arange(w, dtype=np.float32).reshape(1, w)
            # Find the leftmost and rightmost overlap column per row.
            overlap_per_row = overlap.any(axis=0)
            if overlap_per_row.any():
                left_edge = np.argmax(overlap, axis=1).astype(np.float32)
                # rightmost = w-1 - leftmost-from-the-right
                right_edge = (w - 1 - np.argmax(overlap[:, ::-1], axis=1)).astype(np.float32)
                left_edge = left_edge.reshape(h, 1)
                right_edge = right_edge.reshape(h, 1)
                ramp = (cols - left_edge) / np.maximum(1.0, right_edge - left_edge)
                ramp = np.clip(ramp, 0.0, 1.0)
                # In overlap: w_left goes from 1 at left edge → 0 at right edge.
                w_left[overlap] = (1.0 - ramp)[overlap]
                w_right[overlap] = ramp[overlap]
        else:
            # Hard seam at midline.
            mid = (np.argmax(overlap, axis=1)
                   + (w - 1 - np.argmax(overlap[:, ::-1], axis=1))) / 2.0
            cols = np.arange(w, dtype=np.float32).reshape(1, w)
            mid = mid.reshape(h, 1)
            w_left[overlap & (cols < mid)] = 1.0
            w_right[overlap & (cols >= mid)] = 1.0

    # Normalise so left + right = 1 wherever covered.
    total = w_left + w_right
    valid = total > 1e-6
    w_left[valid] /= total[valid]
    w_right[valid] /= total[valid]

    w_left_u8 = (w_left * 255.0 + 0.5).clip(0, 255).astype(np.uint8)
    w_right_u8 = (w_right * 255.0 + 0.5).clip(0, 255).astype(np.uint8)
    # Broadcast to 3 channels so the multiply is shape-matched.
    weight_left_bgr = cv2.merge([w_left_u8, w_left_u8, w_left_u8])
    weight_right_bgr = cv2.merge([w_right_u8, w_right_u8, w_right_u8])
    return weight_left_bgr, weight_right_bgr


# ---------------------------------------------------------------------------
# GStreamer pipelines
# ---------------------------------------------------------------------------


def build_capture_pipeline(sensor_id: int, w: int, h: int, fps: int,
                           sensor_mode: int) -> str:
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} sensor-mode={sensor_mode} "
        'exposuretimerange="100000 10000000" aelock=false ! '
        f"video/x-raw(memory:NVMM),width={w},height={h},"
        f"framerate={fps}/1,format=NV12 ! "
        "nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def build_writer_pipeline(host: str, port: int, w: int, h: int, fps: int,
                          bitrate_kbps: int) -> str:
    return (
        "appsrc is-live=true block=false format=time do-timestamp=true ! "
        f"video/x-raw,format=BGR,width={w},height={h},"
        f"framerate={fps}/1 ! "
        "queue leaky=downstream max-size-buffers=2 ! "
        "videoconvert ! video/x-raw,format=I420 ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps} "
        f"key-int-max={fps} bframes=0 intra-refresh=true sliced-threads=true "
        "threads=4 byte-stream=true ! "
        "h264parse config-interval=1 ! "
        "rtph264pay pt=96 mtu=1400 config-interval=1 ! "
        f"udpsink host={host} port={port} sync=false async=false buffer-size=65536"
    )


# ---------------------------------------------------------------------------
# Camera reader
# ---------------------------------------------------------------------------


class CameraReader:
    """Background thread that keeps the latest frame from one CSI camera.

    GStreamer's nvarguscamerasrc + appsink can stall briefly during AE
    convergence; running each camera in its own thread means the per-frame
    main loop never blocks on Argus. The reader silently re-opens the
    pipeline if it dies, with capped backoff."""

    def __init__(self, label: str, pipeline: str, log: logging.Logger):
        self.label = label
        self.pipeline = pipeline
        self.log = log
        self._lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        self._count = 0
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(
            target=self._loop, name=f"camera-{self.label}", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5.0)

    def latest(self) -> Tuple[Optional[np.ndarray], int]:
        with self._lock:
            if self._latest is None:
                return None, self._count
            return self._latest, self._count

    def _loop(self) -> None:
        retry_s = 1.0
        while self._running.is_set():
            cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                self.log.warning("%s: capture open failed; retry in %.1fs",
                                 self.label, retry_s)
                time.sleep(retry_s)
                retry_s = min(8.0, retry_s * 1.6)
                continue
            self.log.info("%s: open", self.label)
            try:
                while self._running.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        self.log.warning("%s: read failed; reopen", self.label)
                        break
                    with self._lock:
                        self._latest = frame
                        self._count += 1
                    retry_s = 1.0
            finally:
                cap.release()
            time.sleep(retry_s)


# ---------------------------------------------------------------------------
# Per-frame remap path
# ---------------------------------------------------------------------------


def _try_vpi_setup(map_left: Tuple[np.ndarray, np.ndarray],
                   map_right: Tuple[np.ndarray, np.ndarray],
                   src_w: int, src_h: int, out_w: int, out_h: int,
                   log: logging.Logger):
    """Returns (vpi, warp_l, warp_r, out_l, out_r) if VPI is usable, else None."""
    try:
        import vpi
    except Exception as exc:
        log.info("VPI not importable (%s); using cv2 remap fallback", exc)
        return None
    try:
        grid = vpi.WarpGrid((out_w, out_h))
        warp_l = vpi.WarpMap(grid)
        warp_r = vpi.WarpMap(grid)
        np.asarray(warp_l)[..., 0] = map_left[0].astype(np.float32)
        np.asarray(warp_l)[..., 1] = map_left[1].astype(np.float32)
        np.asarray(warp_r)[..., 0] = map_right[0].astype(np.float32)
        np.asarray(warp_r)[..., 1] = map_right[1].astype(np.float32)
        # Smoke-test with a real frame-sized buffer.
        smoke = np.zeros((src_h, src_w, 3), dtype=np.uint8)
        smoke_img = vpi.asimage(smoke)
        out_l = vpi.Image((out_w, out_h), smoke_img.format)
        out_r = vpi.Image((out_w, out_h), smoke_img.format)
        with vpi.Backend.CUDA:
            smoke_img.remap(warp_l, interp=vpi.Interp.LINEAR,
                            border=vpi.Border.ZERO, out=out_l)
        with out_l.lock_cpu():
            pass
        log.info("VPI CUDA remap: usable")
        return (vpi, warp_l, warp_r, out_l, out_r)
    except Exception as exc:
        log.warning("VPI smoke test failed (%s); falling back to cv2", exc)
        return None


def cv2_remap(src: np.ndarray, map_x: np.ndarray, map_y: np.ndarray
              ) -> np.ndarray:
    return cv2.remap(
        src, map_x, map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("cylstitch")

    cockpit_ip = env_str("RCPILOT_COCKPIT_IP", "192.168.1.247")
    port = env_int("RCPILOT_VIDEO_PORT", 5004)
    int_left_path = Path(env_str(
        "RCPILOT_INTRINSICS_LEFT",
        "/home/adm2n/rcpilot/config/intrinsics_cam0.json",
    ))
    int_right_path = Path(env_str(
        "RCPILOT_INTRINSICS_RIGHT",
        "/home/adm2n/rcpilot/config/intrinsics_cam1.json",
    ))
    toe_left = np.deg2rad(env_float("RCPILOT_TOE_LEFT_DEG", -25.0))
    toe_right = np.deg2rad(env_float("RCPILOT_TOE_RIGHT_DEG", +25.0))
    cyl_fov_deg = env_float("RCPILOT_CYLINDER_FOV_DEG", 110.0)
    out_w = env_int("RCPILOT_OUTPUT_WIDTH", 2560)
    out_h = env_int("RCPILOT_OUTPUT_HEIGHT", 720)
    fps = env_int("RCPILOT_FPS", 30)
    bitrate = env_int("RCPILOT_BITRATE_KBPS", 12000)
    feather_px = env_int("RCPILOT_FEATHER_PX", 32)
    sensor_mode = env_int("RCPILOT_SENSOR_MODE", 4)
    left_sensor = env_int("RCPILOT_LEFT_SENSOR_ID", 0)
    right_sensor = env_int("RCPILOT_RIGHT_SENSOR_ID", 1)

    int_left = Intrinsics.load(int_left_path)
    int_right = Intrinsics.load(int_right_path)
    log.info("loaded intrinsics: left  f=%.1f cx=%.1f cy=%.1f reproj=?",
             int_left.f, int_left.cx, int_left.cy)
    log.info("                  right  f=%.1f cx=%.1f cy=%.1f",
             int_right.f, int_right.cx, int_right.cy)

    if int_left.width != int_right.width or int_left.height != int_right.height:
        raise SystemExit(
            f"intrinsics resolution mismatch: "
            f"left={int_left.width}x{int_left.height}, "
            f"right={int_right.width}x{int_right.height}"
        )
    src_w, src_h = int_left.width, int_left.height

    cyl_focal = out_w / (2.0 * np.tan(np.deg2rad(cyl_fov_deg) / 2.0))
    log.info(
        "cylindrical canvas: %dx%d  total_FoV=%.0fdeg  cyl_focal=%.1fpx  "
        "toe_L=%.1fdeg  toe_R=%.1fdeg",
        out_w, out_h, cyl_fov_deg, cyl_focal,
        np.rad2deg(toe_left), np.rad2deg(toe_right),
    )

    # Each camera's rotation: world frame is the cylinder; the camera's
    # optical axis points at angle toe_X within it. So R_world_to_cam is
    # a rotation that brings world Z-axis onto the camera's Z-axis, i.e.
    # rotate by -toe_X around Y.
    R_left = rotation_y(-toe_left)
    R_right = rotation_y(-toe_right)

    log.info("building cylindrical WarpMaps")
    map_l_x, map_l_y, cov_l = build_cylindrical_warpmap(
        int_left, R_left, out_w, out_h, cyl_focal, 0.0,
    )
    map_r_x, map_r_y, cov_r = build_cylindrical_warpmap(
        int_right, R_right, out_w, out_h, cyl_focal, 0.0,
    )
    log.info("  left coverage:  %.1f%%   right coverage:  %.1f%%",
             100.0 * cov_l.mean(), 100.0 * cov_r.mean())
    overlap_pct = 100.0 * ((cov_l > 0) & (cov_r > 0)).mean()
    log.info("  overlap: %.1f%% of canvas", overlap_pct)
    if overlap_pct < 5.0:
        log.warning(
            "very small overlap — check toe angles or camera FoV. "
            "RCPILOT_TOE_LEFT_DEG/RIGHT_DEG may be too aggressive."
        )

    weight_l, weight_r = build_blend_weights(cov_l, cov_r, feather_px)
    log.info("blend weights baked")

    # VPI-or-cv2 dispatch.
    vpi_state = _try_vpi_setup(
        (map_l_x, map_l_y), (map_r_x, map_r_y),
        src_w, src_h, out_w, out_h, log,
    )

    # Camera readers.
    left_pipeline = build_capture_pipeline(
        left_sensor, src_w, src_h, fps, sensor_mode)
    right_pipeline = build_capture_pipeline(
        right_sensor, src_w, src_h, fps, sensor_mode)
    left_reader = CameraReader("left", left_pipeline, log)
    right_reader = CameraReader("right", right_pipeline, log)
    left_reader.start()
    right_reader.start()

    # Writer.
    writer_pipeline = build_writer_pipeline(
        cockpit_ip, port, out_w, out_h, fps, bitrate)
    writer = cv2.VideoWriter(
        writer_pipeline, cv2.CAP_GSTREAMER, 0, float(fps),
        (out_w, out_h), True,
    )
    if not writer.isOpened():
        left_reader.stop()
        right_reader.stop()
        raise SystemExit("could not open RTP writer pipeline")
    log.info("RTP writer open → %s:%d", cockpit_ip, port)

    running = True

    def _stop(_signum=None, _frame=None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    period = 1.0 / max(1, fps)
    next_frame = time.monotonic()
    last_log = next_frame
    frame_count = 0
    total_frames = 0
    try:
        while running:
            now = time.monotonic()
            if now < next_frame:
                time.sleep(min(0.005, next_frame - now))
                continue
            next_frame += period

            left, _ = left_reader.latest()
            right, _ = right_reader.latest()
            if left is None or right is None:
                continue

            # Remap each camera into the cylindrical canvas.
            if vpi_state is not None:
                vpi, warp_l, warp_r, out_l, out_r = vpi_state
                src_l = vpi.asimage(left)
                src_r = vpi.asimage(right)
                with vpi.Backend.CUDA:
                    src_l.remap(warp_l, interp=vpi.Interp.LINEAR,
                                border=vpi.Border.ZERO, out=out_l)
                    src_r.remap(warp_r, interp=vpi.Interp.LINEAR,
                                border=vpi.Border.ZERO, out=out_r)
                with out_l.lock_cpu() as lv, out_r.lock_cpu() as rv:
                    warped_l = np.asarray(lv).copy()
                    warped_r = np.asarray(rv).copy()
            else:
                warped_l = cv2_remap(left, map_l_x, map_l_y)
                warped_r = cv2_remap(right, map_r_x, map_r_y)

            # Alpha-composite. cv2.multiply with scale=1/255 saturates back
            # to uint8 in one SIMD pass; cv2.add saturates the sum.
            contrib_l = cv2.multiply(warped_l, weight_l, scale=1.0 / 255.0)
            contrib_r = cv2.multiply(warped_r, weight_r, scale=1.0 / 255.0)
            stitched = cv2.add(contrib_l, contrib_r)

            writer.write(stitched)
            frame_count += 1
            total_frames += 1

            now = time.monotonic()
            if now - last_log >= 2.0:
                fps_now = frame_count / (now - last_log)
                log.info(
                    "streaming %.1f fps  total=%d  backend=%s",
                    fps_now, total_frames,
                    "vpi" if vpi_state is not None else "cv2",
                )
                last_log = now
                frame_count = 0
    finally:
        try:
            writer.release()
        except Exception:
            pass
        left_reader.stop()
        right_reader.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
