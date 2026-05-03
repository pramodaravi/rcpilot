#!/usr/bin/env bash
#
# rcpilot - Jetson-side real panoramic stitch sender.
#
# This is intentionally NOT a side-by-side compositor. It launches the Python
# vision stitcher, which:
#   1. captures both CSI cameras,
#   2. estimates/loads their alignment,
#   3. warps one image into the other's view,
#   4. feather-blends the overlap into one widescreen frame,
#   5. encodes and sends that single RTP/H.264 stream to the cockpit.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${RCPILOT_COCKPIT_IP:-}" \
      && "${RCPILOT_STITCH_DIAGNOSTIC:-0}" != "1" \
      && "${RCPILOT_STITCH_PREVIEW_ONLY:-0}" != "1" ]]; then
    echo "ERROR: RCPILOT_COCKPIT_IP not set." >&2
    echo "Example: RCPILOT_COCKPIT_IP=192.168.1.247 $0" >&2
    exit 64
fi

# Use system Python by default so Jetson apt packages such as python3-opencv
# are visible even when /opt/rcpilot/venv was created without system packages.
PYTHON="${RCPILOT_STITCH_PYTHON:-/usr/bin/python3}"

exec "${PYTHON}" "${SCRIPT_DIR}/stitch_video.py"
