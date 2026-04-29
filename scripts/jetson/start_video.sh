#!/usr/bin/env bash
#
# rcpilot — Jetson-side video sender.
#
# Captures the IMX219 CSI camera, encodes H.264, and streams RTP/UDP to the
# cockpit. Reads its parameters either from environment variables (set by
# whatever launches it — systemd unit, dev shell, Make target) or falls back
# to safe defaults that match config/default.yaml.
#
# After the Orin NX module swap, set RCPILOT_ENCODER=nvv4l2h264enc to switch
# to hardware NVENC and drop ~40 ms of encode latency. Do not change anything
# else — every other element in the pipeline (capture caps, packetizer,
# transport) stays the same.
#
# Env vars (with defaults):
#   RCPILOT_COCKPIT_IP       (REQUIRED)         Where to send the video stream.
#   RCPILOT_VIDEO_PORT       5004               UDP port on the cockpit.
#   RCPILOT_VIDEO_WIDTH      1280
#   RCPILOT_VIDEO_HEIGHT     720
#   RCPILOT_VIDEO_FPS        60
#   RCPILOT_BITRATE_KBPS     8000
#   RCPILOT_SENSOR_MODE      4                  IMX219 mode (4 = 1280x720@60).
#   RCPILOT_ENCODER          x264enc            Use nvv4l2h264enc on Orin NX.
#
# IMX219 sensor modes:
#   0: 3280x2464 @ 21fps
#   1: 3280x1848 @ 28fps
#   2: 1920x1080 @ 30fps
#   3: 1640x1232 @ 30fps
#   4: 1280x720  @ 60fps   <-- recommended for software encode on Orin Nano
#   5: 1280x720  @ 120fps

set -euo pipefail

if [[ -z "${RCPILOT_COCKPIT_IP:-}" ]]; then
    echo "ERROR: RCPILOT_COCKPIT_IP not set." >&2
    echo "Set it in the environment or via the systemd unit." >&2
    echo "Example: RCPILOT_COCKPIT_IP=192.168.55.100 $0" >&2
    exit 64
fi

PORT="${RCPILOT_VIDEO_PORT:-5004}"
WIDTH="${RCPILOT_VIDEO_WIDTH:-1280}"
HEIGHT="${RCPILOT_VIDEO_HEIGHT:-720}"
FPS="${RCPILOT_VIDEO_FPS:-60}"
BITRATE_KBPS="${RCPILOT_BITRATE_KBPS:-8000}"
SENSOR_MODE="${RCPILOT_SENSOR_MODE:-4}"
ENCODER="${RCPILOT_ENCODER:-x264enc}"

cat <<INFO >&2
====================================================
  rcpilot video sender
  Source:    IMX219 CSI, sensor-mode=${SENSOR_MODE}
  Capture:   ${WIDTH}x${HEIGHT}@${FPS}
  Encoder:   ${ENCODER}
  Bitrate:   ${BITRATE_KBPS} kbps
  Transport: RTP/H264 over UDP -> ${RCPILOT_COCKPIT_IP}:${PORT}
====================================================
INFO

# ---- Build the encoder element string ------------------------------------
#
# x264enc and nvv4l2h264enc take different parameter names for the same
# concepts. Keep the rest of the pipeline identical so the only thing that
# changes when switching encoders is this block.
case "${ENCODER}" in
    x264enc)
        ENCODE_ELEMENT="x264enc tune=zerolatency speed-preset=ultrafast \
            bitrate=${BITRATE_KBPS} key-int-max=${FPS} bframes=0 \
            sliced-threads=true threads=4 byte-stream=true"
        # Software encode runs on system memory, so we have to come out of NVMM.
        CONVERT_ELEMENT="nvvidconv ! video/x-raw,format=I420"
        ;;
    nvv4l2h264enc)
        # NVENC takes bitrate in bits/sec, not kbps.
        BITRATE_BPS=$(( BITRATE_KBPS * 1000 ))
        ENCODE_ELEMENT="nvv4l2h264enc maxperf-enable=1 bitrate=${BITRATE_BPS} \
            iframeinterval=${FPS} insert-sps-pps=1 control-rate=1"
        # NVENC reads from NVMM, no system-memory copy needed.
        CONVERT_ELEMENT="identity"
        ;;
    *)
        echo "ERROR: unknown encoder '${ENCODER}'. Use x264enc or nvv4l2h264enc." >&2
        exit 64
        ;;
esac

exec gst-launch-1.0 -v \
    nvarguscamerasrc sensor-id=0 sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" \
        aelock=false \
    ! "video/x-raw(memory:NVMM),width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1,format=NV12" \
    ! ${CONVERT_ELEMENT} \
    ! ${ENCODE_ELEMENT} \
    ! h264parse config-interval=1 \
    ! rtph264pay pt=96 mtu=1400 config-interval=1 \
    ! udpsink host="${RCPILOT_COCKPIT_IP}" port="${PORT}" \
        sync=false async=false buffer-size=65536
