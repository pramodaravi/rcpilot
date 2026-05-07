#!/usr/bin/env python3
"""rcpilot Phase 0 — single-camera charuco intrinsic calibration.

Why this script exists. The cylindrical stitcher needs honest per-camera
intrinsics: focal length f, principal point (cx, cy), and lens distortion
coefficients. Everything that came before this file in the rcpilot codebase
was empirical "fit a homography from features and hope" — that conflates
intrinsics with extrinsics with scene depth and produces brittle results.
This separates the per-camera optical model (what this script measures)
from the cross-camera geometry (what the cylindrical stitcher infers from
the relative pose).

Usage on the Jetson:

    # Print a charuco board PDF (one time per project — do this on Windows,
    # actually print it on letter or A4 paper, mount it flat on a clipboard):
    python3 calibrate_intrinsics.py --board-pdf /tmp/board.pdf

    # Calibrate camera 0:
    python3 calibrate_intrinsics.py \
        --sensor-id 0 \
        --frames 25 \
        --output /home/adm2n/rcpilot/config/intrinsics_cam0.json

    # Calibrate camera 1:
    python3 calibrate_intrinsics.py --sensor-id 1 \
        --output /home/adm2n/rcpilot/config/intrinsics_cam1.json

The capture flow: shows the live frame on stdout dimensions only (no GUI),
detects the charuco board, accepts a frame when the board is visible with
a good spread, repeats until --frames frames are accepted. Then runs
cv2.calibrateCameraCharuco and writes a JSON file with the model.

Output JSON:
    {
        "width": 1280, "height": 720,
        "f": 1095.2, "cx": 632.1, "cy": 358.4,
        "distortion": [k1, k2, p1, p2, k3],
        "reproj_error_px": 0.42,
        "frames_used": 25,
        "board": {"squares_x": 5, "squares_y": 7,
                  "square_length_m": 0.030, "marker_length_m": 0.022,
                  "aruco_dict": "DICT_5X5_100"}
    }

Honest about the constraint: this calibration is per-camera (intrinsics
only). Cross-camera extrinsics (relative rotation/translation between cam0
and cam1) are estimated separately by the cylindrical stitcher's online
KLT loop. Doing it this way means the stitcher's job is small (3 DoF)
instead of huge (8 DoF) and stays well-conditioned even on low-texture
scenes.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


# Charuco board parameters. Squares are 30 mm wide, ArUco markers are 22 mm
# inside each square. 5 squares wide x 7 tall fits cleanly on letter/A4.
# DICT_5X5_100 is small enough to detect at 1280x720 from arm's length.
DEFAULT_BOARD = {
    "squares_x": 5,
    "squares_y": 7,
    "square_length_m": 0.030,
    "marker_length_m": 0.022,
    "aruco_dict": "DICT_5X5_100",
}


def get_aruco_dict(name: str):
    """cv2.aruco moved its dictionary registry between OpenCV releases. This
    helper hides the API drift."""
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
    if hasattr(cv2.aruco, "Dictionary_get"):
        return cv2.aruco.Dictionary_get(getattr(cv2.aruco, name))
    raise SystemExit("cv2.aruco has no recognizable dictionary accessor")


def make_charuco_board(spec: dict):
    aruco_dict = get_aruco_dict(spec["aruco_dict"])
    if hasattr(cv2.aruco, "CharucoBoard_create"):
        return cv2.aruco.CharucoBoard_create(
            spec["squares_x"], spec["squares_y"],
            float(spec["square_length_m"]),
            float(spec["marker_length_m"]),
            aruco_dict,
        )
    return cv2.aruco.CharucoBoard(
        (spec["squares_x"], spec["squares_y"]),
        float(spec["square_length_m"]),
        float(spec["marker_length_m"]),
        aruco_dict,
    )


def detect_charuco(gray: np.ndarray, board, aruco_dict
                   ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Detect ChArUco corners in a grayscale frame. Returns (corners, ids) or
    (None, None) if the board wasn't found with enough corners to be useful.

    "Useful" = at least 8 corners on the inner grid. Below that, the per-frame
    pose has too few constraints to inform the calibration.
    """
    if hasattr(cv2.aruco, "ArucoDetector"):
        params = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, params)
        marker_corners, marker_ids, _ = detector.detectMarkers(gray)
    else:
        params = cv2.aruco.DetectorParameters_create()
        marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(
            gray, aruco_dict, parameters=params,
        )
    if marker_ids is None or len(marker_ids) < 4:
        return None, None
    if hasattr(cv2.aruco, "interpolateCornersCharuco"):
        ret, ch_corners, ch_ids = cv2.aruco.interpolateCornersCharuco(
            marker_corners, marker_ids, gray, board,
        )
    else:
        # Older API
        ret, ch_corners, ch_ids = cv2.aruco.interpolateCornersCharuco(
            marker_corners, marker_ids, gray, board, None, None,
        )
    if ret is None or ch_ids is None or len(ch_ids) < 8:
        return None, None
    return ch_corners, ch_ids


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


def estimate_spread(corners: np.ndarray, w: int, h: int) -> float:
    """How big (relative to the frame) is the bounding box of detected
    corners? Frames where the board fills 0% of the view contribute nothing
    to the calibration; frames where it fills 30%+ are gold."""
    pts = corners.reshape(-1, 2)
    dx = pts[:, 0].max() - pts[:, 0].min()
    dy = pts[:, 1].max() - pts[:, 1].min()
    return float((dx * dy) / max(1.0, w * h))


def write_board_pdf(spec: dict, path: Path) -> None:
    """Render the charuco board to a PDF for printing. Standalone — no
    camera needed."""
    board = make_charuco_board(spec)
    # 300 DPI, board fills roughly 6"x8"
    px_per_inch = 300
    sx, sy = spec["squares_x"], spec["squares_y"]
    sq_m = spec["square_length_m"]
    in_per_m = 39.3701
    img_w = int(sx * sq_m * in_per_m * px_per_inch)
    img_h = int(sy * sq_m * in_per_m * px_per_inch)
    if hasattr(board, "generateImage"):
        img = board.generateImage((img_w, img_h), marginSize=10)
    else:
        img = board.draw((img_w, img_h), marginSize=10)
    # Save as PNG; user prints as image. (PDF generation in cv2 is not native;
    # the PNG prints fine on letter/A4 with default print sizing.)
    out_png = path.with_suffix(".png")
    cv2.imwrite(str(out_png), img)
    print(f"Charuco board image saved: {out_png}")
    print(
        f"Print on letter/A4 paper at default scale. The "
        f"{sx}x{sy} squares are {spec['square_length_m']*1000:.0f} mm each."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensor-id", type=int, default=0)
    parser.add_argument("--sensor-mode", type=int, default=4,
                        help="IMX219 sensor mode 4 = 1280x720@60")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=25,
                        help="Number of valid charuco views to collect")
    parser.add_argument("--min-spread", type=float, default=0.05,
                        help="Reject frames where the board fills <5% of view")
    parser.add_argument("--accept-every-s", type=float, default=1.0,
                        help="Minimum seconds between accepted frames "
                             "(forces operator to move the board)")
    parser.add_argument("--output", type=Path,
                        default=Path("/home/adm2n/rcpilot/config/intrinsics.json"))
    parser.add_argument("--board-pdf", type=Path,
                        help="Write the board image and exit (no camera)")
    parser.add_argument("--max-wait-s", type=float, default=180.0,
                        help="Abort if --frames frames not collected in this time")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("calibrate")

    if args.board_pdf:
        write_board_pdf(DEFAULT_BOARD, args.board_pdf)
        return 0

    aruco_dict = get_aruco_dict(DEFAULT_BOARD["aruco_dict"])
    board = make_charuco_board(DEFAULT_BOARD)

    pipeline = build_capture_pipeline(
        args.sensor_id, args.width, args.height, args.fps, args.sensor_mode,
    )
    log.info("opening camera (sensor-id=%d)", args.sensor_id)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        log.error("could not open CSI camera. Pipeline: %s", pipeline)
        return 64

    accepted_corners: List[np.ndarray] = []
    accepted_ids: List[np.ndarray] = []
    last_accept_t = 0.0
    deadline = time.monotonic() + args.max_wait_s

    log.info("hold the charuco board in view; %d good frames needed", args.frames)
    while len(accepted_corners) < args.frames and time.monotonic() < deadline:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.05)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids = detect_charuco(gray, board, aruco_dict)
        now = time.monotonic()
        if corners is None or now - last_accept_t < args.accept_every_s:
            continue
        spread = estimate_spread(corners, args.width, args.height)
        if spread < args.min_spread:
            continue
        accepted_corners.append(corners)
        accepted_ids.append(ids)
        last_accept_t = now
        log.info("  accepted frame %d/%d  corners=%d  spread=%.1f%%",
                 len(accepted_corners), args.frames, len(ids), spread * 100)

    cap.release()

    if len(accepted_corners) < 6:
        log.error("only collected %d frames; need at least 6 for a stable fit",
                  len(accepted_corners))
        return 65

    log.info("running calibrateCameraCharuco on %d frames",
             len(accepted_corners))
    if hasattr(cv2.aruco, "calibrateCameraCharuco"):
        ret, K, dist, rvecs, tvecs = cv2.aruco.calibrateCameraCharuco(
            charucoCorners=accepted_corners,
            charucoIds=accepted_ids,
            board=board,
            imageSize=(args.width, args.height),
            cameraMatrix=None,
            distCoeffs=None,
        )
    else:
        # Last-resort fallback: derive object/image points and use the
        # generic cv2.calibrateCamera path.
        obj_pts = []
        img_pts = []
        all_obj = board.getChessboardCorners() if hasattr(board, "getChessboardCorners") else board.chessboardCorners
        for corners, ids in zip(accepted_corners, accepted_ids):
            ids_flat = ids.flatten()
            obj_pts.append(np.array([all_obj[i] for i in ids_flat], dtype=np.float32))
            img_pts.append(corners.reshape(-1, 1, 2))
        ret, K, dist, _, _ = cv2.calibrateCamera(
            obj_pts, img_pts, (args.width, args.height), None, None,
        )

    if not ret:
        log.error("calibrateCameraCharuco failed")
        return 66

    # K is [[f, 0, cx], [0, f, cy], [0, 0, 1]]. Average fx/fy if they differ
    # slightly (square pixels assumed for IMX219).
    f = (float(K[0, 0]) + float(K[1, 1])) / 2.0
    cx = float(K[0, 2])
    cy = float(K[1, 2])
    distortion = [float(x) for x in dist.flatten().tolist()]

    output = {
        "width": args.width,
        "height": args.height,
        "f": f,
        "fx": float(K[0, 0]),
        "fy": float(K[1, 1]),
        "cx": cx,
        "cy": cy,
        "distortion": distortion,
        "reproj_error_px": float(ret),
        "frames_used": len(accepted_corners),
        "board": DEFAULT_BOARD,
        "sensor_id": args.sensor_id,
        "sensor_mode": args.sensor_mode,
        "calibrated_at": time.time(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    log.info("wrote %s", args.output)
    log.info("  f=%.1f  cx=%.1f  cy=%.1f  reproj=%.2fpx  frames=%d",
             f, cx, cy, ret, len(accepted_corners))
    return 0


if __name__ == "__main__":
    sys.exit(main())
