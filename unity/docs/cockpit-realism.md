# Cockpit realism — making the driver feel they're IN the kart

The real RC car has ONE forward camera bolted to the nose. That's a
"bumper cam" view — no fenders, no steering wheel, no dashboard, no sense
of vehicle width. The driver at the cockpit sees raw video and has no
spatial reference for their car.

To convert that into a "I'm sitting in a kart" feel, we don't re-shoot
the video. We overlay a virtual kart cockpit IN 3D around the live video
feed. The FPV video plays on a "windshield" plane; the virtual cockpit —
steering wheel, dashboard, hood silhouette, A-pillars, fender tips — is
rendered in front of/around it. Parallax and scale sell the illusion.

This doc works out the geometry so the numbers in the code aren't
magic.

## The scale problem

Two different scales coexist:

1. **Physical RC car**: 0.45m long, 0.25m wide, camera on nose.
2. **Virtual kart the driver feels they're in**: 1.8m long, 1.2m wide,
   driver seat ~0.8m from front bumper, eye ~0.9m above ground.

The ratio is ~4:1 — our 1/8 RC is ~1/4 the size of a real go-kart, not
1/8. That's fine. What matters is the illusion.

Speed is unchanged by the illusion: the RC drives at 14 m/s (50 km/h) on
the real track and the video shows real 14 m/s motion. Dressing the
cockpit as a kart doesn't change what the driver sees through the
windshield.

## Where to put the virtual driver's eye

The cockpit camera (the one rendering the view to the HMD / cockpit
screens) is our "driver's eye". In the existing `CockpitBuilder`:

```
mainCamera.transform.position = (0, 1.15, -0.4)
mainCamera.fieldOfView        = 55°
```

Eye 1.15m above floor, 0.4m behind the cockpit origin, 55° FOV. Good
starting point — matches a real kart driver's seated eye height.

Main screen sits at `z=1.5`, scale `2.8 × 1.6`, so:

| quantity                | value |
|-------------------------|-------|
| screen distance from eye | 1.9 m |
| screen width             | 2.8 m |
| screen height            | 1.6 m |
| subtended horiz angle    | 2 · atan(1.4/1.9) = **72.8°** |
| subtended vert angle     | 2 · atan(0.8/1.9) = **46.0°** |

The FPV camera on the car captures at **78° horizontal** (we set this
in `SimCameraRig`). The 73° screen-angle is ~7% narrower than 78°, so
the video looks very slightly more zoomed than reality. The eye can't
tell — we're good.

## Virtual kart body geometry

To give the driver width cues, we render a kart body mesh IN the
cockpit scene. The driver's eye looks across this mesh at the
windshield-mounted video feed. What they see:

```
              ┌─────── main screen (video) ────────┐
 pillar L     │                                    │    pillar R
              │         (FPV feed plays here)      │
              │                                    │
              └────────────┬──────────────┘
                    ┌──────┴──────┐
                    │ DASHBOARD   │   (gauges, lap, warning lights)
                    └──────┬──────┘
               ┌───────────┴───────────┐
               │   STEERING WHEEL      │
               └───────────────────────┘
        [hood nose cone]  [left fender]  [right fender]
```

Virtual kart dimensions:

| part                    | size (m)          | where it sits in cockpit space |
|-------------------------|-------------------|--------------------------------|
| chassis main body       | 1.20 × 0.30 × 1.60 | centered at (0, 0.35, 0.30)    |
| nose cone (front hood)  | 0.45 × 0.15 × 0.50 | (0, 0.55, 0.90)                |
| fender L/R              | 0.25 × 0.15 × 0.50 | (±0.55, 0.45, 0.70)            |
| steering wheel          | ⌀0.35, shaft 0.25  | (0, 0.95, 0.45)                |
| dashboard face          | 0.70 × 0.20        | (0, 1.05, 0.55), tilted 20°    |

All positions are in the cockpit's local frame. Driver eye is at
`(0, 1.15, -0.4)` so the steering wheel is ~0.85 m ahead and 0.2 m
below the eye — a natural seated driving position.

## The "how wide is my car" trick

Driver eye at `(0, 1.15, -0.4)`. Fender tip at `(+0.675, 0.525, +0.95)`
(right-front corner of the kart body). From eye to tip:

- horizontal offset: 0.675 m
- longitudinal offset: 0.95 − (−0.4) = 1.35 m
- vertical offset: 1.15 − 0.525 = 0.625 m

Horizontal angle: `atan(0.675 / 1.35) = **26.6°**` off centerline.
Vertical angle below horizon: `atan(0.625 / 1.35) = **24.8°**`.

The main screen subtends ±36.4° horizontal at the eye, so the fender
tip sits inside the screen's horizontal span — at about 26.6/36.4 = 73%
of the way out toward the edge. Vertically, the screen spans roughly
±23° below horizon, so the fender tip at 24.8° sits just below the
screen's lower edge.

That's exactly where we want it: the fender pokes up into the bottom
corner of the video frame, framing the FPV feed like a real kart hood
framing the horizon. The driver instinctively knows "the width of my
car is this" because they see the fender tips in peripheral vision.

### Calibration knob

If the venue's cockpit has a bigger/smaller monitor than our nominal
`2.8 × 1.6` windshield, the fender-tip position drifts inside the
video frame. Solution: a single `VirtualCockpit.widthScale` knob
that linearly scales the virtual kart body. Default 1.0 (= kart-size).
0.6 pulls the fenders closer in (feels like a narrower car / more
peripheral space); 1.4 pushes them out.

## Steering wheel animation

The wheel rotates around its Z-axis (cockpit local frame) proportional
to `WheelInput.state.steering`, with a max rotation of ±270° (real karts
are 1.5 turns lock-to-lock on a quick rack). So:

```
wheel.localRotation = Quaternion.Euler(0, 0, -state.steering * 270f)
```

Negative sign because a right-hand turn (steer = +1) rotates the wheel
clockwise from the driver's view, which is negative Z in Unity's
right-hand coordinate system.

The wheel mesh is 6 cylinders arranged in a hexagon + 3 radial spokes +
a central boss. Cheap and reads as "steering wheel" at any resolution.

## Dashboard gauges

Three gauges on the dashboard face, rendered as 3D meshes so they feel
like physical dials instead of screen overlays:

1. **Speedometer**: analog arc from 0 to `sim.straightSpeedMps * 3.6`
   km/h. Needle rotates 0°–260°.
2. **Tachometer**: analog arc from 0 to 1.0 throttle (no actual RPM
   telemetry from a brushless ESC, so we fake it as throttle-rate).
3. **Status cluster**: four rectangular warning lights — ARM, GRIP
   (low-grip warning from `SimCar.LateralSlip`), HEAT (ESC thermal,
   from real telemetry when present), LAP-GAIN (flashes green if last
   lap was a PB, red if regression). Plus a 3-digit lap counter.

The gauges read from the same `GameState` / `TelemetryReceiver` feeds
the main HUD already uses, so there's no new data plumbing.

## Implementation

One module: `Assets/Scripts/Video/VirtualCockpit.cs`. It takes the
`CockpitBuilder` references (the cockpit root transform, the main
camera, the `WheelInput`, the `TelemetryReceiver`) and procedurally
builds:

- `KartBody` primitive group (chassis + nose + fenders)
- `SteeringWheel` primitive group (rotates in Update)
- `DashboardGauges` group (speedo needle, tach needle, status lights,
  lap counter — all update in Update against game state)

Wired into `CockpitBuilder.Build()` as a single `AddComponent` call at
the end, so the existing cockpit structure (pillars, ceiling, accent
strip) stays untouched.

## Follow-ups (not in v1)

- Material upgrade: the current cockpit is plain matte. Add some PBR
  texture noise on the chassis (brushed aluminum), painted-metal
  fenders, stitched-leather wheel rim. Big perceived-quality win,
  small effort — one texture atlas.
- Engine-RPM-linked wheel vibration (once a DD wheel is in place).
- Helmet visor frame — a subtle darkening at the top/bottom of the
  view sells "I'm wearing a helmet" more than any amount of
  dashboard detail.
- Mirror replacement for the secondary screen: currently it's a
  chase-cam feed. A real kart has no mirrors, but the secondary
  could become a rear-cam feed from the RC (second camera on back).
