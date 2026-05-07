#!/usr/bin/env bash
#
# rcpilot - single CSI camera rescue stream.
#
# This keeps the cockpit alive when the second sensor, nvcompositor, or camera
# calibration path is failing. It still uses NVIDIA's camera stack and NVMM up
# to the final CPU H.264 encode, matching the Orin Nano constraint that there is
# no NVENC block to use.

set -euo pipefail

COCKPIT_IP="${RCPILOT_COCKPIT_IP:-192.168.1.247}"
PORT="${RCPILOT_VIDEO_PORT:-5004}"
BITRATE="${RCPILOT_SINGLE_BITRATE:-6000}"
FPS="${RCPILOT_SINGLE_FPS:-30}"
SENSOR_ID="${RCPILOT_SINGLE_SENSOR_ID:-${RCPILOT_LEFT_SENSOR_ID:-0}}"
SENSOR_MODE="${RCPILOT_SENSOR_MODE:-4}"
CAM_W="${RCPILOT_VIDEO_WIDTH:-1280}"
CAM_H="${RCPILOT_VIDEO_HEIGHT:-720}"

echo ">>> rcpilot single-camera rescue video"
echo "    cockpit: ${COCKPIT_IP}:${PORT}"
echo "    sensor : ${SENSOR_ID} mode=${SENSOR_MODE}"
echo "    output : ${CAM_W}x${CAM_H}@${FPS}fps"
echo "    bitrate: ${BITRATE} kbps (software x264 on Orin Nano)"
echo

for elem in nvarguscamerasrc nvvidconv x264enc h264parse rtph264pay udpsink queue; do
    if ! gst-inspect-1.0 "${elem}" >/dev/null 2>&1; then
        echo "ERROR: GStreamer element '${elem}' is not available." >&2
        echo "Install JetPack base plus gstreamer plugins good/bad/ugly/libav." >&2
        exit 1
    fi
done

exec gst-launch-1.0 -e \
    nvarguscamerasrc sensor-id="${SENSOR_ID}" sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${CAM_W},height=${CAM_H},framerate=${FPS}/1,format=NV12" \
    ! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
    ! nvvidconv \
    ! "video/x-raw,format=I420,width=${CAM_W},height=${CAM_H},framerate=${FPS}/1" \
    ! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
    ! x264enc tune=zerolatency speed-preset=ultrafast \
              bitrate="${BITRATE}" key-int-max="${FPS}" \
              bframes=0 intra-refresh=true sliced-threads=true \
              threads=4 byte-stream=true \
    ! h264parse config-interval=1 \
    ! rtph264pay pt=96 mtu=1400 config-interval=1 \
    ! udpsink host="${COCKPIT_IP}" port="${PORT}" sync=false async=false buffer-size=65536
