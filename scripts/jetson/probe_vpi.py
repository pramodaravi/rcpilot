#!/usr/bin/env python3
"""Figure out the right VPI Python API shape for our stitcher.

Doesn't ASSUME any specific API — tries several patterns and reports which
work. We need:
  1. A persistent WarpMap pre-built from numpy (x, y) maps.
  2. Per-frame remap from a 1280x720 BGR source to a 2560x720 BGR output,
     with the output image pre-allocated and reused.
  3. A per-pixel weighted blend of two warped images (cv2.multiply + cv2.add
     equivalent). VPI may expose this as composite(), image_mul + image_add,
     or something else.
  4. Round-trip timing on real Jetson hardware.

Output goes to /tmp/rcpilot_vpi_probe.log so you can paste it back.
"""
import json
import os
import sys
import time
import traceback

LOG_PATH = "/tmp/rcpilot_vpi_probe.log"


def log(msg=""):
    print(msg, flush=True)


def main():
    sys.stdout = open(LOG_PATH, "w", buffering=1)

    log("=== VPI top-level surface ===")
    try:
        import vpi
        log(f"vpi.__version__ = {getattr(vpi, '__version__', 'unknown')}")
    except Exception as e:
        log(f"FATAL: cannot import vpi: {e}")
        return

    top = sorted(name for name in dir(vpi) if not name.startswith("_"))
    log(f"top-level names ({len(top)}):")
    log("  " + ", ".join(top))
    log("")

    # Group what looks like ops vs types
    log("=== Image / WarpMap / Format types ===")
    for kind in ("Image", "WarpMap", "WarpGrid", "Format", "Interp",
                 "Backend", "Stream"):
        if hasattr(vpi, kind):
            obj = getattr(vpi, kind)
            members = [m for m in dir(obj) if not m.startswith("_")]
            log(f"vpi.{kind}: ({len(members)}) {members}")
    log("")

    log("=== Operations of interest ===")
    interesting = ["remap", "warp", "warp_perspective", "image_blend",
                   "imageBlend", "composite", "image_mul", "imageMul",
                   "image_add", "imageAdd", "blend", "convert_image_format",
                   "convertImageFormat", "stereo_disparity", "stereoDisparity"]
    for name in interesting:
        if hasattr(vpi, name):
            log(f"  vpi.{name}: {getattr(vpi, name)}")
    log("")

    import numpy as np

    SRC_W, SRC_H = 1280, 720
    OUT_W, OUT_H = 2560, 720

    log("=== Building warp maps ===")
    try:
        grid = vpi.WarpGrid((OUT_W, OUT_H))
        warp_l = vpi.WarpMap(grid)
        warp_r = vpi.WarpMap(grid)
        # Fill via numpy view. Identity-ish: each output pixel maps
        # to source position scaled.
        arr_l = np.asarray(warp_l)
        log(f"WarpMap numpy shape: {arr_l.shape}, dtype: {arr_l.dtype}")
        # Build a reasonable test map
        col = np.linspace(0, SRC_W - 1, OUT_W, dtype=np.float32)
        row = np.linspace(0, SRC_H - 1, OUT_H, dtype=np.float32)
        arr_l[..., 0] = np.tile(col, (OUT_H, 1))
        arr_l[..., 1] = np.tile(row.reshape(-1, 1), (1, OUT_W))
        arr_r = np.asarray(warp_r)
        arr_r[..., 0] = np.tile(col, (OUT_H, 1))
        arr_r[..., 1] = np.tile(row.reshape(-1, 1), (1, OUT_W))
        log("WarpMap fill OK")
    except Exception as e:
        log(f"WarpMap setup failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return
    log("")

    log("=== Image creation patterns ===")
    src_np = np.random.randint(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    log(f"src_np shape={src_np.shape} dtype={src_np.dtype}")

    # Pattern A: vpi.asimage(numpy)
    try:
        src_img = vpi.asimage(src_np)
        src_format = src_img.format
        log(f"vpi.asimage OK, size={src_img.size}, format={src_format}")
        log(f"  -> output image must use SAME format ({src_format})")
    except Exception as e:
        log(f"vpi.asimage failed: {e}")
        return

    # Pre-allocate output image with the SAME format as source.
    out_img = vpi.Image((OUT_W, OUT_H), src_format)
    log(f"out_img created: size={out_img.size}, format={out_img.format}")
    log("")

    log("=== remap API patterns ===")
    patterns = [
        ("src.remap(warp_l, interp=LINEAR, out=out_img)  [matched format]",
         lambda: src_img.remap(warp_l, interp=vpi.Interp.LINEAR, out=out_img)),
        ("src.remap(warp_l, out=out_img)",
         lambda: src_img.remap(warp_l, out=out_img)),
        ("vpi.dynamic_remap(src, warp_l, out=out_img)",
         lambda: vpi.dynamic_remap(src_img, warp_l, out=out_img)
         if hasattr(vpi, "dynamic_remap") else
         (_ for _ in ()).throw(AttributeError("vpi.dynamic_remap missing"))),
    ]
    working_remap = None
    for name, fn in patterns:
        try:
            with vpi.Backend.CUDA:
                result = fn()
            log(f"  OK: {name}  (result: {type(result).__name__})")
            if working_remap is None:
                working_remap = (name, fn)
        except Exception as e:
            log(f"  FAIL: {name}  -> {type(e).__name__}: {e}")
    log("")

    if hasattr(vpi, "dynamic_remap"):
        try:
            doc = vpi.dynamic_remap.__doc__ or "(no doc)"
            log(f"vpi.dynamic_remap docstring (first 30 lines):")
            for line in doc.split("\n")[:30]:
                log(f"    {line}")
            log("")
        except Exception:
            pass

    if working_remap is None:
        log("No remap pattern worked — cannot benchmark. Bailing.")
        return

    log(f"=== using working remap pattern: {working_remap[0]} ===")

    # Benchmark the working pattern.
    log("=== remap benchmark on CUDA backend ===")
    fn = working_remap[1]
    # Warmup
    for _ in range(3):
        with vpi.Backend.CUDA:
            r = fn()
        if hasattr(r, "cpu"):
            r.cpu()
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        with vpi.Backend.CUDA:
            r = fn()
    t_remap_only = (time.perf_counter() - t0) / n * 1000
    log(f"  remap submit only (no sync):  {t_remap_only:.2f} ms/frame")

    t0 = time.perf_counter()
    for _ in range(n):
        with vpi.Backend.CUDA:
            r = fn()
        if hasattr(r, "cpu"):
            r.cpu()  # forces sync
    t_remap_sync = (time.perf_counter() - t0) / n * 1000
    log(f"  remap + sync (out.cpu()):     {t_remap_sync:.2f} ms/frame")
    log("")

    log("=== Image method full enumeration (looking for blend/arith/composite) ===")
    img_methods = sorted(m for m in dir(vpi.Image) if not m.startswith("_"))
    log(f"  vpi.Image has {len(img_methods)} public methods/attrs:")
    for chunk in [img_methods[i:i+8] for i in range(0, len(img_methods), 8)]:
        log("    " + ", ".join(chunk))
    log("")

    log("=== top-level vpi op enumeration (full, not just 'interesting') ===")
    callable_top = sorted(name for name in dir(vpi)
                          if callable(getattr(vpi, name, None))
                          and not name.startswith("_")
                          and name[0].islower())  # lowercase = function
    log(f"  callable lowercase top-levels ({len(callable_top)}):")
    for chunk in [callable_top[i:i+5] for i in range(0, len(callable_top), 5)]:
        log("    " + ", ".join(chunk))
    log("")

    # See if any ops at all do per-pixel arithmetic or blend
    arith_candidates = [n for n in callable_top + img_methods
                        if any(k in n.lower() for k in
                               ("blend", "compos", "mul", "add", "weight",
                                "alpha", "convex"))]
    log(f"=== plausible arithmetic/blend ops: {arith_candidates}")
    log("")

    log("=== full proposed pipeline benchmark (VPI remap x2 + CPU blend) ===")
    # Realistic per-frame: TWO source uploads, TWO remaps on CUDA, two
    # downloads, then a CPU blend (since VPI 3.2.4 lacks per-pixel blend).
    src_l_np = np.random.randint(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    src_r_np = np.random.randint(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    warped_l = vpi.Image((OUT_W, OUT_H), src_format)
    warped_r = vpi.Image((OUT_W, OUT_H), src_format)
    weight_l_np = np.full((OUT_H, OUT_W, 3), 128, dtype=np.uint8)
    weight_r_np = 255 - weight_l_np
    import cv2

    def full_pipeline():
        sl = vpi.asimage(src_l_np)
        sr = vpi.asimage(src_r_np)
        with vpi.Backend.CUDA:
            sl.remap(warp_l, interp=vpi.Interp.LINEAR, out=warped_l)
            sr.remap(warp_r, interp=vpi.Interp.LINEAR, out=warped_r)
        # download to numpy and blend
        with warped_l.lock_cpu() as ll, warped_r.lock_cpu() as rr:
            l_arr = np.asarray(ll)
            r_arr = np.asarray(rr)
            cv2.add(
                cv2.multiply(l_arr, weight_l_np, scale=1.0/255.0),
                cv2.multiply(r_arr, weight_r_np, scale=1.0/255.0),
            )

    try:
        for _ in range(3):
            full_pipeline()
        t0 = time.perf_counter()
        for _ in range(n):
            full_pipeline()
        t_full = (time.perf_counter() - t0) / n * 1000
        log(f"  full pipeline (VPI remap x2 + CPU blend): {t_full:.2f} ms/frame")
        log(f"  budget at 30fps: 33.33 ms/frame  (less encode budget ~12-15 ms)")
    except Exception as e:
        log(f"  pipeline benchmark failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc(file=sys.stdout)
    log("")

    log("=== isolated upload / download cost ===")
    fresh_src = np.random.randint(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    n_io = 50
    t0 = time.perf_counter()
    for _ in range(n_io):
        s = vpi.asimage(fresh_src)
    log(f"  vpi.asimage from numpy:           {(time.perf_counter()-t0)/n_io*1000:.2f} ms/call")

    # Warm warped_l first
    if working_remap is not None:
        with vpi.Backend.CUDA:
            working_remap[1]()
        t0 = time.perf_counter()
        for _ in range(n_io):
            with warped_l.lock_cpu() as l_view:
                _ = np.asarray(l_view)
        log(f"  warped vpi -> numpy view:         {(time.perf_counter()-t0)/n_io*1000:.2f} ms/call")
    log("")

    log("DONE.")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.close()
        # Re-open stdout so the print outside main() works
        sys.stdout = sys.__stdout__
        try:
            with open(LOG_PATH) as f:
                print(f.read())
        except Exception:
            pass
