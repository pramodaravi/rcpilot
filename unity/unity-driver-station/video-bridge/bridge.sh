#!/usr/bin/env bash
# Stitched-camera bridge. Launches one bridge.py: RTP UDP 5004 -> raw TCP 9000.
# Matches the Jetson side start_video_stitched.sh defaults and the Unity
# Assets/StreamingAssets/config.json defaults.
#
# Run from the unity-driver-station directory, or anywhere:
#
#   video-bridge/bridge.sh
#
# The two physical cameras are already merged on the Jetson, so the cockpit
# needs only one local bridge process.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

echo "[bridge.sh] starting merged feed (RTP :5004 -> raw TCP :9000)"
exec "$PY" "$SCRIPT_DIR/bridge.py" \
    --in-port 5004 --out-port 9000 \
    --preset fast --frame-format raw
