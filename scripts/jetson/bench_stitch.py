#!/usr/bin/env python3
"""Benchmark the existing production stitcher with per-stage timing.

Imports stitch_video.py functions directly, drives them with synthetic
1280x720 BGR frames, and prints how long each stage actually takes on
this Jetson. No camera, no encoder, no GStreamer — just the math and
the cv2/numpy work.

Goal: find out whether the choppy fps is caused by the remap, the
blend, the exposure-match scan, or something else.
"""
import logging
import os
import sys
import time

import numpy as np

# Make stitch_video.py importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Don't run the main entry point; just import the functions.
import stitch_video as sv  # type: ignore

LOG_PATH = "/tmp/rcpilot_bench_stitch.log"


def main():
    sys.stdout = open(LOG_PATH, "w", buffering=1)
    log = logging.getLogger("bench")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=== bench_stitch.py — production stitcher per-stage timing ===")
    print()

    SRC_W, SRC_H = 1280, 720
    OUT_W, OUT_H = 2560, 720

    print(f"input  : {SRC_W}x{SRC_H} BGR uint8")
    print(f"output : {OUT_W}x{OUT_H} BGR uint8")
    print()

    # Build synthetic input frames
    rng = np.random.default_rng(42)
    left = rng.integers(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    right = rng.integers(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)

    # Build a translation-only homography for benchmarking — geometry doesn't
    # matter for timing, just shape.
    overlap_px = 448
    H = np.array(
        [[1.0, 0.0, float(SRC_W - overlap_px)],
         [0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )

    print("=== building stitch plan ===")
    plan = sv.make_plan(
        H, SRC_W, SRC_H,
        max_canvas_w=SRC_W * 4,
        max_canvas_h=SRC_H * 3,
        output_aspect=float(OUT_W) / float(OUT_H),
        use_cuda=False,  # will be set by bake_fast_path based on env
        log=log,
    )
    accel = os.getenv("RCPILOT_STITCH_ACCEL", "auto").strip().lower()
    sv.bake_fast_path(plan, SRC_W, SRC_H, OUT_W, OUT_H, accel, log)
    print(f"plan.fast_accel = {plan.fast_accel}")
    print()

    # Per-stage timer
    def time_block(label, fn, n=50, warmup=5):
        for _ in range(warmup):
            fn()
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        ms = (time.perf_counter() - t0) / n * 1000
        return ms

    print("=== per-stage timings (median over 50 calls) ===")
    print()

    # CPU remap path
    def stage_cpu_remap():
        return sv.cpu_remap_fast(left, right, plan)
    t_cpu_remap = time_block("CPU remap x2", stage_cpu_remap)
    print(f"  CPU remap (cv2.remap x2):         {t_cpu_remap:6.2f} ms")

    # If CUDA path is set up
    if plan.fast_accel == "opencv-cuda" and hasattr(sv, "cuda_remap_fast"):
        def stage_cuda_remap():
            return sv.cuda_remap_fast(left, right, plan)
        try:
            t_cuda_remap = time_block("CUDA remap x2", stage_cuda_remap, n=30)
            print(f"  CUDA remap (cv2.cuda.remap x2):   {t_cuda_remap:6.2f} ms")
        except Exception as e:
            print(f"  CUDA remap: FAILED — {type(e).__name__}: {e}")
    else:
        print(f"  CUDA remap: skipped (accel={plan.fast_accel})")

    # Blend stage
    warped_left, warped_right = sv.cpu_remap_fast(left, right, plan)

    def stage_blend():
        # Mirror the production blend logic
        left_contrib = __import__("cv2").multiply(
            warped_left, plan.fast_weight_left_bgr, scale=1.0 / 255.0,
        )
        right_contrib = __import__("cv2").multiply(
            warped_right, plan.fast_weight_right_bgr, scale=1.0 / 255.0,
        )
        return __import__("cv2").add(left_contrib, right_contrib)
    t_blend = time_block("CPU blend (cv2.multiply x2 + cv2.add)", stage_blend)
    print(f"  CPU blend (multiply x2 + add):    {t_blend:6.2f} ms")

    # Exposure scan (only runs every N frames in production, but let's see)
    def stage_exposure_match():
        import cv2
        if plan.fast_overlap_mask_out is not None:
            cv2.mean(warped_left, mask=plan.fast_overlap_mask_out)
            cv2.mean(warped_right, mask=plan.fast_overlap_mask_out)
    t_exp = time_block("exposure scan (cv2.mean x2)", stage_exposure_match)
    print(f"  exposure scan (cv2.mean x2):      {t_exp:6.2f} ms (every N frames only)")

    # Full stitch_frame_fast end-to-end
    def stage_full():
        return sv.stitch_frame_fast(left, right, plan, OUT_W, OUT_H, frame_idx=0)
    t_full = time_block("full stitch_frame_fast", stage_full, n=30)
    print(f"  full stitch_frame_fast:           {t_full:6.2f} ms")
    print()

    print("=== summary ===")
    print(f"  remap dominates if: t_cpu_remap > t_blend  →  {'YES' if t_cpu_remap > t_blend else 'NO'}")
    print(f"  blend dominates if: t_blend > t_cpu_remap   →  {'YES' if t_blend > t_cpu_remap else 'NO'}")
    print(f"  budget at 30fps: 33.33 ms (less encode ~12-15 ms = ~20 ms left for stitch)")
    if t_full > 20.0:
        print(f"  current stitch is OVER budget — needs a faster path")
    else:
        print(f"  current stitch is within budget — choppy fps may be from elsewhere (capture? encode?)")
    print()

    print("DONE.")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.close()
        sys.stdout = sys.__stdout__
        try:
            with open(LOG_PATH) as f:
                print(f.read())
        except Exception:
            pass
