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

cuda_ok = False
reason = "cv2.cuda.remap unavailable"
if hasattr(cv2, "cuda") and hasattr(cv2.cuda, "remap"):
    try:
        cuda_ok = cv2.cuda.getCudaEnabledDeviceCount() > 0
        if not cuda_ok:
            reason = "no CUDA device visible to OpenCV"
    except Exception as exc:
        reason = str(exc)

if cuda_ok:
    print("    [ok]   OpenCV CUDA remap available for RCPILOT_STITCH_ACCEL=auto")
else:
    print(f"    [info] OpenCV CUDA remap not available ({reason}); CPU fast path will be used")

try:
    import vpi  # noqa: F401
    print("    [ok]   NVIDIA VPI Python module present")
except Exception:
    print("    [info] NVIDIA VPI Python module not present in /usr/bin/python3")
PY

echo
echo "[install] done."
echo "  Activate with:    source ${VENV_DIR}/bin/activate"
echo "  Run echo server:  rcpilot-echo-server -v"
echo "  Run video sender: RCPILOT_COCKPIT_IP=<cockpit-ip> bash ${REPO_ROOT}/scripts/jetson/start_video.sh"
echo "  Run AI stitcher:  RCPILOT_COCKPIT_IP=<cockpit-ip> bash ${REPO_ROOT}/scripts/jetson/start_video_stitched.sh"
