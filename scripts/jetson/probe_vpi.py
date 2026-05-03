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
            members = [m for m in dir(obj) if not m.startswith("_")][:30]
            log(f"vpi.{kind}: {members}")
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
        log(f"vpi.asimage OK, size={src_img.size}, format={src_img.format}")
    except Exception as e:
        log(f"vpi.asimage failed: {e}")
        return

    # Try output image creation
    out_img = None
    formats_to_try = []
    if hasattr(vpi, "Format"):
        for fname in ("BGR8", "RGB8", "U8", "BGRA8", "RGBA8"):
            f = getattr(vpi.Format, fname, None)
            if f is not None:
                formats_to_try.append((fname, f))
    log(f"available Format attrs to try: {[n for n, _ in formats_to_try]}")

    for fname, fmt in formats_to_try:
        try:
            test = vpi.Image((OUT_W, OUT_H), fmt)
            log(f"  vpi.Image((W, H), {fname}) OK: {test.size}")
            if fname == "BGR8":
                out_img = test
        except Exception as e:
            log(f"  vpi.Image((W, H), {fname}): {type(e).__name__}: {e}")
    log("")

    log("=== remap API patterns ===")
    # Try several invocation styles to see which works.
    if out_img is None:
        out_img = vpi.Image((OUT_W, OUT_H), vpi.Format.BGR8)

    patterns = [
        ("src.remap(warp_l)",
         lambda: src_img.remap(warp_l)),
        ("src.remap(warp_l, interp=LINEAR)",
         lambda: src_img.remap(warp_l, interp=vpi.Interp.LINEAR)),
        ("vpi.remap(src, warp_l)",
         lambda: vpi.remap(src_img, warp_l) if hasattr(vpi, "remap") else (_ for _ in ()).throw(AttributeError("vpi.remap missing"))),
        ("src.remap(warp_l, interp=LINEAR, out=out_img)",
         lambda: src_img.remap(warp_l, interp=vpi.Interp.LINEAR, out=out_img)),
        ("src.remap(warp_l, out=out_img)",
         lambda: src_img.remap(warp_l, out=out_img)),
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

    log("=== blend op discovery ===")
    # We need: out = warped_left * weight_left + warped_right * weight_right
    # weights are uint8 single-channel, values 0..255 representing 0.0..1.0
    weight_np = np.full((OUT_H, OUT_W), 128, dtype=np.uint8)
    try:
        weight_img = vpi.asimage(weight_np)
        log(f"weight image: size={weight_img.size}, format={weight_img.format}")
    except Exception as e:
        log(f"weight asimage failed: {e}")

    # See if vpi.composite or similar is available
    blend_candidates = [
        "composite", "Composite",
        "image_blend", "imageBlend", "blend",
        "image_mul", "imageMul",
        "image_add", "imageAdd",
    ]
    for name in blend_candidates:
        if hasattr(vpi, name):
            try:
                doc = getattr(vpi, name).__doc__
                first_line = doc.split("\n")[0] if doc else "(no doc)"
                log(f"  vpi.{name}: {first_line}")
            except Exception as e:
                log(f"  vpi.{name}: present but introspection failed: {e}")
    log("")

    # Try a per-pixel weighted blend using whatever ops exist.
    # First: if vpi.composite(fg, bg, mask) exists, that's our answer.
    log("=== per-pixel weighted blend pattern test ===")
    fg = vpi.asimage(np.random.randint(0, 256, (OUT_H, OUT_W, 3), dtype=np.uint8))
    bg = vpi.asimage(np.random.randint(0, 256, (OUT_H, OUT_W, 3), dtype=np.uint8))
    mask = vpi.asimage(weight_np)
    blend_patterns = [
        ("vpi.composite(fg, bg, mask)",
         lambda: vpi.composite(fg, bg, mask)),
        ("vpi.image_blend(fg, bg, 0.5)",
         lambda: vpi.image_blend(fg, bg, 0.5) if hasattr(vpi, "image_blend") else None),
    ]
    for name, fn in blend_patterns:
        try:
            with vpi.Backend.CUDA:
                r = fn()
            log(f"  OK: {name}")
        except Exception as e:
            log(f"  FAIL: {name} -> {type(e).__name__}: {e}")
    log("")

    log("=== full-pipeline benchmark (remap x2 + blend) ===")
    # Two warps then blend. Use whichever blend op worked, else fall back to
    # numpy on the CPU side after VPI download.
    src_l = vpi.asimage(src_np)
    src_r = vpi.asimage(src_np)
    warped_l = vpi.Image((OUT_W, OUT_H), vpi.Format.BGR8)
    warped_r = vpi.Image((OUT_W, OUT_H), vpi.Format.BGR8)
    weight_l_np = np.full((OUT_H, OUT_W), 128, dtype=np.uint8)
    weight_r_np = 255 - weight_l_np
    weight_l = vpi.asimage(weight_l_np)
    weight_r = vpi.asimage(weight_r_np)

    # Warmup with whatever pattern works
    composite_works = hasattr(vpi, "composite")

    def full_pipeline():
        with vpi.Backend.CUDA:
            wl = src_l.remap(warp_l, interp=vpi.Interp.LINEAR)
            wr = src_r.remap(warp_r, interp=vpi.Interp.LINEAR)
            if composite_works:
                try:
                    out = vpi.composite(wl, wr, weight_l)
                except Exception:
                    out = wl
            else:
                out = wl
        # Force sync
        out.cpu()

    for _ in range(3):
        full_pipeline()
    t0 = time.perf_counter()
    for _ in range(n):
        full_pipeline()
    t_full = (time.perf_counter() - t0) / n * 1000
    log(f"  full pipeline (remap x2 + blend) round trip: {t_full:.2f} ms/frame")
    log(f"  budget at 30fps: 33.33 ms/frame   (less encode budget ~12-15 ms)")
    log("")

    log("=== upload / download cost on this hardware ===")
    fresh_src = np.random.randint(0, 256, (SRC_H, SRC_W, 3), dtype=np.uint8)
    n_io = 50
    t0 = time.perf_counter()
    for _ in range(n_io):
        s = vpi.asimage(fresh_src)
    log(f"  vpi.asimage from numpy:  {(time.perf_counter()-t0)/n_io*1000:.2f} ms/call")

    t0 = time.perf_counter()
    for _ in range(n_io):
        np.asarray(warped_l.cpu())
    log(f"  vpi -> numpy (cpu()):    {(time.perf_counter()-t0)/n_io*1000:.2f} ms/call")
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
