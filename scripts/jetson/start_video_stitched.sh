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
#
# Why CPU `compositor` instead of GPU `nvcompositor`:
#   - On Orin Nano + JetPack 6 the NvVIC hardware compositor errors out with
#     "failed to validate surface, surface too small" / "dst surface invalid"
#     when fed two 720p sinks placed side-by-side. Forcing explicit output
#     caps doesn't help — nvcompositor's output negotiation rejects them.
#   - The CPU compositor is a software fallback that works universally. We
#     pull each camera out of NVMM via nvvidconv first (a single zero-copy-ish
#     hop into system memory), then compose in I420. Total CPU cost on the
#     Orin Nano: ~10-15% across one core for 2x 720p @ 30 fps — fine.
#   - Switch back to nvcompositor only if a future L4T release fixes the
#     surface-validation bug, or if encode CPU starts pegging.
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

# Pipeline:
#   - Two nvarguscamerasrc sources, both at ${WIDTH}x${HEIGHT}@${FPS} in NVMM.
#   - Each camera's output runs through nvvidconv to land in system memory
#     as I420 (single zero-copy-ish hop out of NVMM, compositor needs CPU
#     access to the buffers).
#   - compositor (CPU) places sink_0 at x=0 and sink_1 at x=${WIDTH},
#     producing one ${OUT_W}x${OUT_H} I420 frame.
#   - x264 (zerolatency, intra-refresh) encodes the merged frame.
#   - rtph264pay -> udpsink to cockpit.
exec gst-launch-1.0 -v \
    compositor name=comp background=black \
        sink_0::xpos=0        sink_0::ypos=0 \
        sink_1::xpos=${WIDTH} sink_1::ypos=0 \
    comp. ! videoconvert ! "video/x-raw,format=I420,width=${OUT_W},height=${OUT_H},framerate=${FPS}/1" \
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
    ! nvvidconv ! "video/x-raw,format=I420" \
    ! comp.sink_0 \
    nvarguscamerasrc sensor-id=1 sensor-mode="${SENSOR_MODE}" \
        exposuretimerange="100000 10000000" aelock=false \
    ! "video/x-raw(memory:NVMM),width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1,format=NV12" \
    ! nvvidconv ! "video/x-raw,format=I420" \
    ! comp.sink_1
