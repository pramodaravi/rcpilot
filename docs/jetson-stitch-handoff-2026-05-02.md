# Jetson Stitch Handoff - 2026-05-02

## Goal

Produce one usable widescreen cockpit image from two IMX219 CSI cameras on a Jetson Orin Nano, streamed to the Unity cockpit as a single RTP/H.264 feed.

The user explicitly does not want two side-by-side feeds. They want one wide image. The latest user feedback after several stitcher changes was: "Still looks like ass, let's try harder." So assume the current code is a working attempt, not a solved visual result.

## Current Repo State

- Branch: `main`
- Remote: `origin https://github.com/pramodaravi/rcpilot.git`
- Latest video code commit before this handoff: `f77a08b video: improve stitch geometry quality`
- `origin/main` matched local `HEAD` before this handoff file was added.
- The broader working tree has many unrelated dirty files under docs, Unity, CAD, and helper scripts. Do not revert those unless the user explicitly asks.
- The Jetson stitcher files were clean before this handoff:
  - `scripts/jetson/stitch_video.py`
  - `scripts/jetson/start_video_stitched.sh`
  - `scripts/jetson/install.sh`
  - `scripts/jetson/rcpilot-video.service`

## Important Commit Timeline

- `718fbda video: add real dual-camera panorama stitcher`
  - Added `scripts/jetson/stitch_video.py`, a Python OpenCV/GStreamer stitcher.
- `9204014 video: clean up panoramic stitch output`
  - Added crop, overlap exposure matching, and better logs.
- `4324942 video: stitch fast path (pre-baked remap + uint8 blend)`
  - Validated performance rewrite using pre-baked remap tables and uint8 blending.
- `b757483 video: seamless stitch via dynamic seam-cut + fast remap path`
  - Dynamic seam experiment. Broke real video. Do not revive blindly.
- `0ba2b24 Revert "video: seamless stitch via dynamic seam-cut + fast remap path"`
  - Reverted seam-cut, kept fast remap path.
- `a46f96f video: make stitch calibration more robust`
  - Fresh frame sampling, lower match threshold, better feature detection.
- `7627169 video: back off when Argus camera sessions fail`
  - Avoid hammering Argus when capture sessions fail.
- `a4c1776 video: avoid respawning service during manual camera tests`
  - Changed `rcpilot-video.service` to `Restart=on-failure`.
- `0ea56ba video: use Jetson CUDA remap when available`
  - Added `RCPILOT_STITCH_ACCEL=auto|cpu|opencv-cuda`.
- `e579010 video: validate CUDA remap before selecting it`
  - Added a real tiny CUDA remap smoke test before choosing CUDA.
- `f77a08b video: improve stitch geometry quality`
  - Current visual-quality attempt: default affine model, reprojection error gate, aspect-preserving crop, narrower feather.

## Runtime Architecture

Manual stitched sender:

```bash
RCPILOT_COCKPIT_IP=192.168.1.247 \
RCPILOT_STITCH_RECALIBRATE=1 \
bash scripts/jetson/start_video_stitched.sh
```

The wrapper uses `/usr/bin/python3` by default so Jetson apt packages such as `python3-opencv` are visible.

Pipeline shape:

1. `nvarguscamerasrc` opens IMX219 sensor 0 and sensor 1.
2. OpenCV calibrates alignment from frame pairs.
3. Stitch plan builds crop, weights, and remap tables.
4. Fast path remaps both camera frames into the final output frame.
5. `x264enc` software encodes H.264 to RTP/UDP for Unity.

Important hardware note: NVIDIA documents that Jetson Orin Nano does not have NVENC. Do not spend time trying to force `nvv4l2h264enc` on this module. The useful acceleration path is camera ISP/VIC/GPU image processing where available; H.264 encode remains software x264.

## Service Trap

Before manual camera testing, stop the video service. It otherwise owns or races for the CSI cameras.

```bash
sudo systemctl stop rcpilot-video
sudo pkill -f stitch_video.py || true
sudo systemctl restart nvargus-daemon
sleep 3
```

The source service file now has `Restart=on-failure`, but the installed unit on the Jetson may be stale unless copied/reinstalled:

```bash
sudo install -m 0644 scripts/jetson/rcpilot-video.service /etc/systemd/system/rcpilot-video.service
sudo systemctl daemon-reload
```

If Argus repeatedly logs `Failed to create CaptureSession`, stop all camera owners and restart `nvargus-daemon`. If that still fails, reboot the Jetson once.

## Current Stitcher Defaults

In `scripts/jetson/stitch_video.py`:

- `RCPILOT_STITCH_MODEL=affine` by default.
  - This was changed from full homography because sparse/bad matches can over-warp the image and make it look worse.
  - `homography` is still available for comparison.
- `RCPILOT_STITCH_MAX_REPROJ_ERROR_PX=12.0`
  - Rejects bad calibration solves.
- `RCPILOT_STITCH_FEATHER_PX=8`
  - Narrower feather to reduce ghosting/double images in the overlap.
- `RCPILOT_STITCH_KEEP_ASPECT=1`
  - Crops to the output aspect ratio before resize to avoid squeezing.
- `RCPILOT_STITCH_ACCEL=auto`
  - Uses OpenCV CUDA remap only after a real smoke test succeeds, otherwise CPU fast path.
- `RCPILOT_STITCH_FAST=1`
  - Uses the pre-baked remap + uint8 blend path.

## Best Current Test Command

```bash
cd ~/rcpilot
git pull

sudo systemctl stop rcpilot-video
sudo pkill -f stitch_video.py || true
sudo systemctl restart nvargus-daemon
sleep 3

rm -f config/stitch_calibration.json

RCPILOT_COCKPIT_IP=192.168.1.247 \
RCPILOT_STITCH_RECALIBRATE=1 \
RCPILOT_STITCH_ACCEL=auto \
RCPILOT_STITCH_MODEL=affine \
RCPILOT_STITCH_FEATHER_PX=8 \
bash scripts/jetson/start_video_stitched.sh
```

Watch for:

- `stitch alignment: detector=... model=affine ... error=...px`
- `stitch canvas=... crop_aspect=... target_aspect=...`
- `fast-path baked: ... accel=opencv-cuda` or `accel=cpu`
- `streaming stitched panorama: ... fps ...`

Low reprojection error is good. If the error is high or the preview looks bad, do not trust the cached calibration.

## Debug Artifacts on Jetson

The stitcher writes debug images under:

```text
/tmp/rcpilot-stitch-debug/
```

Important files:

- `stitched_preview.jpg`
- `left_calibration_failed.jpg`
- `right_calibration_failed.jpg`

If the user says it still looks bad, the next agent should get these images first. Without them, it is guesswork.

Example copy from the Windows/cockpit side:

```powershell
scp adm2n@adm1n-desktop:/tmp/rcpilot-stitch-debug/stitched_preview.jpg C:\Users\Promo\dev\rcpilot\stitched_preview.jpg
scp adm2n@adm1n-desktop:/tmp/rcpilot-stitch-debug/left_calibration_failed.jpg C:\Users\Promo\dev\rcpilot\left_calibration_failed.jpg
scp adm2n@adm1n-desktop:/tmp/rcpilot-stitch-debug/right_calibration_failed.jpg C:\Users\Promo\dev\rcpilot\right_calibration_failed.jpg
```

## Likely Root Cause If It Still Looks Bad

This is probably not "not enough TOPS." The 40/67 TOPS number is neural inference capacity. A two-camera panorama with close objects is mostly a geometry/parallax problem:

- A single affine or homography can only perfectly align one dominant depth plane.
- Close cockpit/hand/body objects will not line up with far background.
- Feather blending creates ghosting when both cameras see the same foreground object at different positions.
- A dynamic seam-cut experiment was attempted and reverted because it broke real video.

If the cameras are angled too far apart or have too much foreground overlap, software cannot make every depth look perfect without depth estimation or a much more complex stereo pipeline.

## Recommended Next Paths

1. Inspect actual debug images first.
   - Compare `stitched_preview.jpg` with left/right source frames.
   - Decide whether the ugliness is calibration, aspect/stretch, seam ghosting, exposure/color, or camera placement.

2. Try geometry toggles before writing more code:

```bash
RCPILOT_STITCH_MODEL=homography
RCPILOT_STITCH_FEATHER_PX=0
RCPILOT_STITCH_FEATHER_PX=4
RCPILOT_STITCH_KEEP_ASPECT=0
RCPILOT_STITCH_EXPOSURE_MATCH=0
```

Use one toggle at a time and save the preview image for each.

3. If the goal is cockpit driving rather than photographic panorama, consider a hard seam with minimal overlap.
   - This often looks better than a mathematically "blended" stitch when foreground parallax is large.
   - Keep the seam in a low-attention area of the view.

4. If true panorama quality is required, do camera calibration properly:
   - Capture chessboard/Charuco frames from both cameras.
   - Estimate intrinsics and distortion per camera.
   - Rectify/undistort before stitching.
   - Then use affine/homography only after lens correction.

5. If foreground parallax must be solved, revisit seam-cut or depth-aware compositing as a separate branch.
   - Do not revive `b757483` directly.
   - The previous seam-cut likely failed due to real overlap geometry and weight/band interactions.
   - Build it behind an explicit opt-in mode and test from saved Jetson frames before enabling live.

## Do Not Do

- Do not return to the old side-by-side compositor as the default.
- Do not force `nvv4l2h264enc` on Orin Nano.
- Do not run `commit_stitch_cleanup.bat`; it was previously stale with the broken seam-cut commit message.
- Do not revert unrelated dirty Unity/CAD/docs files.

## Useful Validation Commands

Local Windows repo validation:

```powershell
python -m py_compile scripts/jetson/stitch_video.py
bash -n scripts/jetson/start_video_stitched.sh
bash -n scripts/jetson/install.sh
git diff --exit-code HEAD -- scripts/jetson/stitch_video.py scripts/jetson/install.sh scripts/jetson/rcpilot-video.service
```

Jetson install/acceleration check:

```bash
cd ~/rcpilot
git pull
sudo bash scripts/jetson/install.sh
```

Look for either:

```text
[ok]   OpenCV CUDA remap available for RCPILOT_STITCH_ACCEL=auto
```

or:

```text
[info] OpenCV CUDA remap not available (...); CPU fast path will be used
```

