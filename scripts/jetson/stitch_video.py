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
    left_weight: np.ndarray
    right_weight: np.ndarray
    use_cuda: bool


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

    cuda_ok = False
    if use_cuda and hasattr(cv2, "cuda"):
        try:
            cuda_ok = cv2.cuda.getCudaEnabledDeviceCount() > 0
        except cv2.error:
            cuda_ok = False

    log.info(
        "stitch canvas=%dx%d left_offset=(%d,%d) cuda_warp=%s",
        canvas_w, canvas_h, tx, ty, cuda_ok,
    )
    return StitchPlan(
        h_canvas=h_canvas.astype(np.float64),
        left_x=tx,
        left_y=ty,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        left_weight=left_weight,
        right_weight=right_weight,
        use_cuda=cuda_ok,
    )


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

    stitched = (
        canvas_l.astype(np.float32) * plan.left_weight[..., None]
        + canvas_r.astype(np.float32) * plan.right_weight[..., None]
    )
    stitched = np.clip(stitched, 0, 255).astype(np.uint8)

    if stitched.shape[1] != out_w or stitched.shape[0] != out_h:
        stitched = cv2.resize(stitched, (out_w, out_h), interpolation=cv2.INTER_AREA)
    return stitched


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

    if left_sensor == right_sensor:
        raise SystemExit("left and right sensor ids must differ")

    log.info(
        "real stitch sender: sensors %d/%d capture=%dx%d@%d output=%dx%d "
        "encoder=%s bitrate=%d -> %s:%d",
        left_sensor, right_sensor, width, height, fps, out_width, out_height,
        encoder, bitrate, cockpit_ip, port,
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
        preview_left, _ = left_reader.latest()
        preview_right, _ = right_reader.latest()
        if preview_left is not None and preview_right is not None:
            preview = stitch_frame(preview_left, preview_right, plan, out_width, out_height)
            save_debug_image("stitched_preview.jpg", preview, log)

        writer = cv2.VideoWriter(
            writer_pipeline, cv2.CAP_GSTREAMER, 0, float(fps),
            (out_width, out_height), True,
        )
        if not writer.isOpened():
            raise SystemExit("could not open RTP writer pipeline")
        log.info("RTP writer open")

        frame_count = 0
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
            stitched = stitch_frame(left, right, plan, out_width, out_height)
            writer.write(stitched)
            frame_count += 1

            now = time.monotonic()
            if now - last_log >= 2.0:
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
