# Input mapping — gamepad → protocol

The Unity driver station sends the same 24-byte control packets as
`windows/driver_station.py` on the old Windows path. What happens between
your gamepad and the wire is what this doc covers.

The in-code class is still named `WheelInput` because every other script
refers to it; renaming would churn a dozen files for no functional win.
"Wheel" here just means "whatever input device is plugged in". A real
direct-drive wheel is on the roadmap for the arcade deployment.

## Device support

Any gamepad Unity's legacy input sees as joystick 1:
- **Xbox** (Series, One) — wired or Bluetooth.
- **PlayStation** (DualSense, DS4) — Bluetooth.
- **Nintendo Switch Pro** — Bluetooth (buttons reorder vs. Xbox; check
  with discovery mode, below).
- **MFi gamepads** — Bluetooth (SteelSeries Nimbus, etc.).

Keyboard is always available as a fallback: WASD for steer/throttle/brake,
Space for E-STOP, R for reset, C for camera toggle, L for lap mark,
1 / 2 for arm / disarm.

## Axes

Unity surfaces raw axes as `joystick 1 axis N`. On macOS Sonoma+ the
common layouts are:

| Pad | Left stick X | Left stick Y | Right stick X | Right stick Y | L trigger | R trigger |
|---|---|---|---|---|---|---|
| Xbox (wired or BT) | 1 | 2 | 3 | 4 | 5 | 6 |
| DualSense / DS4 (BT) | 1 | 2 | 3 | 4 | 5 | 6 |
| Switch Pro (BT) | 1 | 2 | 3 | 4 | — (digital) | — (digital) |

`config.wheel.steeringAxis` = left stick X (default 1).
`config.wheel.throttleAxis` = right trigger (default 6).
`config.wheel.brakeAxis`    = left trigger (default 5).

## Trigger polarity

Xbox triggers idle at −1 and travel to +1. PlayStation triggers
**idle at 0** and travel to +1. The `triggersAreBiPolar` flag picks
between them:

```json
"triggersAreBiPolar": true     // Xbox (default)
"triggersAreBiPolar": false    // DualSense/DS4, most non-Xbox pads
```

If you see full throttle at rest, flip this bit.

## Steering

Symmetric around 0 — no polarity conversion. If the car steers the wrong
way, set `"invertSteering": true`. Mechanical stick play is masked by
`wheel.steeringDeadzone`; 0.08 is the default. Bump it to 0.12 if the car
twitches at rest; lower it to 0.04 if it feels slushy near center.

## Discovery mode — finding your axes/buttons

If a gamepad doesn't match the defaults, don't guess. Turn on discovery:

1. Set `"logDiscovery": true` in `config.json`, **or** hold the backtick
   key (`` ` ``) at runtime.
2. Press Play in Unity. The Console will print, at ~2 Hz:
   ```
   pad: axis1=0.52, axis2=-0.03, btn0
   ```
   as you wiggle sticks, squeeze triggers, and press buttons.
3. Note which axis number moves for each control, and which button index
   each face button reports. Write those into `config.json`.
4. Flip `logDiscovery` back to `false`.

## Protocol units

Units on the wire — same as the Windows driver station:

| Field | Range | Source |
|---|---|---|
| `steering` | −10000 … +10000 | `normalized × 10000` |
| `throttle` | 0 … +10000 | `normalized × 10000` |
| `brake` | 0 … +10000 | `normalized × 10000` |
| `reverse` | 0 … +10000 | 0 in v0.1 (not wired) |

The Jetson side (`jetson/control_receiver.py`) expects exactly these units.

## Buttons

`config.wheel.btnXxx` says which joystick-button index maps to which
semantic action. Unity's `KeyCode.Joystick1Button0..19` cover the first
20 buttons of joystick 1.

Xbox-on-macOS default layout (what `config.json` ships with):

| Index | Button | Action |
|---|---|---|
| 0 | A | ARM |
| 1 | B | DISARM |
| 2 | X | ESTOP |
| 3 | Y | RESET |
| 4 | LB | TOGGLE CAMERA |
| 5 | RB | LAP MARK |
| 6 | Back / View | — |
| 7 | Start / Menu | — |
| 8 | Left stick press | — |
| 9 | Right stick press | — |

PlayStation DualSense reorders these (X, ○, □, △ vs A, B, X, Y). Use
discovery mode to confirm.

## Protocol bits

The `WheelInput` script edge-detects each of the configured buttons, then
holds the corresponding protocol bit for ~80 ms (`EdgeHoldSec = 0.08f`)
so the Jetson sees a clean event even at its slower recv cadence:

| Bit | Name | Default gamepad button | Keyboard fallback |
|---|---|---|---|
| 0x01 | `BTN_ESTOP` | X (index 2) | SPACE |
| 0x02 | `BTN_RESET` | Y (index 3) | R |
| 0x04 | `BTN_ARM` | A (index 0) | 1 |
| 0x08 | `BTN_DISARM` | B (index 1) | 2 |
| 0x10 | `BTN_CAM` (Unity-only, swaps main cockpit camera) | LB (4) | C |
| 0x20 | `BTN_LAP` (Unity-only, starts race / marks lap) | RB (5) | L |

`BTN_CAM` and `BTN_LAP` aren't interpreted by the Jetson — they're purely
Unity-side signals. We piggyback them into the protocol bitfield so
future logging / replay captures the driver's race inputs alongside the
car inputs.

## Force feedback

Not implemented. Legacy input doesn't drive rumble or FFB. When we ship
the real direct-drive wheel for the arcade, we'll either:

- Bring in the `com.unity.inputsystem` package for gamepad rumble
  (dumb rumble only, no centering), or
- Talk directly to the wheel's native SDK for full FFB (centering,
  collision impulse, road-surface feedback).

The plan is to tie feedback to telemetry:
- Centering force scales with `pwm_throttle` magnitude.
- Rumble kick on ESTOP / FAULT state transitions.
- Low-rumble at a frequency that tracks `battery_mv` drop rate once the
  battery's below threshold.
