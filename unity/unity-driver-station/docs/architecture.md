# Architecture

## Data flow end to end

```
                Jetson Orin Nano Super
  ┌────────────────────────────────────────────┐
  │ 2x IMX219 CSI                              │
  │    ↓                                       │
  │ nvarguscamerasrc                           │
  │    ↓                                       │
  │ nvv4l2h264enc (NVENC)                      │
  │    ↓                                       │
  │ rtph264pay → udpsink ─────────┐  ─────────┐│
  │                               │           ││
  │ car_service.py                │           ││
  │  ├─ control_receiver.py ◄─┐   │           ││
  │  ├─ pwm_driver.py            │           ││
  │  ├─ failsafe.py  (150 ms)    │           ││
  │  └─ telemetry_sender.py ─┐   │           ││
  │                          │   │           ││
  └──────────────────────────┼───┼───────────┼┘
                             │   │           │
                        UDP  │   │ RTP :5000 │ RTP :5001
                       :7411 │   │           │
                             │   ▼           ▼
                             │  ┌───────────────────────┐
                             │  │ video-bridge/         │
                             │  │   (python + PyAV /    │
                             │  │    FFmpeg + Pillow)   │
                             │  │                       │
                             │  │  rtp SDP → FFmpeg     │
                             │  │    demux + decode     │
                             │  │  → swscale rescale    │
                             │  │  → PIL JPEG encode    │
                             │  │  → TCP :9000, :9001   │
                             │  └───────┬───────────────┘
                             │          │ JPEG frames
                             │          ▼
                        UDP  │   ┌───────────────────────┐
                       :7412 └───┤ Unity driver station  │
                          ──────►│                       │
                                 │ Core/Bootstrapper     │
                                 │  ├─ VideoBridgeClient │
                                 │  │    × 2            │
                                 │  ├─ TelemetryReceiver │
                                 │  ├─ ControlSender     │
                                 │  ├─ WheelInput        │
                                 │  │    (gamepad)       │
                                 │  ├─ HUDController     │
                                 │  ├─ CockpitBuilder    │
                                 │  ├─ RaceManager       │
                                 │  ├─ EngineSynth       │
                                 │  └─ UIAudio / Menu    │
                                 └───────────────────────┘
```

## Latency budget (60 fps path)

| Stage | Best case | Typical |
|---|---:|---:|
| CSI ISP + capture | 15 ms | 18 ms |
| NVENC H.264 encode | 2 ms | 4 ms |
| Payload + UDP + WiFi | 3 ms | 8 ms |
| Jitter buffer (20 ms) | 20 ms | 20 ms |
| FFmpeg H.264 decode (threaded) | 3 ms | 6 ms |
| swscale rescale + JPEG re-encode | 4 ms | 7 ms |
| TCP + Unity read | 3 ms | 6 ms |
| Unity upload + present | 8 ms | 16 ms |
| **Total glass-to-glass** | **58 ms** | **85 ms** |

At an indoor kart circuit (drivers maybe 10-15 mph tops, closed loop,
pit-lane override) this is fine. At 15 mph a 85 ms delay equals about
0.55 m of travel, which is smaller than the width of a rice-paper-thin
1/8 buggy. For comparison, FPV quadcopters race successfully at
30–50 ms total glass-to-glass with analog video.

Where does the latency go when we want to trim it back?
1. **VideoToolbox hardware decode.** On Apple Silicon, PyAV can route
   decode through `h264_videotoolbox` — 2-3 ms back and frees the CPU.
2. **Drop the JPEG re-encode** by moving to Unity WebRTC (which is the
   production plan in `wireless-link-recommendation.md`). Saves 8-10 ms
   total by skipping re-encode + TCP + Unity upload.
3. **Raise the Jetson encode bitrate** from 4 Mbps to 6 Mbps — reduces
   inter-frame size variance and shrinks the jitterbuffer budget.

## Threading model

| Component | Thread |
|---|---|
| `Bootstrapper` | Main (Unity) |
| `ControlSender` | Main, paced in `Update()` at sendHz |
| `TelemetryReceiver` | Background thread with blocking UDP recv + 200 ms timeout |
| `VideoBridgeClient` | Background thread per camera (TCP recv + blocking reads) |
| `CockpitBuilder` / `HUDController` | Main |
| `EngineSynth.OnAudioFilterRead` | Unity audio thread, reads atomic floats |
| `RaceManager` | Main |
| `BestTimeStore` | Main (blocking File.WriteAllText; only on lap crossings) |

No explicit locks other than the receiver `_lock`s for telemetry snapshots
and video frame hand-off. All cross-thread state is a small struct + scalars.

## Failure modes

| Failure | Unity behavior |
|---|---|
| Jetson unreachable | `StatusPanel` → red "NO LINK"; cockpit camera planes show no-signal pattern; `EngineSynth` cuts out; `ControlSender` still sends (harmless) |
| One camera drops | Other camera keeps streaming. "NO SIGNAL" pattern on failed channel. Re-connects on its own when bridge restores. |
| Gamepad unplugged | Keyboard fallback activates (WASD + SPACE/R) so the HUD still functions for demos. |
| User quits mid-race | `OnApplicationQuit` sends BTN_DISARM + 6 neutral control packets so the car doesn't wait for watchdog timeout. |
| Network packet storm | Send rate capped (4 emits/frame burst cap); receive rate naturally bounded by the incoming UDP rate. |

## What is *not* built yet

Explicit list so future work doesn't accidentally assume these exist:

- No **hardware failsafe** (ESP32-S3) — v0.2 work, per `onboard-compute-recommendation.md`.
- No **ground-truth ghost car** — only input-trace ghost, shown via HUD overlays.
  Proper 3D ghost needs wheel encoders or camera-based visual odometry.
- No **autonomous "save the car"** mode — scoped post-pilot; the Jetson is on
  the car exactly so we can add this later without changing the driver station.
- No **unity-side H.264 decode** — intentionally deferred to the Python bridge.
- No **multiplayer** — the leaderboard is local-file only; networked ladder
  is a backend task.
- No **force feedback** — rumble over the Unity Input System is the v0.2
  path for a generic gamepad; full FFB waits on the custom direct-drive
  wheel Benno plans to build for the arcade deployment.
- No **gamepad calibration UI** — falls back on macOS's built-in
  Bluetooth pane / Game Controller framework.
