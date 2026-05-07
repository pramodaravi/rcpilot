#!/usr/bin/env bash
# Convenience launcher for the Mac driver station.
#
# What this does:
#   1. starts the stitched video bridge in the background
#   2. tells you to press Play in Unity, or launch a built .app
#
# Prereqs, one time:
#   brew install python@3.12 ffmpeg
#   python3 -m pip install -r video-bridge/requirements.txt
#   install Unity Hub + Unity 2022.3 LTS
#
# The Jetson must already be streaming the stitched RTP/H.264 feed to this
# Mac's UDP 5004. If it is not yet streaming, the bridge retries quietly.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python3}"
LOG_DIR="$(pwd)/.bridge-logs"
mkdir -p "$LOG_DIR"

echo "[start-with-unity] launching merged video bridge (logs in $LOG_DIR)"
"$PY" video-bridge/bridge.py \
    --in-port 5004 --out-port 9000 \
    --preset fast --frame-format raw \
    > "$LOG_DIR/stitched.log" 2>&1 &
BRIDGE_PID=$!

echo "[start-with-unity] stitched bridge pid=$BRIDGE_PID"
echo ""
echo "Bridges are running. Next:"
echo "  - If using the Unity Editor, open this folder as a Unity project and press Play."
echo "  - If using a built .app, launch it from Finder or run: open <path>.app"
echo ""
echo "Watch bridge output:  tail -f $LOG_DIR/stitched.log"
echo "Stop bridge:          kill $BRIDGE_PID  (or close this terminal)"
echo ""
echo "Press Ctrl-C to stop the bridge and exit."

cleanup() { kill "$BRIDGE_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
wait
