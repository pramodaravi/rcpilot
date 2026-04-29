#!/usr/bin/env bash
#
# rcpilot — cockpit video receiver (Linux / macOS bash).
#
# Same pipeline as view_video.ps1 but for non-Windows cockpits.
#
# Usage:
#   ./view_video.sh             # uses default port 5004
#   PORT=5004 ./view_video.sh

set -euo pipefail

PORT="${PORT:-5004}"

cat <<INFO
====================================================
  rcpilot video receiver
  Listening:  0.0.0.0:${PORT}
  Decoder:    avdec_h264 (software)
  Latency:    no jitter buffer
  Press q in video window or Ctrl-C here to stop.
====================================================
INFO

exec gst-launch-1.0 -v \
    udpsrc port="${PORT}" caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" \
    ! rtph264depay \
    ! h264parse \
    ! avdec_h264 \
    ! videoconvert \
    ! autovideosink sync=false
