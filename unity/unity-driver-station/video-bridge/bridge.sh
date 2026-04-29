#!/usr/bin/env bash
# V1: single-camera. Launches one bridge.py — RTP UDP 5004 → JPEG TCP 9000.
# Matches the Jetson side defaults in rcpilot/config/default.yaml and the
# Unity side defaults in Assets/StreamingAssets/config.json.
#
# Run from the unity-driver-station directory (or anywhere — paths are
# resolved relative to this script):
#
#   video-bridge/bridge.sh
#
# A second camera (chase view, etc.) is a future feature — to enable it,
# launch a second copy with distinct ports and set config.video.cam1Port in
# the Unity config.json. Example:
#
#   "$PY" "$SCRIPT_DIR/bridge.py" --in-port 5005 --out-port 9001 &

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

echo "[bridge.sh] starting cam0 (RTP :5004 → JPEG TCP :9000)"
exec "$PY" "$SCRIPT_DIR/bridge.py" --in-port 5004 --out-port 9000
