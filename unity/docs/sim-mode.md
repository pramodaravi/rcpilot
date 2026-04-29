# Sim mode

Drive the full driver-station stack against an in-engine RC car on a
procedural Novi-inspired kart track. No Jetson, no car, no Wi-Fi. Use it for:

- Demos to Full Throttle when real hardware isn't plugged in.
- Iterating on the driver-assist system (racing line, corner speed governor,
  barrier warnings) — the sim is the test bed.
- Tuning the cockpit, HUD, engine audio, and results screen on your own
  schedule without a car on the bench.

## Turning it on

Two options:

1. **Copy the preset:**
   ```
   cp Assets/StreamingAssets/config.sim.json Assets/StreamingAssets/config.json
   ```
2. **Edit in place:** flip `sim.enabled` to `true` in `config.json`.

Then press Play in the Unity Editor (or run the built macOS player).

## What you get

- **Procedural track**: ~335 m (1100 ft) loop, kart-track-shaped — one long
  main straight, a hairpin, a chicane, a right-hand sweeper, an infield
  section. Geometry generated at runtime from a waypoint list in
  `SimTrack.cs` — edit there if you want to reshape the track.
- **Arcade RC physics**: bicycle-model steering, lateral grip budget, air
  drag, speed-limited to ~50 km/h. Calibrated for 1/8-scale RC (3.5 kg,
  0.33 m wheelbase, 35° max front-wheel angle).
- **FPV camera** on the car's nose → routed into the cockpit's main screen.
- **Chase camera** behind and above the car → routed into the cockpit's
  secondary screen. Press C (or the `btnToggleCam` pad button) to swap them.
- **Synthetic telemetry** published at 50 Hz — speedometer, engine synth,
  status panel, and race manager all read from it normally.
- **Lap detection** via a start/finish trigger collider. Same code path as
  the hardware gamepad's lap-mark button, so the race manager doesn't care
  which fires.
- **Reset-car button** (Y on a pad, R on keyboard) pops the car back to the
  start line if you clip into a barrier.

## Controls (sim mode)

| Input           | Gamepad             | Keyboard |
|-----------------|---------------------|----------|
| Steer           | Left stick X        | A / D    |
| Throttle        | Right trigger       | W        |
| Brake           | Left trigger        | S        |
| Start race      | RB (lap mark)       | L        |
| Reset car       | Y                   | R        |
| Swap cam        | LB                  | C        |
| Toggle menu     | —                   | Esc      |

The first time you press the lap-mark button (RB / L), RaceManager starts
a 3-second countdown. GO toast appears, then you race. Each crossing of
the start line fires a lap. Race ends after `race.targetLapCount` laps
(default 5).

## What sim mode does NOT do

- **No UDP/TCP network activity.** ControlSender and TelemetryReceiver are
  created but not Configured — so no packets fly. This also means:
  - Sim mode works offline.
  - If you leave the real bridge/Jetson running on the same machine, sim
    mode won't collide with them.
- **No ghost recording visuals.** The existing GhostRecorder samples wheel
  inputs + telemetry each frame. In sim mode those samples are real and
  stored, but we haven't wired a visualization for them yet — that's a
  driver-assist-system follow-up (replay the best lap as a semi-transparent
  ghost car).
- **No dynamic weather / lighting / crowd noise.** Single fixed sun, gray
  asphalt, gray ambient. The goal is a credible rolling demo, not Forza.

## How it hooks into the existing code

```
Config.sim.enabled = true
        ↓
Bootstrapper.BuildSimWorld()
        ↓
SimWorld ──────► ground + sun at y=+1000 (so cockpit camera can't see it)
  └─► SimTrack ─► procedural loop (Catmull-Rom through waypoints,
                   triangle-strip mesh, barrier boxes, start/finish
                   trigger collider, racing-line sample array)
  └─► SimCar ────► Rigidbody + arcade physics, reads WheelInput.state
  └─► SimCameraRig ► FPV cam on car → RenderTexture → cockpit screen 0
                    Chase cam spring-follow → RenderTexture → cockpit screen 1
  └─► SimLapTrigger ► OnTriggerStay(car) with directional sign-flip
                       detection → wheel.FireLapMark() → RaceManager
  └─► SimTelemetryBridge ► 50 Hz synthetic packets →
                           TelemetryReceiver.InjectSimulated(pkt)
```

The cockpit, HUD, race manager, engine synth, and menu code are unmodified
by sim mode — they read from the same interfaces they always would. This
is deliberate: when real hardware shows up, disabling sim mode (flip
`sim.enabled=false`) hands control back to the network path without any
code churn.

## Physics tuning

Knobs in `sim.*` (both `config.json` and `config.sim.json`):

| Field               | Default | Notes |
|---------------------|---------|-------|
| `straightSpeedMps`  | 13.5    | Top speed (~50 km/h). Raise for scale tests. |
| `accelMps2`         | 9.0     | 0→top in ~1.5 s. |
| `brakeMps2`         | 18.0    | Strong regen. |
| `maxSteerDeg`       | 35      | Front-wheel deflection. Too high = wash out. |
| `wheelbaseM`        | 0.33    | 1/8 RC scale. Shorter = twitchier. |
| `gripN`             | 35      | Lateral grip budget. Lower = more drift. |
| `drag`              | 1.2     | Linear air drag. Mostly kills top speed. |
| `trackWidthM`       | 4.0     | Kart-track-width-at-RC-scale. |

If you want to record "reference" lap times for Full Throttle to calibrate
against real-car expectations, this is the place to tune — the arcade model
is close enough to real RC behavior that lap times here are a reasonable
pre-hardware estimate.

## Known limitations / follow-ups

- Track shape is one hand-drawn layout in `SimTrack.BuildCenterline()`. Not
  a cycle-accurate reproduction of Novi's actual kart track (the real
  layout isn't public). Swap in anchor points if you get surveyed data.
- Racing line (exposed as `simTrack.RacingLine`) is currently the
  centerline — fine as a seed for the driver-assist system but not
  apex-optimal. The assist work will replace it with a curvature-minimized
  line.
- No AI opponents. Deliberate — the product for Full Throttle is
  multi-driver local racing with other humans, not AI.
- No force feedback. Benno plans a custom direct-drive wheel for the
  actual arcade deployment, so we're holding off on FFB integration until
  that hardware exists.
