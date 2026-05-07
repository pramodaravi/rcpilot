# rcpilot - architecture

This document is a fast-reference companion to the comprehensive technical
research package in `../technical-research-2026-04-28/`. Read those for the
full reasoning behind every choice. This file is the skim version.

## System diagram

```
+---------------------------+              +---------------------------+
|  SIM COCKPIT              |              |  RC CAR (Jetson)          |
|                           |              |                           |
|  Asetek wheelbase + ----+ |   UDP 5005   | +-- rcpilot-echo-server   |
|  pedals (HID)           |-|------------->| |   (control packets in,  |
|                         | |   ~250 Hz    | |    echoes out)          |
|  rcpilot-control-sender | |              | |                         |
|  (pygame -> UDP)        | |              | +-- (future) PWM driver   |
|                         | |              |                           |
|  Unity video bridge -<--+-|<-------------|+-- start_video_stitched.sh|
|  (PyAV decode ->        | |   RTP/UDP    | |   (2x nvarguscamerasrc ->|
|   one wide texture)     | |   5004       | |    align/warp/blend/enc ->|
|                         | |              | |    rtph264pay -> udpsink)|
|                         | |              | |                         |
|  (future) SimHub for    | |              | |  2x IMX219 CSI cameras  |
|  FFB synthesis          | |              | |  (rolling shutter today,|
|                         | |              | |   IMX296 global shutter |
|                         | |              | |   for the actual car)   |
+---------------------------+              +---------------------------+
```

## What Runs Where, Today

| Process | Host | Owner | Notes |
| --- | --- | --- | --- |
| `rcpilot-control-sender` | Cockpit | This repo | Reads HID joystick at 250 Hz, sends 32-byte UDP packets, measures RTT |
| `start_video_stitched.sh` | Jetson (car) | This repo | Defaults to one Python panorama stitcher stream using NVIDIA camera capture plus VPI CUDA remap for warp/blend and a live background matcher for camera-motion compensation; `native-sbs` remains a rescue/debug mode |
| `rcpilot-echo-server` | Jetson (car) | This repo | Validates CRC, echoes for RTT, tracks last good packet |
| Unity video bridge | Cockpit | This repo (script) | `bridge.py` decodes one already-merged RTP stream and forwards one wide texture to Unity |
| pygame-ce | Cockpit | Third-party (vendored as wheel) | HID joystick abstraction |

## What's Coming

| Process | Host | When | Notes |
| --- | --- | --- | --- |
| ESP32-S3 failsafe | Onboard the car | Phase 1, before any motor moves | Independent watchdog. UART to Jetson; cuts motor power on heartbeat loss |
| PWM driver | Jetson | After failsafe | PCA9685 over I2C, already prototyped in `../rc-pilot-code/jetson/pwm_driver.py` |
| Telemetry sender | Jetson | After sensors land | IMU + ESC + load cell -> ROS 2 / UDP back to cockpit |
| FFB synthesis | Cockpit (SimHub) | After telemetry exists | Maps real-car telemetry -> DirectInput effects -> Asetek wheel torque |
| Race scoring | Cockpit / ops | Phase 2 | Lap timing via MyLaps RC4 transponder; UWB for ghost cars |

## Networking

The default `192.168.55.x` subnet is what JetPack auto-assigns over USB-C
virtual ethernet. Bench bring-up uses that link directly, no Wi-Fi needed for
software development. Production transport is Wi-Fi 6E plus ELRS 915 MHz
failsafe control; see `../technical-research-2026-04-28/2_wireless_network.md`
for the full design.

Per the same brief, the latency budgets are:

- Visual loop (camera -> driver eyes): 29-46 ms typical with NVENC, ~80-150 ms today on software encode
- Control loop (wheel -> real car steering): 6.5-17.5 ms target
- FFB loop (real car -> wheel torque): 6-16 ms target

## Why These Specific Choices

Cross-referenced into the research briefs for justification. Each link points
to the full reasoning.

| Choice | Justification |
| --- | --- |
| H.264 baseline + ULL | `1_video_pipeline.md` section "Encoder choice and settings" |
| RTP over raw UDP (no jitter buffer) | `1_video_pipeline.md` section "Transport protocol comparison" |
| 250 Hz control rate | `3_simulator_software.md` section "Control protocol design" |
| CRC32 on control packets | `3_simulator_software.md` section "Telemetry plumbing detail" |
| ELRS as failsafe (separate band) | `2_wireless_network.md` section "Hybrid architectures" |
| Asetek Forte wheelbase | User has it on hand; brief `4_force_feedback.md` covers the equivalence vs. Simucube |
| IMX296 global shutter for the car | `1_video_pipeline.md` section "CSI camera selection": rolling-shutter jelly at 25 mph is fatal |
