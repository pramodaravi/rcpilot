#!/usr/bin/env bash
#
# rcpilot — Jetson-side dual-camera stitched video sender.
#
# Captures BOTH IMX219 cameras simultaneously, composites them side-by-side in
# NVMM via nvcompositor (GPU/Tegra hardware-accelerated, zero-copy), encodes
# the merged 2560x720 frame as one H.264 stream, and sends RTP to the cockpit
# on the same UDP port the single-camera pipeline used to use (5004 by default).
#
# Why stitch on the Jetson:
#   - One RTP stream on the wire instead of two (less network jitter)
#   - The cockpit just sees one wide video and renders it on one Quad — no
#     custom shader, no UV math, no duplicated middle from parallel-mounted
#     cameras
#   - nvcompositor is Tegra-hardware-accelerated, so the per-frame composite
#     cost is essentially free on the GPU
#
# Trade-off: total pixel count doubles (1280x720 -> 2560x720), so the encoder
# has more work to do. We compensate by dropping framerate to 30 fps and
# bumping the bitrate budget. After the Orin NX swap (NVENC), this'll all
# disappear into hardware.
#
# Env vars (with defaults):
#   RCPILOT_COCKPIT_IP   (REQUIRED)  Where to send RTP.
#   RCPILOT_VIDEO_PORT   5004        UDP port on the cockpit.
#   RCPILOT_VIDEO_WIDTH  1280        Per-camera capture width.
#   RCPILOT_VIDEO_HEIGHT 720         Per-camera capture height.
#   RCPILOT_VIDEO_FPS    30          Output framerate (lower than single-cam
#                                    because we encode 2x the pixels).
#   RCPILOT_BITRATE_KBPS 16000       Output bitrate. ~33% over single-cam to
#                                    compensate for the larger frame area.
#   RCPILOT_SENSOR_MODE  4           IMX219 capture mode (4 = 1280x720@60).

set -euo pipefail

if [[ -z "${RCPILOT_COCKPIT_IP:-}" ]]; then
    echo "ERROR: RCPILOT_COCKPIT_IP not set." >&2
    exit 64
fi

PORT="${RCPILOT_VIDEO_PORT:-5004}"
WIDTH="${RCPILOT_VIDEO_WIDTH:-1280}"
HEIGHT="${RCPILOT_VIDEO_HEIGHT:-720}"
FPS="${RCPILOT_VIDEO_FPS:-30}"
BITRATE_KBPS="${RCPILOT_BITRATE_KBPS:-16000}"
SENSOR_MODE="${RCPILOT_SENSOR_MODE:-4}"
KEY_INTERVAL="${RCPILOT_KEY_INTERVAL:-$((FPS / 2))}"
X264_PRESET="${RCPILOT_X264_PRESET:-superfast}"

OUT_W=$(( WIDTH * 2 ))   # 2560
OUT_H="${HEIGHT}"        # 720

cat <<INFO >&2
====================================================
  rcpilot dual-camera stitched video sender
  Sources:   IMX219 sensor 0 + sensor 1 (mode ${SENSOR_MODE})
  Per-cam:   ${WIDTH}x${HEIGHT}@${FPS}
  Composite: ${OUT_W}x${OUT_H} (side-by-side, butt-jointed at x=${WIDTH})
  Encoder:   x264enc ${X264_PRESET}, ${BITRATE_KBPS} kbps
  Transport: RTP/H264 -> ${RCPILOT_COCKPIT_IP}:${PORT}
====================================================
INFO

# nvcompositor pipeline:
#   - Two nvarguscamerasrc sources, both at ${WIDTH}x${HEIGHT}@${FPS}, NVMM.
#   - nvcompositor stitches them side-by-side into a ${OUT_W}x${OUT_H} NV12
#     frame, still in NVMM (no host-memory copy).
#   - Single nvvidconv pulls the merged frame out of NVMM into I420 system
#     memory for the software encoder.
#   - x264 (zerolatency, intra-refresh) encodes the merged frame.
#   - rtph264pay -> udpsink to cockpit.
exec gst-launch-1.0 -v \
    nvcompositor name=comp \
        sink_0::xpos=0          sink_0::ypos=0 \
        sink_0::width=${WIDTH}  sink_0::height=${HEIGHT} \
        sink_1::xpos=${WIDTH}   sink_1::ypos=0 \
        sink_1::width=${WIDTH}  sink_1::height=${HEIGHT} \
    comp. ! nvvidconv ! video/x-raw,format=I420 \
    ! x264enc tune=zerolatency speed-preset=${X264_PRESET} \
        bitrate=${BITRATE_KBPS} key-int-max=${KEY_INTERVAL} bframes=0 \
        intra-refresh=true sliced-threads=true threads=4 byte-stream=true \
    ! h264parse config-interval=1 \
    ! rtph264pay pt=96 mtu=1400 config-interval=1 \
    ! udpsink host="${RCPILOT_COCKPIT_IP}" port="${PORT}" \
        sync=false async=false buffer-size=65536 \
    nvarguscamerasrc sensor-id=0 sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1,format=NV12" \
    ! comp.sink_0 \
    nvarguscamerasrc sensor-id=1 sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1,format=NV12" \
    ! comp.sink_1
