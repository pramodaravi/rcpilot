#!/usr/bin/env bash
#
# rcpilot - Jetson-side video sender.
#
# The service must always prefer a live camera image over a clever-but-dead
# stitch. By default this wrapper runs the native NVIDIA GStreamer side-by-side
# sender, which keeps both CSI cameras in NVMM through nvcompositor and only
# drops to CPU for x264 because Orin Nano has no NVENC.
#
# Set RCPILOT_VIDEO_MODE=stitch to launch the Python vision stitcher, which:
#   1. captures both CSI cameras,
#   2. estimates/loads their alignment,
#   3. warps one image into the other's view,
#   4. feather-blends the overlap into one widescreen frame,
#   5. encodes and sends that single RTP/H.264 stream to the cockpit.
#
# Modes:
#   native-sbs  - default, most reliable live video path
#   stitch      - Python feature-based panorama stitcher
#   auto        - try stitch; if it exits non-zero, fall back to native-sbs

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${RCPILOT_VIDEO_MODE:-native-sbs}"

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
    native-sbs|sbs|native)
        exec /usr/bin/bash "${SCRIPT_DIR}/stream_native_sbs.sh"
        ;;
    stitch|stitched)
        exec "${PYTHON}" "${SCRIPT_DIR}/stitch_video.py"
        ;;
    auto)
        set +e
        "${PYTHON}" "${SCRIPT_DIR}/stitch_video.py"
        code=$?
        set -e
        if [[ "${code}" -eq 0 ]]; then
            exit 0
        fi
        echo "WARN: stitch_video.py exited with ${code}; falling back to native side-by-side video." >&2
        exec /usr/bin/bash "${SCRIPT_DIR}/stream_native_sbs.sh"
        ;;
    *)
        echo "ERROR: RCPILOT_VIDEO_MODE must be native-sbs, stitch, or auto; got '${MODE}'." >&2
        exit 64
        ;;
esac
