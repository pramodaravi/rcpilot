# Driver assists

iRacing-style tiered driver aids layered on top of the driver station. Aimed
at the Full Throttle use case: casual drivers step up to a cockpit for the
first time and need to not spin off on lap one, but experienced drivers
should be able to turn the help off and race clean.

## Tiers

Four tiers (Off / Beginner / Intermediate / Expert) picked at race check-in
or cycled at runtime with **Tab**. Only Beginner and Intermediate blend in
any autopilot; Expert shows the racing line only, and Off is raw driving.

| Assist              | Off | Beginner | Intermediate | Expert |
|---------------------|-----|----------|--------------|--------|
| Racing line visible |     | yes      | yes          | yes    |
| Steer mix           |  0  |  0.45    |  0.20        |  0     |
| Speed governor      |  0  |  1.00    |  0.50        |  0     |
| Barrier warning     |  0  |  0.9 m   |  0.5 m       |  0     |

Numbers are knob values in `AssistTierConfig.For(level)`. Tune them there
after wheel-time — don't surface them in `config.json` (12 assist knobs in
a config file is a foot-gun).

## What each assist does

### 1. Racing line (visual)

A persistent colored line drawn on the tarmac along the apex-biased path
the controller thinks is fastest. Colors encode the speed profile:

- green: fast (near top speed)
- yellow: medium
- red: brake zone before an apex

Driver just has to follow the colors. Works even for Expert because seeing
the line doesn't take agency away.

### 2. Steer mix

Lerps between the driver's raw steering and a pure-pursuit target
(`toAimLocal.x` scaled × 1.6, clamped) that aims ~2 m + 0.4·v_mps ahead
along the racing line. Mix weight comes from the tier:

- Beginner (0.45): car pulls firmly toward the line — feels like a rail.
- Intermediate (0.20): subtle correction, driver stays in charge.
- Expert (0): nothing blended.

The mix is capped at 0.5 even if we tweak Beginner higher — beyond that
the driver stops feeling connected to the wheel and loses trust.

### 3. Speed governor

Looks ahead `1 + 0.6·v_mps` seconds along the racing line and reads that
point's max-safe-speed (precomputed by `RacingLineComputer` from curvature
and grip). If current speed > target, scrubs throttle proportionally.
Beyond 50% overshoot, layers in light brake as a save.

Governor gain scales the scrub aggressiveness:

- Beginner: 1.0 — car refuses to over-speed a corner
- Intermediate: 0.5 — noticeable but you can override it
- Expert: 0 — off

### 4. Barrier warning

`ProximitySensor` fires 7 raycasts in a forward fan (-90° to +90°) each
FixedUpdate and surfaces the shortest hit distance. When the distance
drops inside the tier's warning radius, the HUD flashes a "WALL!" toast
(rate-limited to every 0.6 s). When the direct-drive wheel exists, this
same signal drives a haptic pulse.

## Architecture

```
WheelInput (order=0)          raw driver inputs
        ↓ writes state
AssistController (order=500)  reads state, reads car pose, reads racing line
        ↓ overwrites state    reads proximity sensor
ControlSender (order=1000)    sends assisted state over UDP
```

The execution-order trick is the whole reason the assist stack doesn't
need to live inside WheelInput or ControlSender: it slots in between
them, and because FixedUpdate runs between Update frames, SimCar always
sees the assisted state even though SimCar reads in FixedUpdate.

In sim mode the controller is handed `SimCar` + `SimTrack` for pose and
geometry. When indoor positioning exists on the real car, swap those for
equivalents that return the same data shape — the assist math is
identical.

## Files

```
Assets/Scripts/Assists/
  AssistProfile.cs        — enum AssistLevel, per-tier knobs
  RacingLineComputer.cs   — apex-bias + speed profile algorithm
  RacingLineRenderer.cs   — LineRenderer with per-vertex speed colors
  ProximitySensor.cs      — 7-ray fan to nearest barrier
  AssistController.cs     — integrator, [DefaultExecutionOrder(500)]
  AssistLevelCycler.cs    — Tab key cycles tiers at runtime
```

## Tuning workflow

1. Launch in sim mode (`sim.enabled=true`).
2. Press Tab to cycle assist levels.
3. Drive a few laps at each tier.
4. Identify whichever feels wrong:
   - corners understeer on exit? steerMix too high
   - car won't let you carry entry speed? governorGain too high
   - wall warning triggers in wide corners? barrierWarnDistM too high
   - line turns red in straights? RacingLineComputer's back-pass too aggressive
5. Edit the numbers in `AssistTierConfig.For()` and rerun.

## Known limitations

- Racing line is recomputed once at sim start. Doesn't adapt to physics
  changes at runtime — call `AssistController.RebuildRacingLine()` if
  you edit grip/top speed mid-session.
- Pure-pursuit steering is naive. When the racing line crosses back on
  itself (chicanes), the aim-point walk may briefly aim "backward" if
  look-ahead exceeds the chicane arc length. Not observed yet on the
  Novi-shape track but keep an eye out on real surveyed tracks.
- No force-feedback. Benno's direct-drive wheel will plug into
  `AssistController.SteerAssist01` as a torque cue once the wheel exists.
- Assists currently only run in sim mode. Real-car mode waits on indoor
  positioning — otherwise the controller has no car pose to drive the
  steer-mix and governor off of.
