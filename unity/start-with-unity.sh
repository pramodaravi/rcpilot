#!/usr/bin/env bash
# Convenience launcher for the Mac driver station.
#
# What this does:
#   1. starts the two video bridges (cam0/cam1) in the background
#   2. tells you to press Play in Unity (or double-click a built .app)
#
# Prereqs (one-time, see docs/unity-setup.md for the full walkthrough):
#   brew install python@3.12 ffmpeg
#   python3 -m pip install -r video-bridge/requirements.txt
#   install Unity Hub + Unity 2022.3 LTS (macOS Build Support — Apple Silicon)
#
# The Jetson must already be streaming RTP H.264 to this Mac's 5000 / 5001
# before the bridges can deliver frames. If it isn't yet, the bridges will
# retry quietly every 2 s — no harm done.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python3}"
LOG_DIR="$(pwd)/.bridge-logs"
mkdir -p "$LOG_DIR"

echo "[start-with-unity] launching video bridges (logs in $LOG_DIR)"
"$PY" video-bridge/bridge.py --in-port 5000 --out-port 9000 \
    > "$LOG_DIR/cam0.log" 2>&1 &
CAM0_PID=$!
"$PY" video-bridge/bridge.py --in-port 5001 --out-port 9001 \
    > "$LOG_DIR/cam1.log" 2>&1 &
CAM1_PID=$!

echo "[start-with-unity] cam0 pid=$CAM0_PID  cam1 pid=$CAM1_PID"
echo ""
echo "Bridges are running. Next:"
echo "  - If using the Unity Editor, open this folder as a Unity project and press Play."
echo "  - If using a built .app, launch it from Finder or  open <path>.app"
echo ""
echo "Watch bridge output:  tail -f $LOG_DIR/cam0.log  (or cam1.log)"
echo "Stop bridges:         kill $CAM0_PID $CAM1_PID  (or close this terminal)"
echo ""
echo "Press Ctrl-C to stop both bridges and exit."

# Keep this shell alive so Ctrl-C lands here and cleans up the children.
cleanup() { kill "$CAM0_PID" "$CAM1_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
wait
