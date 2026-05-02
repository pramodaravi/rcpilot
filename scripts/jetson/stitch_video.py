#!/usr/bin/env python3
"""Jetson-side two-camera panoramic stitcher.

This produces ONE RTP/H.264 video stream for Unity. It is a real image stitch:
feature-based alignment, perspective warp, and feather blending. The alignment
is estimated once at startup and cached, because the two cameras are rigidly
mounted on the car.

The Jetson's useful acceleration here is its camera ISP, GStreamer/NVIDIA video
path, and optional OpenCV CUDA warp if the installed OpenCV build exposes it.
The DLA/TOPS block is for neural inference; this stitcher avoids hallucinating
missing scene content and only blends pixels the cameras actually see.
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


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from exc


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_capture_pipeline(sensor_id: int, width: int, height: int,
                           fps: int, sensor_mode: int) -> str:
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} sensor-mode={sensor_mode} "
        'exposuretimerange="100000 10000000" aelock=false ! '
        f"video/x-raw(memory:NVMM),width={width},height={height},"
        f"framerate={fps}/1,format=NV12 ! "
        "nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def build_writer_pipeline(host: str, port: int, width: int, height: int,
                          fps: int, bitrate_kbps: int, encoder: str,
                          key_interval: int, x264_preset: str) -> str:
    common_prefix = (
        "appsrc is-live=true block=false format=time do-timestamp=true ! "
        f"video/x-raw,format=BGR,width={width},height={height},"
        f"framerate={fps}/1 ! "
        "queue leaky=downstream max-size-buffers=2 ! "
    )
    common_suffix = (
        " ! h264parse config-interval=1"
        " ! rtph264pay pt=96 mtu=1400 config-interval=1"
        f" ! udpsink host={host} port={port} sync=false async=false "
        "buffer-size=65536"
    )

    if encoder == "x264enc":
        encode = (
            "videoconvert ! video/x-raw,format=I420"
            f" ! x264enc tune=zerolatency speed-preset={x264_preset}"
            f" bitrate={bitrate_kbps} key-int-max={key_interval}"
            " bframes=0 intra-refresh=true sliced-threads=true"
            " threads=4 byte-stream=true"
        )
    elif encoder == "nvv4l2h264enc":
        bitrate_bps = bitrate_kbps * 1000
        encode = (
            "videoconvert ! video/x-raw,format=I420"
            f" ! nvvidconv ! video/x-raw(memory:NVMM),format=NV12,"
            f"width={width},height={height},framerate={fps}/1"
            " ! nvv4l2h264enc maxperf-enable=1"
            f" bitrate={bitrate_bps} iframeinterval={key_interval}"
            " insert-sps-pps=1 control-rate=1"
        )
    else:
        raise SystemExit(
            f"RCPILOT_ENCODER must be x264enc or nvv4l2h264enc, got {encoder!r}"
        )

    return common_prefix + encode + common_suffix


class CameraReader:
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
            target=self._loop, name=f"camera-{self.label}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1.0)

    def latest(self) -> Tuple[Optional[np.ndarray], int]:
        with self._lock:
            if self._latest is None:
                return None, self._count
            return self._latest.copy(), self._count

    def wait_frame(self, timeout_s: float) -> Optional[np.ndarray]:
        deadline = time.monotonic() + timeout_s
        last_count = -1
        while time.monotonic() < deadline:
            frame, count = self.latest()
            if frame is not None and count != last_count:
                return frame
            time.sleep(0.01)
        return None

    def _loop(self) -> None:
        while self._running.is_set():
            cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                self.log.warning("%s camera open failed; retrying", self.label)
                time.sleep(1.0)
                continue
            self.log.info("%s camera open", self.label)
            try:
                while self._running.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        self.log.warning("%s camera read failed; reopening", self.label)
                        break
                    with self._lock:
                        self._latest = frame
                        self._count += 1
            finally:
                cap.release()
            time.sleep(0.25)


@dataclass
class HomographyResult:
    h_right_to_left: np.ndarray
    inliers: int
    matches: int
    detector: str

    @property
    def ratio(self) -> float:
        return self.inliers / max(1, self.matches)


def estimate_homography(left: np.ndarray, right: np.ndarray,
                        feature_scale: float, min_matches: int,
                        log: logging.Logger) -> Optional[HomographyResult]:
    scale = float(np.clip(feature_scale, 0.25, 1.0))
    left_small = cv2.resize(left, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
    right_small = cv2.resize(right, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_AREA)
    left_gray = cv2.cvtColor(left_small, cv2.COLOR_BGR2GRAY)
    right_gray = cv2.cvtColor(right_small, cv2.COLOR_BGR2GRAY)

    detector_name = "ORB"
    if hasattr(cv2, "SIFT_create"):
        detector = cv2.SIFT_create(nfeatures=2500)
        detector_name = "SIFT"
    else:
        detector = cv2.ORB_create(nfeatures=3000, fastThreshold=12)

    kp_l, des_l = detector.detectAndCompute(left_gray, None)
    kp_r, des_r = detector.detectAndCompute(right_gray, None)
    if des_l is None or des_r is None or len(kp_l) < 8 or len(kp_r) < 8:
        log.warning("not enough features: left=%d right=%d", len(kp_l), len(kp_r))
        return None

    if detector_name == "SIFT":
        matcher = cv2.BFMatcher(cv2.NORM_L2)
    else:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    raw = matcher.knnMatch(des_r, des_l, k=2)
    good = []
    for pair in raw:
        if len(pair) != 2:
            continue
        m, n = pair
        if m.distance < 0.74 * n.distance:
            good.append(m)

    if len(good) < min_matches:
        log.warning("not enough stitch matches: %d < %d", len(good), min_matches)
        return None

    pts_r = np.float32([kp_r[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_l = np.float32([kp_l[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    h_small, mask = cv2.findHomography(
        pts_r, pts_l, cv2.RANSAC, ransacReprojThreshold=4.0
    )
    if h_small is None or mask is None:
        log.warning("homography solve failed")
        return None

    inliers = int(mask.ravel().sum())
    if inliers < min_matches:
        log.warning("not enough stitch inliers: %d < %d", inliers, min_matches)
        return None

    s = np.array([[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]])
    h_full = np.linalg.inv(s) @ h_small @ s
    h_full = h_full / h_full[2, 2]
    return HomographyResult(h_full.astype(np.float64), inliers, len(good), detector_name)


def approximate_homography(width: int, overlap_px: int) -> np.ndarray:
    # Places the right camera to the right with an overlap. This is a fallback,
    # not a true stitch, but still produces one blended widescreen frame.
    x = max(1, width - max(0, overlap_px))
    return np.array([[1.0, 0.0, float(x)], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


@dataclass
class StitchPlan:
    h_canvas: np.ndarray
    left_x: int
    left_y: int
    canvas_w: int
    canvas_h: int
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    left_weight: np.ndarray
    right_weight: np.ndarray
    overlap_mask: np.ndarray
    overlap_pixels: int
    # Raw per-camera coverage at canvas resolution, captured BEFORE feather
    # narrowing snaps weights to 1/0 — needed by the seam-finder so it knows
    # the *full* width of the overlap, not just the narrow feather band.
    raw_left_coverage: np.ndarray
    raw_right_coverage: np.ndarray
    exposure_match: bool
    exposure_alpha: float
    exposure_gain: np.ndarray
    use_cuda: bool

    # ---- fast-path pre-baked tables (built by bake_fast_path) ----------
    # Once-per-startup remap tables that fuse warp + crop + resize so the
    # per-frame work is just two cv2.remap calls + one vectorized uint8
    # blend. Set lazily — None until bake_fast_path() runs.
    fast_map_left_x: Optional[np.ndarray] = None
    fast_map_left_y: Optional[np.ndarray] = None
    fast_map_right_x: Optional[np.ndarray] = None
    fast_map_right_y: Optional[np.ndarray] = None
    # Output-resolution 3-channel weights as uint8 [0..255], so cv2.multiply
    # against a BGR frame is one shape-matched SIMD op (no broadcast).
    fast_weight_left_bgr: Optional[np.ndarray] = None
    fast_weight_right_bgr: Optional[np.ndarray] = None
    # Single-channel uint8 mask of the overlap region at output resolution,
    # used to sample exposure cheaply without re-warping.
    fast_overlap_mask_out: Optional[np.ndarray] = None
    # How often to refresh the exposure gain in fast mode (frames).
    fast_exposure_every_n: int = 15

    # ---- seam-cut state (built by bake_fast_path, mutated each frame) -----
    # Per-row [first_col, last_col+1) range of overlap pixels for each output
    # row. Outside this range, the row is single-camera; the dynamic seam is
    # only computed within these columns.
    fast_overlap_row_lo: Optional[np.ndarray] = None  # (out_h,) int32
    fast_overlap_row_hi: Optional[np.ndarray] = None  # (out_h,) int32
    # Bounding box of the overlap region in output coords.
    fast_overlap_xmin: int = 0
    fast_overlap_xmax: int = 0
    # Single-channel uint8 mask: 255 where the OUTPUT pixel is covered by at
    # least one camera (so we can blank uncovered pixels to black at the end).
    fast_covered_mask: Optional[np.ndarray] = None
    # Single-channel uint8 [0..255] = baseline left weight, valid OUTSIDE the
    # overlap band only. Inside the overlap, the per-frame seam logic writes
    # the weight directly. Outside, this is 255 in left-only region, 0 in
    # right-only, 0 in uncovered.
    fast_baseline_left_u8: Optional[np.ndarray] = None
    # Last frame's seam columns, kept for temporal smoothing.
    fast_seam_prev: Optional[np.ndarray] = None  # (out_h,) int32
    # How aggressively to smooth the seam frame-to-frame. 0 = no smoothing
    # (latest seam every frame), 1 = freeze (never update). Default 0.6 →
    # ~3-frame settling time, plenty fast for hand motion at 30fps.
    fast_seam_smooth: float = 0.6


def find_clean_crop(mask: np.ndarray,
                    min_edge_coverage: float) -> Tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        raise SystemExit("stitch coverage mask is empty")

    x0 = int(xs.min())
    x1 = int(xs.max()) + 1
    y0 = int(ys.min())
    y1 = int(ys.max()) + 1
    original = (x0, y0, x1 - x0, y1 - y0)
    min_edge_coverage = float(np.clip(min_edge_coverage, 0.80, 1.0))

    # Trim one border pixel at a time until the visible crop has no obvious
    # black wedge along its edges. This runs once at startup, not per frame.
    while x1 - x0 > 16 and y1 - y0 > 16:
        window = mask[y0:y1, x0:x1] > 0
        edge_scores = {
            "top": float(window[0, :].mean()),
            "bottom": float(window[-1, :].mean()),
            "left": float(window[:, 0].mean()),
            "right": float(window[:, -1].mean()),
        }
        edge, score = min(edge_scores.items(), key=lambda item: item[1])
        if score >= min_edge_coverage:
            break
        if edge == "top":
            y0 += 1
        elif edge == "bottom":
            y1 -= 1
        elif edge == "left":
            x0 += 1
        else:
            x1 -= 1

    if x1 - x0 < original[2] * 0.70 or y1 - y0 < original[3] * 0.70:
        return original
    return x0, y0, x1 - x0, y1 - y0


def make_plan(h_right_to_left: np.ndarray, width: int, height: int,
              max_canvas_w: int, max_canvas_h: int, use_cuda: bool,
              log: logging.Logger) -> StitchPlan:
    left_corners = np.float32(
        [[0, 0], [width, 0], [width, height], [0, height]]
    ).reshape(-1, 1, 2)
    right_corners = cv2.perspectiveTransform(left_corners, h_right_to_left)
    all_corners = np.concatenate([left_corners, right_corners], axis=0)
    min_xy = np.floor(all_corners.min(axis=0).ravel()).astype(int)
    max_xy = np.ceil(all_corners.max(axis=0).ravel()).astype(int)
    tx = -int(min_xy[0])
    ty = -int(min_xy[1])
    canvas_w = int(max_xy[0] - min_xy[0])
    canvas_h = int(max_xy[1] - min_xy[1])

    if canvas_w < width or canvas_w > max_canvas_w or canvas_h > max_canvas_h:
        if env_bool("RCPILOT_STITCH_ALLOW_FALLBACK", False):
            log.warning(
                "estimated canvas %dx%d is unreasonable; using fallback overlap",
                canvas_w, canvas_h,
            )
            h_right_to_left = approximate_homography(width, overlap_px=180)
            return make_plan(
                h_right_to_left, width, height, max_canvas_w, max_canvas_h,
                use_cuda, log,
            )
        raise SystemExit(
            f"Estimated stitch canvas {canvas_w}x{canvas_h} is unreasonable. "
            "Not streaming a fake split view. Re-aim cameras with more overlap "
            "or recalibrate against a textured scene."
        )

    translate = np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])
    h_canvas = translate @ h_right_to_left

    left_mask = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    right_src_mask = np.full((height, width), 255, dtype=np.uint8)
    left_mask[ty:ty + height, tx:tx + width] = 255
    right_mask = cv2.warpPerspective(
        right_src_mask, h_canvas, (canvas_w, canvas_h), flags=cv2.INTER_NEAREST
    )

    left_distance = cv2.distanceTransform(left_mask, cv2.DIST_L2, 3)
    right_distance = cv2.distanceTransform(right_mask, cv2.DIST_L2, 3)
    total = left_distance + right_distance
    covered = total > 0.0
    left_weight = np.zeros_like(left_distance, dtype=np.float32)
    right_weight = np.zeros_like(right_distance, dtype=np.float32)
    left_weight[covered] = left_distance[covered] / total[covered]
    right_weight[covered] = right_distance[covered] / total[covered]

    # Pixels covered by only one camera should be copied exactly.
    only_left = (left_mask > 0) & (right_mask == 0)
    only_right = (right_mask > 0) & (left_mask == 0)
    left_weight[only_left] = 1.0
    right_weight[only_left] = 0.0
    left_weight[only_right] = 0.0
    right_weight[only_right] = 1.0

    # Optional narrow-feather pass. Distance-transform feathering produces a
    # very wide blend zone, which makes parallax ghosting visible across the
    # whole overlap. Setting RCPILOT_STITCH_FEATHER_PX clips the soft band
    # to that many pixels around the seam (where left_distance == right_distance)
    # and snaps everything outside the band to the nearer camera.
    feather_px = env_int("RCPILOT_STITCH_FEATHER_PX", 24)
    if feather_px > 0:
        overlap_full = (left_mask > 0) & (right_mask > 0)
        diff = left_distance - right_distance  # >0 = deeper into left
        in_band = overlap_full & (np.abs(diff) < float(feather_px))
        far_overlap = overlap_full & ~in_band
        # Linear ramp across the band: at diff = -feather_px → all right,
        # at diff = +feather_px → all left.
        ramp_left = np.clip(0.5 + diff / (2.0 * float(feather_px)), 0.0, 1.0)
        left_weight[in_band] = ramp_left[in_band].astype(np.float32)
        right_weight[in_band] = (1.0 - ramp_left[in_band]).astype(np.float32)
        snap_left = far_overlap & (diff > 0)
        snap_right = far_overlap & (diff < 0)
        left_weight[snap_left] = 1.0
        right_weight[snap_left] = 0.0
        left_weight[snap_right] = 0.0
        right_weight[snap_right] = 1.0

    coverage_mask = ((left_mask > 0) | (right_mask > 0)).astype(np.uint8)
    overlap_mask = ((left_mask > 0) & (right_mask > 0)).astype(np.uint8) * 255
    if np.count_nonzero(overlap_mask) > 0:
        overlap_mask = cv2.erode(overlap_mask, np.ones((7, 7), dtype=np.uint8))
    overlap_pixels = int(np.count_nonzero(overlap_mask))
    if env_bool("RCPILOT_STITCH_CROP_BORDERS", True):
        min_edge_coverage = env_float("RCPILOT_STITCH_CROP_EDGE_COVERAGE", 0.985)
        crop_x, crop_y, crop_w, crop_h = find_clean_crop(
            coverage_mask, min_edge_coverage
        )
    else:
        crop_x, crop_y, crop_w, crop_h = 0, 0, canvas_w, canvas_h

    exposure_match = env_bool("RCPILOT_STITCH_EXPOSURE_MATCH", True)
    exposure_alpha = float(
        np.clip(env_float("RCPILOT_STITCH_EXPOSURE_ALPHA", 0.08), 0.0, 1.0)
    )

    cuda_ok = False
    if use_cuda and hasattr(cv2, "cuda"):
        try:
            cuda_ok = cv2.cuda.getCudaEnabledDeviceCount() > 0
        except cv2.error:
            cuda_ok = False

    log.info(
        "stitch canvas=%dx%d crop=(%d,%d %dx%d) left_offset=(%d,%d) "
        "overlap_pixels=%d feather_px=%d exposure_match=%s cuda_warp=%s",
        canvas_w, canvas_h, crop_x, crop_y, crop_w, crop_h, tx, ty,
        overlap_pixels, feather_px, exposure_match, cuda_ok,
    )
    return StitchPlan(
        h_canvas=h_canvas.astype(np.float64),
        left_x=tx,
        left_y=ty,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_w=crop_w,
        crop_h=crop_h,
        left_weight=left_weight,
        right_weight=right_weight,
        overlap_mask=overlap_mask,
        overlap_pixels=overlap_pixels,
        raw_left_coverage=(left_mask > 0).astype(np.uint8) * 255,
        raw_right_coverage=(right_mask > 0).astype(np.uint8) * 255,
        exposure_match=exposure_match,
        exposure_alpha=exposure_alpha,
        exposure_gain=np.ones(3, dtype=np.float32),
        use_cuda=cuda_ok,
    )


def match_right_exposure(canvas_l: np.ndarray, canvas_r: np.ndarray,
                         plan: StitchPlan) -> np.ndarray:
    if not plan.exposure_match or plan.overlap_pixels < 5000:
        return canvas_r

    left_mean = np.array(cv2.mean(canvas_l, mask=plan.overlap_mask)[:3],
                         dtype=np.float32)
    right_mean = np.array(cv2.mean(canvas_r, mask=plan.overlap_mask)[:3],
                          dtype=np.float32)
    if float(right_mean.max()) < 2.0 or float(left_mean.max()) < 2.0:
        return canvas_r

    target_gain = np.clip(left_mean / np.maximum(right_mean, 1.0), 0.70, 1.45)
    plan.exposure_gain = (
        plan.exposure_gain * (1.0 - plan.exposure_alpha)
        + target_gain * plan.exposure_alpha
    ).astype(np.float32)

    adjusted = canvas_r.astype(np.float32) * plan.exposure_gain.reshape(1, 1, 3)
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def stitch_frame(left: np.ndarray, right: np.ndarray, plan: StitchPlan,
                 out_w: int, out_h: int) -> np.ndarray:
    canvas_l = np.zeros((plan.canvas_h, plan.canvas_w, 3), dtype=np.uint8)
    canvas_l[
        plan.left_y:plan.left_y + left.shape[0],
        plan.left_x:plan.left_x + left.shape[1],
    ] = left

    if plan.use_cuda:
        gpu = cv2.cuda_GpuMat()
        gpu.upload(right)
        warped_gpu = cv2.cuda.warpPerspective(
            gpu, plan.h_canvas, (plan.canvas_w, plan.canvas_h)
        )
        canvas_r = warped_gpu.download()
    else:
        canvas_r = cv2.warpPerspective(
            right, plan.h_canvas, (plan.canvas_w, plan.canvas_h),
            flags=cv2.INTER_LINEAR,
        )

    canvas_r = match_right_exposure(canvas_l, canvas_r, plan)
    stitched = (
        canvas_l.astype(np.float32) * plan.left_weight[..., None]
        + canvas_r.astype(np.float32) * plan.right_weight[..., None]
    )
    stitched = np.clip(stitched, 0, 255).astype(np.uint8)
    stitched = stitched[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]

    if stitched.shape[1] != out_w or stitched.shape[0] != out_h:
        stitched = cv2.resize(stitched, (out_w, out_h), interpolation=cv2.INTER_AREA)
    return stitched


def bake_fast_path(plan: StitchPlan, src_w: int, src_h: int,
                   out_w: int, out_h: int, log: logging.Logger) -> None:
    """Pre-compute remap tables and weights at output resolution.

    The slow stitch_frame() does its work on the full canvas (which is larger
    than the output), then crops and resizes. The fast path collapses warp +
    crop + resize into two cv2.remap lookups straight from each source camera
    into the final out_w x out_h frame, and pre-converts the alpha weights to
    3-channel uint8 so the blend is one cv2.multiply + cv2.add per camera.
    """
    # For each output pixel (u, v), figure out which canvas pixel it samples,
    # then which source pixel that canvas pixel came from.
    sx_u = (np.arange(out_w, dtype=np.float32) + 0.5) * (plan.crop_w / float(out_w))
    sy_v = (np.arange(out_h, dtype=np.float32) + 0.5) * (plan.crop_h / float(out_h))
    canvas_x = (plan.crop_x + sx_u - 0.5).reshape(1, -1)
    canvas_y = (plan.crop_y + sy_v - 0.5).reshape(-1, 1)
    cx_full = np.broadcast_to(canvas_x, (out_h, out_w)).astype(np.float32)
    cy_full = np.broadcast_to(canvas_y, (out_h, out_w)).astype(np.float32)

    # Left camera: canvas was just translated by (left_x, left_y). Don't
    # pre-filter "out of bounds" — cv2.remap's BORDER_CONSTANT handles real
    # OOB by sampling 0, and pre-filtering would clobber legitimate edge
    # samples whose interpolation kernel overlaps the source by 80%+ (e.g.
    # a coordinate of -0.09 should still pull mostly-real pixel data, the
    # same way the slow path's resize step does).
    invalid = -1e6  # only used to neutralize NaN/Inf, never triggered for valid samples
    left_sx = (cx_full - float(plan.left_x)).astype(np.float32)
    left_sy = (cy_full - float(plan.left_y)).astype(np.float32)

    # Right camera: invert the canvas homography.
    h_inv = np.linalg.inv(plan.h_canvas).astype(np.float64)
    flat_x = cx_full.ravel().astype(np.float64)
    flat_y = cy_full.ravel().astype(np.float64)
    homo = np.stack([flat_x, flat_y, np.ones_like(flat_x)], axis=0)  # (3, N)
    src = h_inv @ homo  # (3, N)
    # Guard against the homography's line at infinity producing NaN/Inf —
    # those values would crash or produce garbage in cv2.remap.
    denom = src[2]
    safe = np.abs(denom) > 1e-9
    rsx = np.where(safe, src[0] / np.where(safe, denom, 1.0), invalid).reshape(out_h, out_w)
    rsy = np.where(safe, src[1] / np.where(safe, denom, 1.0), invalid).reshape(out_h, out_w)
    rsx = np.where(np.isfinite(rsx), rsx, invalid).astype(np.float32)
    rsy = np.where(np.isfinite(rsy), rsy, invalid).astype(np.float32)

    # Resample the float32 weights to output resolution and convert to
    # 3-channel uint8 [0..255] so cv2.multiply lines up channel-for-channel.
    cropped_lw = plan.left_weight[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]
    cropped_rw = plan.right_weight[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]
    interp = cv2.INTER_AREA if plan.crop_w > out_w else cv2.INTER_LINEAR
    lw_out = cv2.resize(cropped_lw, (out_w, out_h), interpolation=interp)
    rw_out = cv2.resize(cropped_rw, (out_w, out_h), interpolation=interp)
    # Renormalize so the pair sums to 1 wherever covered.
    total = lw_out + rw_out
    valid = total > 1e-6
    lw_norm = np.zeros_like(lw_out)
    rw_norm = np.zeros_like(rw_out)
    lw_norm[valid] = lw_out[valid] / total[valid]
    rw_norm[valid] = rw_out[valid] / total[valid]
    lw_u8 = np.clip(lw_norm * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rw_u8 = np.clip(rw_norm * 255.0 + 0.5, 0, 255).astype(np.uint8)
    plan.fast_weight_left_bgr = cv2.merge([lw_u8, lw_u8, lw_u8])
    plan.fast_weight_right_bgr = cv2.merge([rw_u8, rw_u8, rw_u8])

    # Pre-feather "raw" left and right coverage at canvas resolution: where
    # each camera actually has pixels, before any feather narrowing snapped
    # the weights to 1/0. We need this for seam-finding because the feather
    # pass collapses the soft overlap band; the seam-finder wants the *full*
    # overlap so it can route around foreground objects.
    raw_l = plan.raw_left_coverage
    raw_r = plan.raw_right_coverage
    raw_overlap = ((raw_l > 0) & (raw_r > 0)).astype(np.uint8) * 255
    raw_covered = ((raw_l > 0) | (raw_r > 0)).astype(np.uint8) * 255
    raw_left_only = ((raw_l > 0) & (raw_r == 0)).astype(np.uint8) * 255

    cropped_overlap = raw_overlap[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]
    cropped_covered = raw_covered[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]
    cropped_left_only = raw_left_only[
        plan.crop_y:plan.crop_y + plan.crop_h,
        plan.crop_x:plan.crop_x + plan.crop_w,
    ]
    plan.fast_overlap_mask_out = cv2.resize(
        cropped_overlap, (out_w, out_h), interpolation=cv2.INTER_NEAREST,
    )
    plan.fast_covered_mask = cv2.resize(
        cropped_covered, (out_w, out_h), interpolation=cv2.INTER_NEAREST,
    )

    # Baseline left weight valid OUTSIDE the overlap band: 255 in left-only
    # region, 0 elsewhere. Inside the overlap, the per-frame seam logic
    # supersedes this. Use the *raw* left-only mask (where left covers and
    # right does not) so the boundary lines up with the overlap_mask above.
    baseline = cv2.resize(cropped_left_only, (out_w, out_h),
                          interpolation=cv2.INTER_NEAREST)
    plan.fast_baseline_left_u8 = baseline

    # Per-row overlap range, used to skip-DP rows that have no overlap.
    overlap_bool = overlap_out > 0
    if overlap_bool.any():
        row_any = overlap_bool.any(axis=1)
        # For rows that have any overlap, find first and last True column.
        # Use argmax tricks for speed: argmax on bool returns first True.
        col_idx = np.arange(out_w)
        # row_lo[r] = first col where overlap_bool[r] is True (or -1 if none)
        first_col = np.where(row_any,
                             overlap_bool.argmax(axis=1),
                             -1).astype(np.int32)
        # last_col[r] = last col where overlap_bool[r] is True
        # Reverse trick: argmax on reversed gives the position from right.
        rev = overlap_bool[:, ::-1]
        last_col = np.where(row_any,
                            (out_w - 1) - rev.argmax(axis=1),
                            -1).astype(np.int32)
        plan.fast_overlap_row_lo = first_col
        plan.fast_overlap_row_hi = (last_col + 1).astype(np.int32)
        plan.fast_overlap_xmin = int(first_col[row_any].min())
        plan.fast_overlap_xmax = int(last_col[row_any].max()) + 1
    else:
        plan.fast_overlap_row_lo = np.full(out_h, -1, dtype=np.int32)
        plan.fast_overlap_row_hi = np.full(out_h, -1, dtype=np.int32)
        plan.fast_overlap_xmin = 0
        plan.fast_overlap_xmax = 0

    # Initial seam guess: midpoint of each row's overlap. Smoothed each frame.
    seam_init = np.where(
        plan.fast_overlap_row_lo >= 0,
        (plan.fast_overlap_row_lo.astype(np.int64)
         + plan.fast_overlap_row_hi.astype(np.int64) - 1) // 2,
        0,
    ).astype(np.int32)
    plan.fast_seam_prev = seam_init

    plan.fast_map_left_x = left_sx
    plan.fast_map_left_y = left_sy
    plan.fast_map_right_x = rsx
    plan.fast_map_right_y = rsy

    log.info(
        "fast-path baked: out=%dx%d remap_tables=2 overlap_pixels=%d "
        "overlap_xrange=[%d,%d) exposure_every_n=%d",
        out_w, out_h, int((overlap_out > 0).sum()),
        plan.fast_overlap_xmin, plan.fast_overlap_xmax,
        plan.fast_exposure_every_n,
    )


def find_dynamic_seam(warped_left: np.ndarray, warped_right: np.ndarray,
                      plan: StitchPlan, downscale: int = 2) -> np.ndarray:
    """Return a per-row column position for a minimum-disagreement seam
    through the overlap band.

    Where left and right cameras agree (background, well-aligned by the
    homography), pixel-difference is small. Where they disagree (parallax-
    affected foreground like a hand reaching toward the cameras), it's
    large. A vertical dynamic-program seam through the cost map naturally
    routes around the disagreement region, putting the hand in exactly one
    camera instead of double-blending it.

    `downscale` runs the cost+DP at half/quarter resolution for speed; the
    seam is upscaled back to output rows. 1 = full res, 2 = half (default).
    """
    out_h, out_w = warped_left.shape[:2]
    xmin = plan.fast_overlap_xmin
    xmax = plan.fast_overlap_xmax
    if xmax <= xmin:
        # No overlap — every output row is single-camera, no seam needed.
        return plan.fast_seam_prev

    # Crop to overlap bounding box for faster work.
    band_l = warped_left[:, xmin:xmax]
    band_r = warped_right[:, xmin:xmax]
    # Sum-of-channel-abs-diff is a cheap "do these cameras agree" metric,
    # used as the BASE cost.
    diff = cv2.absdiff(band_l, band_r)
    diff_sum = diff.sum(axis=2).astype(np.int32)

    # CRUCIAL: where a foreground object (like a hand) appears in BOTH
    # cameras at different positions, the *interior* of the doubled-object
    # region can have low pixel-diff (both cameras show "hand pixels", even
    # though they're different parts of the hand). The DISAGREEMENT shows
    # only at the object's silhouette boundaries. To prevent the seam from
    # cutting through the middle of a foreground object, threshold and
    # dilate the disagreement boundaries into a wide no-fly zone, then
    # treat that whole zone as high cost.
    disagree_thr = 24  # below this is "background agreement"
    forbidden = (diff_sum > disagree_thr).astype(np.uint8) * 255
    # Dilate horizontally a lot, vertically less. Parallax is mostly
    # horizontal (cameras side-by-side), so the no-fly zone needs to be
    # wide enough to bridge the hand's two image positions.
    band_w = xmax - xmin
    h_kernel = max(15, band_w // 4)  # ~25% of overlap width
    v_kernel = 9
    forbidden = cv2.dilate(
        forbidden,
        cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel, v_kernel)),
    )

    # Mask out non-overlap pixels AND forbidden zones with high cost.
    overlap_band = plan.fast_overlap_mask_out[:, xmin:xmax]
    high = 1 << 24
    cost = np.where(overlap_band > 0, diff_sum, high).astype(np.int64)
    cost = np.where(forbidden > 0, np.int64(high), cost)

    # Optional downscale for the DP. cv2.resize keeps the cost shape stable.
    if downscale > 1:
        ds_h = max(1, out_h // downscale)
        ds_w = max(1, cost.shape[1] // downscale)
        cost_ds = cv2.resize(cost.astype(np.float32),
                             (ds_w, ds_h),
                             interpolation=cv2.INTER_AREA).astype(np.int64)
    else:
        cost_ds = cost
        ds_h, ds_w = cost_ds.shape

    # Dynamic program top-down. Each row's cell adds the min of the three
    # cells above it (left, center, right) plus its own cost. int64 keeps
    # the accumulator from overflowing when many rows worth of "high cost"
    # placeholder values stack up.
    dp = np.empty_like(cost_ds)
    dp[0] = cost_ds[0]
    big = np.int64(high) * np.int64(ds_h)
    for r in range(1, ds_h):
        prev = dp[r - 1]
        ls = np.empty_like(prev)
        ls[0] = big
        ls[1:] = prev[:-1]
        rs = np.empty_like(prev)
        rs[-1] = big
        rs[:-1] = prev[1:]
        dp[r] = cost_ds[r] + np.minimum(np.minimum(ls, prev), rs)

    # Backtrace: start at min of last row, walk up choosing min predecessor.
    seam_ds = np.empty(ds_h, dtype=np.int32)
    seam_ds[-1] = int(np.argmin(dp[-1]))
    for r in range(ds_h - 2, -1, -1):
        c = seam_ds[r + 1]
        cl = c - 1 if c > 0 else c
        cr = c + 1 if c < ds_w - 1 else c
        # Pick the predecessor with the lowest cost.
        candidates = (dp[r, cl], dp[r, c], dp[r, cr])
        best = int(np.argmin(candidates))
        seam_ds[r] = (cl, c, cr)[best]

    # Upscale seam back to output rows and shift to absolute output cols.
    if downscale > 1:
        # Map ds_row → out_row by linear interp.
        out_rows = np.arange(out_h)
        ds_rows = (out_rows.astype(np.float32) + 0.5) * (ds_h / float(out_h)) - 0.5
        ds_rows = np.clip(ds_rows, 0, ds_h - 1)
        ri = ds_rows.astype(np.int32)
        rf = ds_rows - ri
        ri_next = np.clip(ri + 1, 0, ds_h - 1)
        seam_full_band = (
            seam_ds[ri].astype(np.float32) * (1 - rf)
            + seam_ds[ri_next].astype(np.float32) * rf
        )
        seam_full_band = (seam_full_band * downscale).astype(np.int32)
    else:
        seam_full_band = seam_ds.copy()

    # Translate to absolute output column.
    seam_full = seam_full_band + xmin

    # Clamp each row to its specific overlap range; rows with no overlap
    # keep the prev seam value (irrelevant, since baseline mask covers them).
    lo = plan.fast_overlap_row_lo
    hi = plan.fast_overlap_row_hi
    has_overlap = lo >= 0
    seam_clamped = np.where(
        has_overlap,
        np.clip(seam_full, np.maximum(lo, 0), np.maximum(hi - 1, 0)),
        plan.fast_seam_prev,
    ).astype(np.int32)

    # Temporal smoothing: blend with the previous frame's seam so it doesn't
    # jitter on small per-frame cost-map noise.
    alpha = 1.0 - float(plan.fast_seam_smooth)  # alpha = how much new contributes
    if alpha < 0.999:
        smoothed = (
            plan.fast_seam_prev.astype(np.float32) * (1 - alpha)
            + seam_clamped.astype(np.float32) * alpha
        ).astype(np.int32)
    else:
        smoothed = seam_clamped
    plan.fast_seam_prev = smoothed
    return smoothed


def build_seam_weights(plan: StitchPlan, seam: np.ndarray,
                       out_h: int, out_w: int,
                       crossfade_px: int) -> np.ndarray:
    """Build a (out_h, out_w) uint8 weight map for the LEFT camera. The
    right camera's weight is (covered_mask - left_weight)."""
    col_idx = np.arange(out_w, dtype=np.float32)[None, :]
    seam_col = seam.astype(np.float32)[:, None]
    # Distance from seam: negative = left of seam (use left camera).
    d = col_idx - seam_col
    if crossfade_px <= 0:
        # Hard cut: 1 if d < 0, else 0.
        wl_norm = (d < 0).astype(np.float32)
    else:
        wl_norm = np.clip(0.5 - d / (2.0 * float(crossfade_px)), 0.0, 1.0)
    wl_u8 = (wl_norm * 255.0 + 0.5).astype(np.uint8)
    # Override outside overlap with the static baseline weight (so left-only
    # gets 255 left and right-only gets 0).
    band = plan.fast_overlap_mask_out
    wl_u8 = np.where(band > 0, wl_u8, plan.fast_baseline_left_u8)
    # Zero out fully-uncovered pixels.
    wl_u8 = np.where(plan.fast_covered_mask > 0, wl_u8, 0).astype(np.uint8)
    return wl_u8


def stitch_frame_fast(left: np.ndarray, right: np.ndarray, plan: StitchPlan,
                      out_w: int, out_h: int, frame_idx: int,
                      seam_mode: str = "dynamic",
                      seam_crossfade_px: int = 4,
                      seam_downscale: int = 2) -> np.ndarray:
    """Per-frame fast path: two remaps, optional seam routing, blend.

    seam_mode = "feather"   - use the static feathered weights from bake
                              (Codex's original behavior, shows ghosting).
              = "dynamic"   - per-frame minimum-disagreement seam-cut so
                              foreground objects (hands) appear in exactly
                              one camera. Default — fixes parallax ghost.
              = "static"    - hard cut at the natural distance-transform
                              seam, no per-frame work but cuts through
                              foreground if it sits on the seam.
    """
    warped_left = cv2.remap(
        left, plan.fast_map_left_x, plan.fast_map_left_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    warped_right = cv2.remap(
        right, plan.fast_map_right_x, plan.fast_map_right_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )

    # Sampled exposure refresh: only every N frames, and on the small overlap
    # region rather than the full canvas. Skip the work in between.
    if (
        plan.exposure_match
        and plan.fast_overlap_mask_out is not None
        and plan.fast_exposure_every_n > 0
        and (frame_idx % plan.fast_exposure_every_n) == 0
        and int(np.count_nonzero(plan.fast_overlap_mask_out)) > 5000
    ):
        left_mean = np.array(
            cv2.mean(warped_left, mask=plan.fast_overlap_mask_out)[:3],
            dtype=np.float32,
        )
        right_mean = np.array(
            cv2.mean(warped_right, mask=plan.fast_overlap_mask_out)[:3],
            dtype=np.float32,
        )
        if float(right_mean.max()) > 2.0 and float(left_mean.max()) > 2.0:
            target_gain = np.clip(
                left_mean / np.maximum(right_mean, 1.0), 0.70, 1.45,
            )
            plan.exposure_gain = (
                plan.exposure_gain * (1.0 - plan.exposure_alpha)
                + target_gain * plan.exposure_alpha
            ).astype(np.float32)

    if plan.exposure_match:
        gain = plan.exposure_gain
        if not (0.99 <= float(gain.min()) and float(gain.max()) <= 1.01):
            # Per-channel gain via split / convertScaleAbs / merge — keeps
            # everything in uint8 land so we never allocate a float canvas.
            b, g, r = cv2.split(warped_right)
            b = cv2.convertScaleAbs(b, alpha=float(gain[0]))
            g = cv2.convertScaleAbs(g, alpha=float(gain[1]))
            r = cv2.convertScaleAbs(r, alpha=float(gain[2]))
            warped_right = cv2.merge([b, g, r])

    # Pick the weight map based on seam_mode.
    if seam_mode == "feather" or plan.fast_overlap_xmax <= plan.fast_overlap_xmin:
        # Original feather-blend behavior, or no overlap → use baked weights.
        wl_bgr = plan.fast_weight_left_bgr
        wr_bgr = plan.fast_weight_right_bgr
    else:
        if seam_mode == "dynamic":
            seam = find_dynamic_seam(warped_left, warped_right, plan,
                                     downscale=max(1, seam_downscale))
        elif seam_mode == "static":
            # Use the static midline that bake set as the initial seam.
            seam = plan.fast_seam_prev
        else:
            raise ValueError(f"unknown seam_mode {seam_mode!r}")
        wl_u8 = build_seam_weights(plan, seam, out_h, out_w,
                                   crossfade_px=max(0, seam_crossfade_px))
        wr_u8 = np.where(plan.fast_covered_mask > 0,
                         255 - wl_u8, 0).astype(np.uint8)
        wl_bgr = cv2.merge([wl_u8, wl_u8, wl_u8])
        wr_bgr = cv2.merge([wr_u8, wr_u8, wr_u8])

    # Vectorized uint8 blend: each pixel = (src * weight) / 255, summed.
    left_contrib = cv2.multiply(warped_left, wl_bgr, scale=1.0 / 255.0)
    right_contrib = cv2.multiply(warped_right, wr_bgr, scale=1.0 / 255.0)
    return cv2.add(left_contrib, right_contrib)


def calibration_path() -> Path:
    default = "/home/adm2n/rcpilot/config/stitch_calibration.json"
    return Path(os.getenv("RCPILOT_STITCH_CALIBRATION", default))


def debug_dir() -> Path:
    return Path(os.getenv("RCPILOT_STITCH_DEBUG_DIR", "/tmp/rcpilot-stitch-debug"))


def save_debug_image(name: str, frame: np.ndarray, log: logging.Logger) -> None:
    try:
        path = debug_dir()
        path.mkdir(parents=True, exist_ok=True)
        out = path / name
        cv2.imwrite(str(out), frame)
        log.warning("saved stitch debug image: %s", out)
    except Exception as exc:
        log.warning("could not save stitch debug image %s: %s", name, exc)


def load_calibration(path: Path, width: int, height: int,
                     log: logging.Logger) -> Optional[HomographyResult]:
    if env_bool("RCPILOT_STITCH_RECALIBRATE", False):
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("width") != width or data.get("height") != height:
            log.warning("ignoring calibration with mismatched dimensions: %s", path)
            return None
        h = np.array(data["h_right_to_left"], dtype=np.float64)
        if h.shape != (3, 3):
            return None
        log.info("loaded stitch calibration: %s", path)
        return HomographyResult(h, int(data.get("inliers", 0)),
                                int(data.get("matches", 0)), "cached")
    except Exception as exc:
        log.warning("could not load stitch calibration %s: %s", path, exc)
        return None


def save_calibration(path: Path, width: int, height: int,
                     result: HomographyResult, log: logging.Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "width": width,
            "height": height,
            "h_right_to_left": result.h_right_to_left.tolist(),
            "inliers": result.inliers,
            "matches": result.matches,
            "inlier_ratio": result.ratio,
            "detector": result.detector,
            "created_at": time.time(),
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        log.info("saved stitch calibration: %s", path)
    except Exception as exc:
        log.warning("could not save stitch calibration %s: %s", path, exc)


def calibrate(left_reader: CameraReader, right_reader: CameraReader,
              width: int, height: int, log: logging.Logger) -> HomographyResult:
    path = calibration_path()
    cached = load_calibration(path, width, height, log)
    if cached is not None:
        return cached

    samples = env_int("RCPILOT_STITCH_CALIBRATION_SAMPLES", 8)
    min_matches = env_int("RCPILOT_STITCH_MIN_MATCHES", 18)
    feature_scale = env_float("RCPILOT_STITCH_FEATURE_SCALE", 0.50)
    best: Optional[HomographyResult] = None
    last_left: Optional[np.ndarray] = None
    last_right: Optional[np.ndarray] = None

    log.info("estimating stitch alignment from %d frame pairs", samples)
    for idx in range(samples):
        left = left_reader.wait_frame(timeout_s=3.0)
        right = right_reader.wait_frame(timeout_s=3.0)
        if left is None or right is None:
            log.warning("calibration sample %d: no frame yet", idx + 1)
            continue
        last_left = left
        last_right = right
        result = estimate_homography(left, right, feature_scale, min_matches, log)
        if result is None:
            continue
        log.info(
            "calibration sample %d: %s matches=%d inliers=%d ratio=%.2f",
            idx + 1, result.detector, result.matches, result.inliers,
            result.ratio,
        )
        if best is None or result.inliers > best.inliers:
            best = result
        time.sleep(0.12)

    if best is not None:
        save_calibration(path, width, height, best, log)
        return best

    if last_left is not None:
        save_debug_image("left_calibration_failed.jpg", last_left, log)
    if last_right is not None:
        save_debug_image("right_calibration_failed.jpg", last_right, log)

    overlap = env_int("RCPILOT_STITCH_FALLBACK_OVERLAP_PX", 180)
    if env_bool("RCPILOT_STITCH_ALLOW_FALLBACK", False):
        log.warning(
            "feature stitch failed; RCPILOT_STITCH_ALLOW_FALLBACK=1, using "
            "a blended %dpx overlap approximation.",
            overlap,
        )
        return HomographyResult(approximate_homography(width, overlap), 0, 0, "fallback")

    raise SystemExit(
        "Could not estimate a real stitch between the cameras. The stream was "
        "not started because that would look like a split two-feed view. Aim "
        "both cameras at a textured scene with visible overlap, then rerun with "
        "RCPILOT_STITCH_RECALIBRATE=1. Debug frames were saved under "
        f"{debug_dir()}."
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-12s %(levelname)s: %(message)s",
    )
    log = logging.getLogger("stitch")

    cockpit_ip = os.getenv("RCPILOT_COCKPIT_IP")
    if not cockpit_ip:
        raise SystemExit("RCPILOT_COCKPIT_IP is required")

    port = env_int("RCPILOT_VIDEO_PORT", 5004)
    left_sensor = env_int("RCPILOT_LEFT_SENSOR_ID", 0)
    right_sensor = env_int("RCPILOT_RIGHT_SENSOR_ID", 1)
    width = env_int("RCPILOT_VIDEO_WIDTH", 1280)
    height = env_int("RCPILOT_VIDEO_HEIGHT", 720)
    fps = env_int("RCPILOT_VIDEO_FPS", 30)
    out_width = env_int("RCPILOT_STITCH_OUT_WIDTH", 2560)
    out_height = env_int("RCPILOT_STITCH_OUT_HEIGHT", 720)
    bitrate = env_int("RCPILOT_BITRATE_KBPS", 18000)
    sensor_mode = env_int("RCPILOT_SENSOR_MODE", 4)
    encoder = os.getenv("RCPILOT_ENCODER", "x264enc")
    key_interval = env_int("RCPILOT_KEY_INTERVAL", max(1, fps // 2))
    x264_preset = os.getenv("RCPILOT_X264_PRESET", "superfast")
    use_cuda = env_bool("RCPILOT_STITCH_USE_CUDA", True)
    use_fast = env_bool("RCPILOT_STITCH_FAST", True)
    exposure_every_n = env_int("RCPILOT_STITCH_EXPOSURE_EVERY_N", 15)
    seam_mode = os.getenv("RCPILOT_STITCH_SEAM", "dynamic").strip().lower()
    if seam_mode not in {"dynamic", "static", "feather"}:
        raise SystemExit(
            f"RCPILOT_STITCH_SEAM must be dynamic|static|feather, got "
            f"{seam_mode!r}"
        )
    seam_crossfade_px = env_int("RCPILOT_STITCH_SEAM_CROSSFADE_PX", 4)
    seam_downscale = max(1, env_int("RCPILOT_STITCH_SEAM_DOWNSCALE", 2))
    seam_smooth = float(np.clip(
        env_float("RCPILOT_STITCH_SEAM_SMOOTH", 0.6), 0.0, 0.95))

    if left_sensor == right_sensor:
        raise SystemExit("left and right sensor ids must differ")

    log.info(
        "real stitch sender: sensors %d/%d capture=%dx%d@%d output=%dx%d "
        "encoder=%s bitrate=%d fast=%s seam=%s seam_crossfade=%d "
        "seam_downscale=%d -> %s:%d",
        left_sensor, right_sensor, width, height, fps, out_width, out_height,
        encoder, bitrate, use_fast, seam_mode, seam_crossfade_px,
        seam_downscale, cockpit_ip, port,
    )

    left_pipeline = build_capture_pipeline(left_sensor, width, height, fps, sensor_mode)
    right_pipeline = build_capture_pipeline(right_sensor, width, height, fps, sensor_mode)
    writer_pipeline = build_writer_pipeline(
        cockpit_ip, port, out_width, out_height, fps, bitrate,
        encoder, key_interval, x264_preset,
    )

    left_reader = CameraReader("left", left_pipeline, log)
    right_reader = CameraReader("right", right_pipeline, log)
    left_reader.start()
    right_reader.start()

    running = True

    def stop(_signum=None, _frame=None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    writer = None
    try:
        h_result = calibrate(left_reader, right_reader, width, height, log)
        log.info(
            "stitch alignment: detector=%s matches=%d inliers=%d ratio=%.2f",
            h_result.detector, h_result.matches, h_result.inliers, h_result.ratio,
        )
        plan = make_plan(
            h_result.h_right_to_left, width, height,
            max_canvas_w=width * 4,
            max_canvas_h=height * 3,
            use_cuda=use_cuda,
            log=log,
        )
        plan.fast_exposure_every_n = max(0, exposure_every_n)
        plan.fast_seam_smooth = seam_smooth
        if use_fast:
            bake_fast_path(plan, width, height, out_width, out_height, log)
        preview_left, _ = left_reader.latest()
        preview_right, _ = right_reader.latest()
        if preview_left is not None and preview_right is not None:
            if use_fast:
                preview = stitch_frame_fast(
                    preview_left, preview_right, plan,
                    out_width, out_height, frame_idx=0,
                    seam_mode=seam_mode,
                    seam_crossfade_px=seam_crossfade_px,
                    seam_downscale=seam_downscale,
                )
            else:
                preview = stitch_frame(
                    preview_left, preview_right, plan, out_width, out_height,
                )
            save_debug_image("stitched_preview.jpg", preview, log)

        writer = cv2.VideoWriter(
            writer_pipeline, cv2.CAP_GSTREAMER, 0, float(fps),
            (out_width, out_height), True,
        )
        if not writer.isOpened():
            raise SystemExit("could not open RTP writer pipeline")
        log.info("RTP writer open")

        frame_count = 0
        total_frames = 0
        last_log = time.monotonic()
        period = 1.0 / max(1, fps)
        next_frame = time.monotonic()
        while running:
            now = time.monotonic()
            if now < next_frame:
                time.sleep(min(0.005, next_frame - now))
                continue
            next_frame += period

            left, left_count = left_reader.latest()
            right, right_count = right_reader.latest()
            if left is None or right is None:
                continue
            if use_fast:
                stitched = stitch_frame_fast(
                    left, right, plan, out_width, out_height,
                    frame_idx=total_frames,
                    seam_mode=seam_mode,
                    seam_crossfade_px=seam_crossfade_px,
                    seam_downscale=seam_downscale,
                )
            else:
                stitched = stitch_frame(left, right, plan, out_width, out_height)
            writer.write(stitched)
            frame_count += 1
            total_frames += 1

            now = time.monotonic()
            if now - last_log >= 2.0:
                if plan.exposure_match:
                    gain = plan.exposure_gain
                    log.info(
                        "streaming stitched panorama: %.1f fps left=%d right=%d "
                        "gain_bgr=(%.2f,%.2f,%.2f)",
                        frame_count / (now - last_log), left_count, right_count,
                        gain[0], gain[1], gain[2],
                    )
                else:
                    log.info(
                        "streaming stitched panorama: %.1f fps left=%d right=%d",
                        frame_count / (now - last_log), left_count, right_count,
                    )
                frame_count = 0
                last_log = now
    finally:
        if writer is not None:
            writer.release()
        left_reader.stop()
        right_reader.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
