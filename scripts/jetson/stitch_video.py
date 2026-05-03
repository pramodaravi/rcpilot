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
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                self.log.warning("%s camera thread did not stop cleanly", self.label)

    def latest(self) -> Tuple[Optional[np.ndarray], int]:
        with self._lock:
            if self._latest is None:
                return None, self._count
            return self._latest.copy(), self._count

    def wait_frame(self, timeout_s: float,
                   after_count: int = -1) -> Tuple[Optional[np.ndarray], int]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            frame, count = self.latest()
            if frame is not None and count != after_count:
                return frame, count
            time.sleep(0.01)
        return None, after_count

    def _loop(self) -> None:
        retry_s = 1.0
        read_failures = 0
        max_retry_s = env_float("RCPILOT_CAMERA_RETRY_MAX_S", 6.0)
        while self._running.is_set():
            cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                self.log.warning(
                    "%s camera open failed; retrying in %.1fs",
                    self.label, retry_s,
                )
                time.sleep(retry_s)
                retry_s = min(max_retry_s, retry_s * 1.6)
                continue
            self.log.info("%s camera open", self.label)
            frames_this_open = 0
            try:
                while self._running.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        read_failures += 1
                        self.log.warning(
                            "%s camera read failed; reopening in %.1fs",
                            self.label, retry_s,
                        )
                        if read_failures >= 4 and frames_this_open == 0:
                            self.log.warning(
                                "%s camera is opening but not delivering frames. "
                                "If both cameras do this after a previous crash, "
                                "restart nvargus-daemon on the Jetson.",
                                self.label,
                            )
                        break
                    with self._lock:
                        self._latest = frame
                        self._count += 1
                    frames_this_open += 1
                    read_failures = 0
                    retry_s = 1.0
            finally:
                cap.release()
            time.sleep(retry_s)
            retry_s = min(max_retry_s, retry_s * 1.6)


@dataclass
class HomographyResult:
    h_right_to_left: np.ndarray
    inliers: int
    matches: int
    detector: str
    model: str = "homography"
    reproj_error_px: float = 0.0

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
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    left_gray = clahe.apply(left_gray)
    right_gray = clahe.apply(right_gray)

    detector_name = "ORB"
    if hasattr(cv2, "SIFT_create"):
        try:
            detector = cv2.SIFT_create(
                nfeatures=5000, contrastThreshold=0.015, edgeThreshold=18,
            )
        except (TypeError, cv2.error):
            detector = cv2.SIFT_create(nfeatures=5000)
        detector_name = "SIFT"
    else:
        detector = cv2.ORB_create(nfeatures=5000, fastThreshold=8)

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
    ratio = float(np.clip(env_float("RCPILOT_STITCH_MATCH_RATIO", 0.82), 0.60, 0.95))
    for pair in raw:
        if len(pair) != 2:
            continue
        m, n = pair
        if m.distance < ratio * n.distance:
            good.append(m)

    if len(good) < min_matches:
        log.warning("not enough stitch matches: %d < %d", len(good), min_matches)
        return None

    pts_r = np.float32([kp_r[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_l = np.float32([kp_l[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    model = os.getenv("RCPILOT_STITCH_MODEL", "affine").strip().lower()
    if model not in {"affine", "homography"}:
        raise SystemExit(
            f"RCPILOT_STITCH_MODEL must be affine or homography, got {model!r}"
        )

    if model == "affine":
        affine, mask = cv2.estimateAffinePartial2D(
            pts_r.reshape(-1, 2), pts_l.reshape(-1, 2),
            method=cv2.RANSAC, ransacReprojThreshold=4.0,
            maxIters=3000, confidence=0.995, refineIters=10,
        )
        h_small = None
        if affine is not None:
            h_small = np.eye(3, dtype=np.float64)
            h_small[:2, :] = affine
    else:
        h_small, mask = cv2.findHomography(
            pts_r, pts_l, cv2.RANSAC, ransacReprojThreshold=4.0
        )
    if h_small is None or mask is None:
        log.warning("%s solve failed", model)
        return None

    inliers = int(mask.ravel().sum())
    min_inliers = env_int("RCPILOT_STITCH_MIN_INLIERS", max(6, min_matches - 2))
    if inliers < min_inliers:
        log.warning("not enough stitch inliers: %d < %d", inliers, min_inliers)
        return None

    projected = cv2.perspectiveTransform(pts_r, h_small)
    errors_small = np.linalg.norm(projected - pts_l, axis=2).ravel()
    inlier_errors = errors_small[mask.ravel().astype(bool)]
    reproj_error_px = float(np.median(inlier_errors) / max(scale, 1e-6))
    max_reproj = env_float("RCPILOT_STITCH_MAX_REPROJ_ERROR_PX", 12.0)
    if reproj_error_px > max_reproj:
        log.warning(
            "%s reprojection error too high: %.1fpx > %.1fpx",
            model, reproj_error_px, max_reproj,
        )
        return None

    s = np.array([[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]])
    h_full = np.linalg.inv(s) @ h_small @ s
    h_full = h_full / h_full[2, 2]
    return HomographyResult(
        h_full.astype(np.float64), inliers, len(good), detector_name,
        model=model, reproj_error_px=reproj_error_px,
    )


def approximate_homography(width: int, overlap_px: int) -> np.ndarray:
    # Places the right camera to the right with an overlap. This is a fallback,
    # not a true stitch, but still produces one blended widescreen frame.
    x = max(1, width - max(0, overlap_px))
    return np.array([[1.0, 0.0, float(x)], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


def opencv_cuda_remap_available() -> bool:
    if (
        not hasattr(cv2, "cuda")
        or not hasattr(cv2, "cuda_GpuMat")
        or not hasattr(cv2.cuda, "remap")
    ):
        return False
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() <= 0:
            return False
        src = np.zeros((2, 2, 3), dtype=np.uint8)
        map_x = np.array([[0, 1], [0, 1]], dtype=np.float32)
        map_y = np.array([[0, 0], [1, 1]], dtype=np.float32)
        gpu_src = upload_cuda_array(src)
        gpu_x = upload_cuda_array(map_x)
        gpu_y = upload_cuda_array(map_y)
        out = cv2.cuda.remap(
            gpu_src, gpu_x, gpu_y, cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        out.download()
        return True
    except (AttributeError, cv2.error):
        return False


def upload_cuda_array(array: np.ndarray):
    gpu = cv2.cuda_GpuMat()
    gpu.upload(array)
    return gpu


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
    # Optional OpenCV CUDA remap backend. Maps are uploaded once at startup;
    # frames still download to CPU memory because Orin Nano has no NVENC and
    # the final H.264 encode path is software x264.
    fast_accel: str = "cpu"
    fast_cuda_map_left_x: Optional[object] = None
    fast_cuda_map_left_y: Optional[object] = None
    fast_cuda_map_right_x: Optional[object] = None
    fast_cuda_map_right_y: Optional[object] = None
    # Single-channel uint8 mask of the overlap region at output resolution,
    # used to sample exposure cheaply without re-warping.
    fast_overlap_mask_out: Optional[np.ndarray] = None
    # How often to refresh the exposure gain in fast mode (frames).
    fast_exposure_every_n: int = 15


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


def fit_crop_to_aspect(crop: Tuple[int, int, int, int],
                       target_aspect: float) -> Tuple[int, int, int, int]:
    x, y, w, h = crop
    if w <= 0 or h <= 0 or target_aspect <= 0:
        return crop

    current = w / float(h)
    if abs(current - target_aspect) < 0.01:
        return crop

    if current > target_aspect:
        new_w = max(16, int(round(h * target_aspect)))
        x += max(0, (w - new_w) // 2)
        w = min(w, new_w)
    else:
        new_h = max(16, int(round(w / target_aspect)))
        y += max(0, (h - new_h) // 2)
        h = min(h, new_h)
    return x, y, w, h


def make_plan(h_right_to_left: np.ndarray, width: int, height: int,
              max_canvas_w: int, max_canvas_h: int, output_aspect: float,
              use_cuda: bool, log: logging.Logger) -> StitchPlan:
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
                output_aspect, use_cuda, log,
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
    feather_px = env_int("RCPILOT_STITCH_FEATHER_PX", 8)
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
    if env_bool("RCPILOT_STITCH_KEEP_ASPECT", True):
        crop_x, crop_y, crop_w, crop_h = fit_crop_to_aspect(
            (crop_x, crop_y, crop_w, crop_h), output_aspect,
        )

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
        "crop_aspect=%.3f target_aspect=%.3f overlap_pixels=%d "
        "feather_px=%d exposure_match=%s cuda_warp=%s",
        canvas_w, canvas_h, crop_x, crop_y, crop_w, crop_h, tx, ty,
        crop_w / float(max(1, crop_h)), output_aspect, overlap_pixels,
        feather_px, exposure_match, cuda_ok,
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
                   out_w: int, out_h: int, accel: str,
                   log: logging.Logger) -> None:
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

    # Cheap exposure sampling mask: pixels where both contribute meaningfully.
    overlap_out = ((lw_u8 > 32) & (rw_u8 > 32)).astype(np.uint8) * 255
    plan.fast_overlap_mask_out = overlap_out

    plan.fast_map_left_x = left_sx
    plan.fast_map_left_y = left_sy
    plan.fast_map_right_x = rsx
    plan.fast_map_right_y = rsy
    plan.fast_accel = "cpu"

    if accel in {"auto", "opencv-cuda"}:
        if opencv_cuda_remap_available():
            try:
                plan.fast_cuda_map_left_x = upload_cuda_array(left_sx)
                plan.fast_cuda_map_left_y = upload_cuda_array(left_sy)
                plan.fast_cuda_map_right_x = upload_cuda_array(rsx)
                plan.fast_cuda_map_right_y = upload_cuda_array(rsy)
                plan.fast_accel = "opencv-cuda"
            except (AttributeError, cv2.error) as exc:
                log.warning("OpenCV CUDA map upload failed; using CPU remap: %s", exc)
                plan.fast_accel = "cpu"
        elif accel == "opencv-cuda":
            log.warning("RCPILOT_STITCH_ACCEL=opencv-cuda requested, but OpenCV CUDA remap is unavailable")

    log.info(
        "fast-path baked: out=%dx%d remap_tables=2 overlap_out_pixels=%d "
        "exposure_every_n=%d accel=%s",
        out_w, out_h, int((overlap_out > 0).sum()), plan.fast_exposure_every_n,
        plan.fast_accel,
    )


def finish_fast_frame(warped_left: np.ndarray, warped_right: np.ndarray,
                      plan: StitchPlan, frame_idx: int) -> np.ndarray:
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

    # Vectorized uint8 blend: each pixel = (src * weight) / 255, summed.
    # cv2.multiply with scale=1/255 saturates back to uint8 in one SIMD pass,
    # cv2.add saturates the sum. Both inputs are (H, W, 3) uint8, no broadcast.
    left_contrib = cv2.multiply(
        warped_left, plan.fast_weight_left_bgr, scale=1.0 / 255.0,
    )
    right_contrib = cv2.multiply(
        warped_right, plan.fast_weight_right_bgr, scale=1.0 / 255.0,
    )
    return cv2.add(left_contrib, right_contrib)


def cuda_remap_fast(left: np.ndarray, right: np.ndarray,
                    plan: StitchPlan) -> Tuple[np.ndarray, np.ndarray]:
    gpu_left = cv2.cuda_GpuMat()
    gpu_right = cv2.cuda_GpuMat()
    gpu_left.upload(left)
    gpu_right.upload(right)
    warped_left_gpu = cv2.cuda.remap(
        gpu_left, plan.fast_cuda_map_left_x, plan.fast_cuda_map_left_y,
        cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    warped_right_gpu = cv2.cuda.remap(
        gpu_right, plan.fast_cuda_map_right_x, plan.fast_cuda_map_right_y,
        cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    return warped_left_gpu.download(), warped_right_gpu.download()


def cpu_remap_fast(left: np.ndarray, right: np.ndarray,
                   plan: StitchPlan) -> Tuple[np.ndarray, np.ndarray]:
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
    return warped_left, warped_right


def stitch_frame_fast(left: np.ndarray, right: np.ndarray, plan: StitchPlan,
                      out_w: int, out_h: int, frame_idx: int) -> np.ndarray:
    """Per-frame fast path: two remaps, sampled exposure, vectorized blend."""
    if plan.fast_accel == "opencv-cuda":
        try:
            warped_left, warped_right = cuda_remap_fast(left, right, plan)
        except (AttributeError, cv2.error) as exc:
            logging.getLogger("stitch").warning(
                "OpenCV CUDA remap failed; falling back to CPU remap: %s", exc,
            )
            plan.fast_accel = "cpu"
            warped_left, warped_right = cpu_remap_fast(left, right, plan)
    else:
        warped_left, warped_right = cpu_remap_fast(left, right, plan)
    return finish_fast_frame(warped_left, warped_right, plan, frame_idx)


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
                                int(data.get("matches", 0)), "cached",
                                model=str(data.get("model", "homography")),
                                reproj_error_px=float(data.get("reproj_error_px", 0.0)))
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
            "model": result.model,
            "reproj_error_px": result.reproj_error_px,
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

    samples = env_int("RCPILOT_STITCH_CALIBRATION_SAMPLES", 20)
    min_matches = env_int("RCPILOT_STITCH_MIN_MATCHES", 10)
    feature_scale = env_float("RCPILOT_STITCH_FEATURE_SCALE", 0.75)
    best: Optional[HomographyResult] = None
    last_left: Optional[np.ndarray] = None
    last_right: Optional[np.ndarray] = None
    last_left_count = -1
    last_right_count = -1
    no_frame_samples = 0

    model = os.getenv("RCPILOT_STITCH_MODEL", "affine").strip().lower()
    max_reproj = env_float("RCPILOT_STITCH_MAX_REPROJ_ERROR_PX", 12.0)
    log.info(
        "estimating stitch alignment from %d fresh frame pairs "
        "(model=%s min_matches=%d feature_scale=%.2f max_error=%.1fpx)",
        samples, model, min_matches, feature_scale, max_reproj,
    )
    for idx in range(samples):
        left, last_left_count = left_reader.wait_frame(
            timeout_s=3.0, after_count=last_left_count,
        )
        right, last_right_count = right_reader.wait_frame(
            timeout_s=3.0, after_count=last_right_count,
        )
        if left is None or right is None:
            no_frame_samples += 1
            log.warning("calibration sample %d: no frame yet", idx + 1)
            if no_frame_samples >= 5 and last_left is None and last_right is None:
                raise SystemExit(
                    "No frames arrived from the CSI cameras. Argus may still "
                    "be holding a stale capture session from an earlier crash. "
                    "Stop this process and run: sudo systemctl restart "
                    "nvargus-daemon"
                )
            continue
        no_frame_samples = 0
        last_left = left
        last_right = right
        result = estimate_homography(left, right, feature_scale, min_matches, log)
        if result is None:
            continue
        log.info(
            "calibration sample %d: %s/%s matches=%d inliers=%d "
            "ratio=%.2f error=%.1fpx",
            idx + 1, result.detector, result.model, result.matches,
            result.inliers, result.ratio, result.reproj_error_px,
        )
        if (
            best is None
            or result.inliers > best.inliers
            or (
                result.inliers >= best.inliers - 2
                and result.reproj_error_px < best.reproj_error_px
            )
        ):
            best = result
        time.sleep(0.12)

    if best is not None:
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
        return HomographyResult(
            approximate_homography(width, overlap), 0, 0, "fallback",
            model="fallback", reproj_error_px=0.0,
        )

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
    stitch_accel = os.getenv("RCPILOT_STITCH_ACCEL", "auto").strip().lower()
    if stitch_accel not in {"auto", "cpu", "opencv-cuda"}:
        raise SystemExit(
            "RCPILOT_STITCH_ACCEL must be auto, cpu, or opencv-cuda; "
            f"got {stitch_accel!r}"
        )
    exposure_every_n = env_int("RCPILOT_STITCH_EXPOSURE_EVERY_N", 15)

    if left_sensor == right_sensor:
        raise SystemExit("left and right sensor ids must differ")

    log.info(
        "real stitch sender: sensors %d/%d capture=%dx%d@%d output=%dx%d "
        "encoder=%s bitrate=%d fast=%s accel=%s exposure_every_n=%d -> %s:%d",
        left_sensor, right_sensor, width, height, fps, out_width, out_height,
        encoder, bitrate, use_fast, stitch_accel, exposure_every_n,
        cockpit_ip, port,
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
            "stitch alignment: detector=%s model=%s matches=%d inliers=%d "
            "ratio=%.2f error=%.1fpx",
            h_result.detector, h_result.model, h_result.matches,
            h_result.inliers, h_result.ratio, h_result.reproj_error_px,
        )
        plan = make_plan(
            h_result.h_right_to_left, width, height,
            max_canvas_w=width * 4,
            max_canvas_h=height * 3,
            output_aspect=out_width / float(out_height),
            use_cuda=use_cuda,
            log=log,
        )
        if h_result.detector not in {"cached", "fallback"}:
            save_calibration(calibration_path(), width, height, h_result, log)
        plan.fast_exposure_every_n = max(0, exposure_every_n)
        if use_fast:
            bake_fast_path(
                plan, width, height, out_width, out_height, stitch_accel, log,
            )
        preview_left, _ = left_reader.latest()
        preview_right, _ = right_reader.latest()
        if preview_left is not None and preview_right is not None:
            if use_fast:
                preview = stitch_frame_fast(
                    preview_left, preview_right, plan,
                    out_width, out_height, frame_idx=0,
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
                        "accel=%s gain_bgr=(%.2f,%.2f,%.2f)",
                        frame_count / (now - last_log), left_count, right_count,
                        plan.fast_accel if use_fast else "slow",
                        gain[0], gain[1], gain[2],
                    )
                else:
                    log.info(
                        "streaming stitched panorama: %.1f fps left=%d right=%d accel=%s",
                        frame_count / (now - last_log), left_count, right_count,
                        plan.fast_accel if use_fast else "slow",
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
