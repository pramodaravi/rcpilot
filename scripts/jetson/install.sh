#!/usr/bin/env bash
#
# rcpilot - Jetson install script.
#
# Idempotent: safe to re-run after pulling new code. Installs:
#   * GStreamer plugins missing from the minimal JetPack image
#   * Python deps in a venv at /opt/rcpilot
#   * The rcpilot package in editable mode
#
# Run on the Jetson, not the cockpit:
#   curl -sSL https://example/install.sh | bash    # eventual story
#   # or:
#   sudo bash scripts/jetson/install.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script needs sudo. Re-run as: sudo bash $0" >&2
    exit 1
fi

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${VENV_DIR:-/opt/rcpilot/venv}"

echo "[install] repo root: ${REPO_ROOT}"
echo "[install] venv:      ${VENV_DIR}"

# ---- 1. Apt prerequisites ------------------------------------------------
echo "[install] installing system packages..."
apt-get update
apt-get install -y --no-install-recommends \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    nano \
    python3-numpy \
    python3-opencv \
    python3-venv \
    python3-pip

# ---- 2. Python venv ------------------------------------------------------
mkdir -p "$(dirname "${VENV_DIR}")"
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "[install] creating venv..."
    python3 -m venv "${VENV_DIR}"
fi

echo "[install] installing rcpilot in editable mode..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -e "${REPO_ROOT}"

# ---- 3. Smoke check ------------------------------------------------------
echo "[install] sanity check - required GStreamer elements:"
for element in \
    nvarguscamerasrc \
    nvvidconv \
    nvcompositor \
    videoconvert \
    appsrc \
    appsink \
    x264enc \
    h264parse \
    rtph264pay \
    udpsink; do
    if gst-inspect-1.0 "${element}" > /dev/null 2>&1; then
        echo "    [ok]   ${element}"
    else
        echo "    [MISS] ${element}" >&2
    fi
done

echo "[install] OpenCV / Jetson acceleration check:"
/usr/bin/python3 - <<'PY'
import cv2
import numpy as np

cuda_ok = False
reason = "cv2.cuda.remap unavailable"
if hasattr(cv2, "cuda") and hasattr(cv2, "cuda_GpuMat") and hasattr(cv2.cuda, "remap"):
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() <= 0:
            reason = "no CUDA device visible to OpenCV"
        else:
            src = np.zeros((2, 2, 3), dtype=np.uint8)
            map_x = np.array([[0, 1], [0, 1]], dtype=np.float32)
            map_y = np.array([[0, 0], [1, 1]], dtype=np.float32)
            gpu_src = cv2.cuda_GpuMat()
            gpu_x = cv2.cuda_GpuMat()
            gpu_y = cv2.cuda_GpuMat()
            gpu_src.upload(src)
            gpu_x.upload(map_x)
            gpu_y.upload(map_y)
            out = cv2.cuda.remap(
                gpu_src, gpu_x, gpu_y, cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT, borderValue=0,
            )
            out.download()
            cuda_ok = True
    except Exception as exc:
        reason = str(exc)

if cuda_ok:
    print("    [ok]   OpenCV CUDA remap available for RCPILOT_STITCH_ACCEL=auto")
else:
    print(f"    [info] OpenCV CUDA remap not available ({reason}); CPU fast path will be used")

vpi_ok = False
vpi_reason = "vpi import failed"
try:
    import vpi
    # VPI WarpGrid requires region width >= 64; use 128x64 for the smoke
    # test (production warp is 2560x720, well above the minimum).
    sw, sh = 128, 64
    src = np.zeros((sh, sw, 3), dtype=np.uint8)
    grid = vpi.WarpGrid((sw, sh))
    warp = vpi.WarpMap(grid)
    arr = np.asarray(warp)
    arr[..., 0] = np.tile(np.linspace(0, sw - 1, sw, dtype=np.float32), (sh, 1))
    arr[..., 1] = np.tile(np.linspace(0, sh - 1, sh, dtype=np.float32).reshape(-1, 1), (1, sw))
    src_img = vpi.asimage(src)
    out_img = vpi.Image((sw, sh), src_img.format)
    with vpi.Backend.CUDA:
        src_img.remap(warp, interp=vpi.Interp.LINEAR,
                      border=vpi.Border.ZERO, out=out_img)
    with out_img.lock_cpu() as _:
        pass
    vpi_ok = True
except Exception as exc:
    vpi_reason = f"{type(exc).__name__}: {exc}"

if vpi_ok:
    print("    [ok]   VPI CUDA remap available — RCPILOT_STITCH_ACCEL=auto will pick VPI")
else:
    print(f"    [info] VPI CUDA remap not available ({vpi_reason})")
PY

echo
echo "[install] done."
echo "  Activate with:    source ${VENV_DIR}/bin/activate"
echo "  Run echo server:  rcpilot-echo-server -v"
echo "  Run video sender: RCPILOT_COCKPIT_IP=<cockpit-ip> bash ${REPO_ROOT}/scripts/jetson/start_video_stitched.sh"
echo "  Run stitcher:     RCPILOT_VIDEO_MODE=stitch RCPILOT_COCKPIT_IP=<cockpit-ip> bash ${REPO_ROOT}/scripts/jetson/start_video_stitched.sh"
