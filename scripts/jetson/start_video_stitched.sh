#!/usr/bin/env bash
#
# rcpilot - Jetson-side video sender.
#
# The normal service path is a single stitched panorama: two CSI cameras are
# captured through NVIDIA's camera stack, aligned once, warped/blended per
# frame, and sent as one RTP/H.264 stream to the cockpit.
#
# RCPILOT_VIDEO_MODE=stitch launches the Python vision stitcher, which:
#   1. captures both CSI cameras,
#   2. estimates/loads their alignment,
#   3. warps one image into the other's view,
#   4. feather-blends the overlap into one widescreen frame,
#   5. encodes and sends that single RTP/H.264 stream to the cockpit.
#
# Modes:
#   stitch        - default, real single panorama via VPI/CUDA remap when available
#   auto          - try stitch, then native-sbs, then native-single
#   native-sbs    - rescue/debug dual-camera side-by-side live video path
#   native-single - rescue path if the second camera/compositor is broken

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${RCPILOT_VIDEO_MODE:-stitch}"
export RCPILOT_STITCH_ACCEL="${RCPILOT_STITCH_ACCEL:-vpi}"
export RCPILOT_STITCH_FAST="${RCPILOT_STITCH_FAST:-1}"

case "${MODE}" in
    native-sbs|sbs|native|native-single|single)
        IS_NATIVE_MODE=1
        ;;
    *)
        IS_NATIVE_MODE=0
        ;;
esac

if [[ "${IS_NATIVE_MODE}" != "1" \
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

trap 'exit 0' INT TERM

run_native_sbs_with_rescue() {
    set +e
    /usr/bin/bash "${SCRIPT_DIR}/stream_native_sbs.sh"
    code=$?
    set -e

    if [[ "${code}" -eq 0 ]]; then
        exit 0
    fi
    if [[ "${code}" -eq 130 || "${code}" -eq 143 ]]; then
        exit "${code}"
    fi

    echo "WARN: native side-by-side sender exited with ${code}; falling back to single-camera rescue video." >&2
    exec /usr/bin/bash "${SCRIPT_DIR}/stream_single_camera.sh"
}

case "${MODE}" in
    native-sbs|sbs|native)
        run_native_sbs_with_rescue
        ;;
    native-single|single)
        exec /usr/bin/bash "${SCRIPT_DIR}/stream_single_camera.sh"
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
        run_native_sbs_with_rescue
        ;;
    *)
        echo "ERROR: RCPILOT_VIDEO_MODE must be native-sbs, native-single, stitch, or auto; got '${MODE}'." >&2
        exit 64
        ;;
esac
