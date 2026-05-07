#!/usr/bin/env bash
#
# rcpilot - cylindrical-projection panorama sender (Phase 1 of clean-slate plan).
#
# Reads per-camera intrinsics from /home/adm2n/rcpilot/config/intrinsics_cam{0,1}.json
# (produced by calibrate_intrinsics.py), builds two cylindrical VPI WarpMaps,
# remaps each camera into a single panoramic canvas, alpha-blends, and ships
# RTP/H.264 to the cockpit. No homography fitting on a 5cm bench rig that
# can't see real parallax — this works as soon as cameras are aimed at a
# textured forward scene with reasonable toe-out.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${RCPILOT_COCKPIT_IP:-}" ]]; then
    echo "ERROR: RCPILOT_COCKPIT_IP not set." >&2
    echo "Example: RCPILOT_COCKPIT_IP=192.168.1.247 $0" >&2
    exit 64
fi

PYTHON="${RCPILOT_STITCH_PYTHON:-/usr/bin/python3}"
exec "${PYTHON}" "${SCRIPT_DIR}/cylindrical_stitcher.py" "$@"
