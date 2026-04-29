# Unity driver station

A Unity front-end for the RC-pilot tele-op stack. Delivers the racing-game feel
the existing `windows/driver_station.py` never could: a procedurally-built
cockpit, HUD speedometer + gauges, lap timing, best-time leaderboard, toast
notifications, engine sound synthesized from the car's throttle telemetry, and
both camera feeds projected into the 3D cockpit.

Functionally it **replaces** the existing Python Windows driver station. You
still run the Jetson service exactly as before — the Unity app sends the same
UDP protocol on the same ports.

## What's in this folder

```
unity-driver-station/
├── Assets/
│   ├── Scripts/
│   │   ├── Core/          # Bootstrapper, Config, GameState
│   │   ├── Network/       # UDP control sender, telemetry receiver, video bridge
│   │   ├── Input/         # Gamepad input (legacy Unity input)
│   │   ├── UI/            # Procedural uGUI HUD, menu, results, toasts, theme
│   │   ├── Race/          # Lap timing, best-time persistence, ghost recorder
│   │   ├── Video/         # Cockpit builder + camera textures
│   │   ├── Audio/         # Procedural engine synth + UI beep library
│   │   └── Util/          # ByteOps, Log, MovingAverage
│   └── StreamingAssets/
│       └── config.json    # ← edit this to change IPs, input axes, driver name
├── Packages/manifest.json
├── ProjectSettings/       # Input axes, project version, tag/layer defs
├── video-bridge/
│   ├── bridge.py          # RTP H.264 → JPEG TCP sidecar (one per camera, PyAV)
│   ├── bridge.sh          # Launch both bridges on macOS
│   └── requirements.txt
├── docs/
│   ├── unity-setup.md     # First-time macOS install + open
│   ├── architecture.md    # How the pieces fit together
│   └── input-mapping.md   # Gamepad → protocol bits
├── start-with-unity.sh    # One-click: launch bridges + remind you to open Unity
└── .gitignore
```

## Quick start (assuming Jetson car is already streaming)

Target platform is **macOS** (Apple Silicon or Intel). Full walkthrough in
`docs/unity-setup.md`; this is the 60-second version.

1. **Install Unity 2022.3 LTS** via Unity Hub, with Mac Build Support (IL2CPP).

2. **Install Python + FFmpeg:**
   ```
   brew install python@3.12 ffmpeg
   ```

3. **Install the video bridge's Python deps:**
   ```
   cd unity-driver-station
   python3 -m pip install -r video-bridge/requirements.txt
   ```

4. **Open the Unity project:**
   - Unity Hub → Add → point at `rc-pilot-code/unity-driver-station`.
   - Let Unity resolve packages (takes a minute on first open).
   - Create an empty scene (File → New Scene), save it in `Assets/Scenes/`.
   - Create an empty GameObject, name it `Boot`, and add the `Bootstrapper`
     component (Add Component → search "Bootstrapper").

5. **Edit `Assets/StreamingAssets/config.json`** — at minimum set:
   - `network.jetsonIp` → your Jetson's IP.
   - Verify gamepad axis/button indices if you're not using an Xbox-layout
     controller. See `docs/input-mapping.md` for discovery mode.

6. **Run the video bridges + Unity:**
   ```
   ./start-with-unity.sh
   ```
   Then press Play in the Unity Editor.

7. The cockpit appears; camera feeds populate within a second or two of
   the bridges connecting. The HUD turns green once telemetry is flowing.

## Controls (Xbox-layout defaults — edit config.json to remap)

| Gamepad / Keyboard | Action |
|---|---|
| Left stick X              | Steering |
| Right trigger (RT)        | Throttle |
| Left trigger (LT)         | Brake |
| A button / keyboard `1`   | Arm |
| B button / keyboard `2`   | Disarm |
| X button / keyboard SPACE | E-stop |
| Y button / keyboard `R`   | Reset (clear estop → idle) |
| LB bumper / keyboard `C`  | Toggle which camera is main |
| RB bumper / keyboard `L`  | Mark lap / start time trial |
| Backtick (\`) held        | Discovery log: print axes/buttons as you press them |
| Keyboard `ESC`            | Open/close menu |

Keyboard fallback (WASD) is always layered in if no gamepad is detected.
See `docs/input-mapping.md` for PlayStation / Switch Pro mappings.

## Architecture in 60 seconds

```
                         Jetson (rc-pilot-code/jetson/)
                         ─────────────────────────────────
                                   │  RTP H.264
                                   ▼
   ┌─────────────────┐       UDP 5000 ┌────────────┐
   │ video-bridge    │  ◄────────────│ cam0       │
   │  bridge.py      │       UDP 5001│ cam1       │
   │  (PyAV /        │  ◄────────────┘            │
   │   FFmpeg)       │                            │
   │                 │  JPEG TCP :9000/:9001      │
   │                 │──────────────────────────► │
   └─────────────────┘                            │
                                                  ▼
                    ┌───────────────── Unity driver station ─────────────────┐
                    │  VideoBridgeClient → Texture2D → CockpitBuilder planes │
                    │  TelemetryReceiver ← UDP :7412 ──── Jetson             │
                    │  ControlSender     → UDP :7411 ──── Jetson             │
                    │                                                        │
                    │  Gamepad → WheelInput → ControlSender                  │
                    │  Telemetry → HUD + EngineSynth + RaceManager           │
                    └────────────────────────────────────────────────────────┘
```

Full diagram in `docs/architecture.md`.

## Why a Python video bridge instead of native Unity H.264?

Unity on any desktop OS does not ship with a low-latency H.264 RTP
pipeline that keeps up with 60 fps. The reliable options (native
GStreamer plugin wrapped for Unity, commercial NDI, the official Unity
WebRTC package) each add either real money, fragility, or build
complexity we didn't want in v0.1.

The sidecar approach costs ~20 ms of added latency (decode + re-encode +
TCP) for a rock-solid pipeline in ~200 lines of Python (PyAV) and ~150
of C# receiver. See `docs/architecture.md` for the numbers.

When the pilot goes live and we need the last 20 ms back, swap the
sidecar for Unity WebRTC (which is what the technical architecture doc
recommends for the production wireless link). The Unity-side
`VideoBridgeClient` API is already a Texture2D push — any decoder that
outputs frames to that texture is a drop-in replacement.

## Does this replace the old `windows/driver_station.py`?

Yes. The Python driver station still works, and is useful as a minimalist
fallback for debugging, but the Unity app is what the pilot experience will
ship on. Both send the same protocol, so you can run either against the
Jetson without server-side changes.
