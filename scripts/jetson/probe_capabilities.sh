#!/bin/bash
# Run on the bench Jetson to confirm what hardware paths are actually
# usable from Python. Output goes to /tmp/rcpilot_jetson_probe.log so you
# can `cat` it after.
LOG=/tmp/rcpilot_jetson_probe.log
exec > "$LOG" 2>&1

echo "=== JetPack / L4T version ==="
cat /etc/nv_tegra_release 2>/dev/null || echo "no /etc/nv_tegra_release"
echo

echo "=== uname ==="
uname -a
echo

echo "=== CUDA toolkit ==="
nvcc --version 2>&1 | tail -3
ls /usr/local/cuda* 2>/dev/null | head -5
echo

echo "=== VPI (NVIDIA Vision Programming Interface) ==="
dpkg -l | grep -i vpi || echo "no vpi packages"
echo
echo "VPI Python module:"
python3 - <<'PY' 2>&1
try:
    import vpi
    print(f"vpi version: {getattr(vpi, '__version__', 'unknown')}")
    backends = [name for name in dir(vpi.Backend) if not name.startswith('_') and name.isupper()]
    print(f"backends exposed in vpi.Backend: {backends}")
except Exception as e:
    print(f"vpi import/probe failed: {type(e).__name__}: {e}")
PY
echo

echo "=== OpenCV ==="
python3 -c "import cv2; print('cv2 version:', cv2.__version__); info = cv2.getBuildInformation(); print('CUDA:', 'YES' in info[info.find('CUDA'):info.find('CUDA')+200] if 'CUDA' in info else 'NO')" 2>&1
python3 -c "import cv2; bi = cv2.getBuildInformation(); [print(line) for line in bi.split(chr(10)) if 'CUDA' in line or 'cuDNN' in line or 'NVIDIA' in line]" 2>&1
echo

echo "=== GStreamer plugins (NV-accelerated) ==="
gst-inspect-1.0 nvarguscamerasrc 2>&1 | head -3
gst-inspect-1.0 nvvidconv 2>&1 | head -3
gst-inspect-1.0 nvv4l2h264enc 2>&1 | head -3
gst-inspect-1.0 nvcompositor 2>&1 | head -3
echo

echo "=== NVMM-aware appsink/appsrc ==="
gst-inspect-1.0 nveglglessink 2>&1 | head -3
gst-inspect-1.0 appsink 2>&1 | grep -i 'caps' | head -3
echo

echo "=== Tegra stats snapshot ==="
which tegrastats && timeout 1 tegrastats || echo "no tegrastats"
echo

echo "=== Python imports for VPI workflow ==="
python3 - <<'PY'
import sys
mods = ['vpi', 'numpy', 'cv2', 'cuda']
for m in mods:
    try:
        __import__(m)
        print(f"{m}: OK")
    except Exception as e:
        print(f"{m}: FAIL — {type(e).__name__}: {e}")
PY
echo

echo "=== cv2.cuda micro-benchmarks (Codex's existing path) ==="
python3 - <<'PY' 2>&1
try:
    import cv2, numpy as np, time
    if not (hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0):
        print("cv2.cuda not available — skipping benchmark")
        raise SystemExit
    h_in, w_in = 720, 1280
    h_out, w_out = 720, 2560
    src = np.random.randint(0, 256, (h_in, w_in, 3), dtype=np.uint8)
    weight = np.full((h_out, w_out, 3), 128, dtype=np.uint8)
    map_x = np.tile(np.linspace(0, w_in - 1, w_out, dtype=np.float32), (h_out, 1))
    map_y = np.tile(np.linspace(0, h_in - 1, h_out, dtype=np.float32).reshape(-1, 1), (1, w_out))
    g_src = cv2.cuda_GpuMat(); g_src.upload(src)
    g_mx = cv2.cuda_GpuMat(); g_mx.upload(map_x)
    g_my = cv2.cuda_GpuMat(); g_my.upload(map_y)
    g_w = cv2.cuda_GpuMat(); g_w.upload(weight)
    # Warmup
    for _ in range(3):
        g_dst = cv2.cuda.remap(g_src, g_mx, g_my, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    cv2.cuda.Stream.Null().waitForCompletion()
    def bench(label, fn, n=30):
        # Warm
        for _ in range(2): fn()
        cv2.cuda.Stream.Null().waitForCompletion()
        t0 = time.perf_counter()
        for _ in range(n): fn()
        cv2.cuda.Stream.Null().waitForCompletion()
        ms = (time.perf_counter() - t0) / n * 1000
        print(f"  {label:42s} {ms:6.2f} ms")

    print("Codex-style: alloc fresh GpuMat per frame, upload, remap, download, blend on CPU")
    def codex_path():
        gl = cv2.cuda_GpuMat(); gr = cv2.cuda_GpuMat()
        gl.upload(src); gr.upload(src)
        wl = cv2.cuda.remap(gl, g_mx, g_my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        wr = cv2.cuda.remap(gr, g_mx, g_my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        l = wl.download(); r = wr.download()
        cv2.add(cv2.multiply(l, weight, scale=1/255.0), cv2.multiply(r, weight, scale=1/255.0))
    bench("codex CUDA-remap + CPU-blend round trip", codex_path)

    print("\nProposed: persist GpuMats, blend on GPU, single download")
    g_wl = cv2.cuda_GpuMat(); g_wr = cv2.cuda_GpuMat()
    g_warpL = cv2.cuda_GpuMat(); g_warpR = cv2.cuda_GpuMat()
    g_blendL = cv2.cuda_GpuMat(); g_blendR = cv2.cuda_GpuMat()
    g_out = cv2.cuda_GpuMat()
    g_left_in = cv2.cuda_GpuMat(); g_right_in = cv2.cuda_GpuMat()
    g_left_in.upload(src); g_right_in.upload(src)
    def proposed_path():
        g_left_in.upload(src); g_right_in.upload(src)
        cv2.cuda.remap(g_left_in, g_mx, g_my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0, dst=g_warpL)
        cv2.cuda.remap(g_right_in, g_mx, g_my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0, dst=g_warpR)
        cv2.cuda.multiply(g_warpL, g_w, scale=1/255.0, dst=g_blendL)
        cv2.cuda.multiply(g_warpR, g_w, scale=1/255.0, dst=g_blendR)
        cv2.cuda.add(g_blendL, g_blendR, dst=g_out)
        g_out.download()
    try:
        bench("persisted GpuMat + GPU blend round trip", proposed_path)
    except Exception as e:
        print(f"  proposed_path failed: {type(e).__name__}: {e}")

    print("\nCPU-only baseline: cv2.remap + cv2.multiply + cv2.add")
    def cpu_path():
        l = cv2.remap(src, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        r = cv2.remap(src, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        cv2.add(cv2.multiply(l, weight, scale=1/255.0), cv2.multiply(r, weight, scale=1/255.0))
    bench("CPU remap+blend round trip", cpu_path)

    print("\nIsolated upload / download cost (BGR 1280x720 / 2560x720):")
    def upload_only():
        g = cv2.cuda_GpuMat(); g.upload(src)
    bench("upload 1280x720 BGR (per frame)", upload_only)
    def download_only():
        g_warpL.download()
    bench("download 2560x720 BGR (per frame)", download_only)
except SystemExit:
    pass
except Exception as e:
    print(f"cv2.cuda bench failed: {type(e).__name__}: {e}")
PY
echo

echo "=== VPI remap micro-benchmark (proper API) ==="
python3 - <<'PY' 2>&1
try:
    import vpi, numpy as np, time
    src_np = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
    out_h, out_w = 720, 2560
    # Build a dense warp map: identity resize from 1280 -> 2560.
    grid = vpi.WarpGrid((out_w, out_h))
    warpmap = vpi.WarpMap(grid)
    # Fill the warpmap by accessing the underlying numpy view.
    arr = np.asarray(warpmap)  # shape (out_h, out_w, 2) — (x, y)
    arr[..., 0] = np.tile(np.linspace(0, 1280, out_w, dtype=np.float32), (out_h, 1))
    arr[..., 1] = np.tile(np.linspace(0, 720, out_h, dtype=np.float32).reshape(-1, 1), (1, out_w))

    src = vpi.asimage(src_np)
    out = vpi.Image((out_w, out_h), src.format)
    for _ in range(3):
        with vpi.Backend.CUDA:
            src.remap(warpmap, interp=vpi.Interp.LINEAR, out=out)
        with out.lock_cpu():
            pass
    n = 30
    t0 = time.perf_counter()
    for _ in range(n):
        with vpi.Backend.CUDA:
            src.remap(warpmap, interp=vpi.Interp.LINEAR, out=out)
        with out.lock_cpu():
            pass
    elapsed = (time.perf_counter() - t0) / n * 1000
    print(f"VPI CUDA remap (1280x720 -> 2560x720) round trip: {elapsed:.2f} ms/frame")

    # Enumerate VPI blend-relevant ops so stitch_video.vpi_blend_fast knows
    # what's actually available on this JetPack.
    print()
    print("VPI top-level ops (blend/composite/arithmetic candidates):")
    callables = sorted(
        n for n in dir(vpi)
        if callable(getattr(vpi, n, None))
        and not n.startswith("_")
        and any(k in n.lower() for k in
                ("blend", "compos", "mul", "add", "weight", "alpha", "arith", "convex"))
    )
    for n in callables:
        print(f"  vpi.{n}")
    img_methods = sorted(
        m for m in dir(vpi.Image)
        if not m.startswith("_")
        and any(k in m.lower() for k in
                ("blend", "compos", "mul", "add", "weight", "alpha", "arith", "convex"))
    )
    print(f"vpi.Image methods of interest:")
    for m in img_methods:
        print(f"  vpi.Image.{m}")
    print()
    backends = [name for name in dir(vpi.Backend)
                if not name.startswith("_") and name.isupper()]
    print(f"vpi.Backend members: {backends}")
except Exception as e:
    print(f"VPI bench failed: {type(e).__name__}: {e}")
PY

echo

echo
echo "=== tegrastats snapshot (5 seconds at 500ms intervals) ==="
if [ -x /usr/bin/tegrastats ]; then
    /usr/bin/tegrastats --interval 500 --start
    sleep 5
    /usr/bin/tegrastats --stop
else
    echo "tegrastats not found"
fi

echo "=== done. tail -60 $LOG to see the result ==="
