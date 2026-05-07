#!/usr/bin/env bash
#
# rcpilot - Jetson-side video sender.
#
# By default this wrapper runs the Python feature-based panorama stitcher,
# which the Jetson has been validated to drive at ~23 fps via VPI CUDA remap.
# Set RCPILOT_VIDEO_MODE=native-sbs for the rescue path: a pure GStreamer
# nvcompositor side-by-side pipeline with no warp, useful for proving the
# camera + RTP + cockpit chain is alive when the stitcher is misbehaving.
#
# Modes:
#   stitch      - default, Python feature-based panorama stitcher (VPI accel)
#   native-sbs  - rescue, native NVIDIA GStreamer side-by-side composite
#   auto        - try stitch; if it exits non-zero, fall back to native-sbs

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${RCPILOT_VIDEO_MODE:-stitch}"

if [[ "${MODE}" != "native-sbs" && "${MODE}" != "sbs" && "${MODE}" != "native" \
      && -z "${RCPILOT_COCKPIT_IP:-}" \
      && "${RCPILOT_STITCH_DIAGNOSTIC:-0}" != "1" \
      && "${RCPILOT_STITCH_PREVIEW_ONLY:-0}" != "1" ]]; then
    echo "ERROR: RCPILOT_COCKPIT_IP not set." >&2
    echo "Example: RCPILOT_COCKPIT_IP=192.168.1.247 $0" >&2
    exit 64
fi

# Use system Python by default so Jetson apt packages such as python3-opencv
# are visible even when /opt/rcpilot/venv was created without system packages.
PYTHON="${RCPILOT_STITCH_PYTHON:-/usr/bin/python3}"

case "${MODE}" in
    stitch|stitched)
        exec "${PYTHON}" "${SCRIPT_DIR}/stitch_video.py"
        ;;
    native-sbs|sbs|native)
        exec /usr/bin/bash "${SCRIPT_DIR}/stream_native_sbs.sh"
        ;;
    auto)
        set +e
        "${PYTHON}" "${SCRIPT_DIR}/stitch_video.py"
        code=$?
        set -e
        if [[ "${code}" -eq 0 ]]; then
            exit 0
        fi
        if [[ "${code}" -eq 130 || "${code}" -eq 143 ]]; then
            exit "${code}"
        fi
        echo "WARN: stitch_video.py exited with ${code}; falling back to native side-by-side video." >&2
        exec /usr/bin/bash "${SCRIPT_DIR}/stream_native_sbs.sh"
        ;;
    *)
        echo "ERROR: RCPILOT_VIDEO_MODE must be stitch, native-sbs, or auto; got '${MODE}'." >&2
        exit 64
        ;;
esac
