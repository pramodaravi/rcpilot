#!/usr/bin/env bash
#
# rcpilot - native NVIDIA side-by-side live stream.
#
# This is the rescue/default video path. It does not try to be a photographic
# panorama. It proves that both CSI cameras, the Jetson camera stack, RTP, the
# cockpit bridge, and Unity are alive. The two cameras stay in NVIDIA NVMM
# memory through nvarguscamerasrc, nvvidconv, and nvcompositor. Orin Nano has
# no NVENC, so H.264 encode is intentionally software x264.

set -euo pipefail

COCKPIT_IP="${RCPILOT_COCKPIT_IP:-192.168.1.247}"
PORT="${RCPILOT_VIDEO_PORT:-5004}"
BITRATE="${RCPILOT_SBS_BITRATE:-12000}"
FPS="${RCPILOT_SBS_FPS:-30}"
OVERLAP="${RCPILOT_SBS_OVERLAP:-0}"
LEFT_SENSOR="${RCPILOT_LEFT_SENSOR_ID:-0}"
RIGHT_SENSOR="${RCPILOT_RIGHT_SENSOR_ID:-1}"
SENSOR_MODE="${RCPILOT_SENSOR_MODE:-4}"

# IMX219 sensor mode 4 is 1280x720. Keep the native path aligned with the
# cockpit bridge and Unity texture aspect.
CAM_W="${RCPILOT_VIDEO_WIDTH:-1280}"
CAM_H="${RCPILOT_VIDEO_HEIGHT:-720}"

RIGHT_XPOS=$(( CAM_W - OVERLAP ))
OUT_W=$(( CAM_W * 2 - OVERLAP ))
OUT_H="${CAM_H}"

if [[ "${LEFT_SENSOR}" == "${RIGHT_SENSOR}" ]]; then
    echo "ERROR: left and right sensor ids must differ." >&2
    exit 64
fi

echo ">>> rcpilot native side-by-side video"
echo "    cockpit: ${COCKPIT_IP}:${PORT}"
echo "    sensors: ${LEFT_SENSOR}/${RIGHT_SENSOR} mode=${SENSOR_MODE}"
echo "    cameras: ${CAM_W}x${CAM_H}@${FPS}fps x2"
echo "    output : ${OUT_W}x${OUT_H} overlap=${OVERLAP}px"
echo "    bitrate: ${BITRATE} kbps (software x264 on Orin Nano)"
echo

for elem in nvarguscamerasrc nvvidconv nvcompositor videoconvert x264enc \
            h264parse rtph264pay udpsink queue; do
    if ! gst-inspect-1.0 "${elem}" >/dev/null 2>&1; then
        echo "ERROR: GStreamer element '${elem}' is not available." >&2
        echo "Install JetPack base plus gstreamer plugins good/bad/ugly/libav." >&2
        exit 1
    fi
done

exec gst-launch-1.0 -e \
    nvcompositor name=comp \
        sink_0::xpos=0             sink_0::ypos=0 sink_0::width="${CAM_W}" sink_0::height="${CAM_H}" \
        sink_1::xpos="${RIGHT_XPOS}" sink_1::ypos=0 sink_1::width="${CAM_W}" sink_1::height="${CAM_H}" \
    ! "video/x-raw(memory:NVMM),width=${OUT_W},height=${OUT_H},framerate=${FPS}/1" \
    ! nvvidconv \
    ! "video/x-raw,format=I420,width=${OUT_W},height=${OUT_H},framerate=${FPS}/1" \
    ! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
    ! x264enc tune=zerolatency speed-preset=ultrafast \
              bitrate="${BITRATE}" key-int-max="${FPS}" \
              bframes=0 intra-refresh=true sliced-threads=true \
              threads=4 byte-stream=true \
    ! h264parse config-interval=1 \
    ! rtph264pay pt=96 mtu=1400 config-interval=1 \
    ! udpsink host="${COCKPIT_IP}" port="${PORT}" sync=false async=false buffer-size=65536 \
    nvarguscamerasrc sensor-id="${LEFT_SENSOR}" sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${CAM_W},height=${CAM_H},framerate=${FPS}/1,format=NV12" \
    ! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
    ! comp.sink_0 \
    nvarguscamerasrc sensor-id="${RIGHT_SENSOR}" sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${CAM_W},height=${CAM_H},framerate=${FPS}/1,format=NV12" \
    ! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
    ! comp.sink_1
