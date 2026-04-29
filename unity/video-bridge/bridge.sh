#!/usr/bin/env bash
# Launch two bridge.py processes — one per camera.
#
# Both run in the foreground of this shell with prefixed output, so you can
# watch them together and Ctrl-C kills both. If you'd rather see two separate
# Terminal windows, there's an AppleScript-y variant at the bottom — commented.
#
#   cd unity-driver-station
#   video-bridge/bridge.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

cleanup() {
  # Forward SIGTERM to the bridge children.
  jobs -p | xargs -I{} kill {} 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[bridge.sh] starting cam0 (RTP :5000 → JPEG TCP :9000) and cam1 (RTP :5001 → JPEG TCP :9001)"

# Prefix each child's stdout with its label so two interleaved logs stay
# readable. sed -u disables buffering on macOS BSD sed.
( "$PY" "$SCRIPT_DIR/bridge.py" --in-port 5000 --out-port 9000 2>&1 \
    | sed -u 's/^/[cam0] /' ) &
( "$PY" "$SCRIPT_DIR/bridge.py" --in-port 5001 --out-port 9001 2>&1 \
    | sed -u 's/^/[cam1] /' ) &

wait
