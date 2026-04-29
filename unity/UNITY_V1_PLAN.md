# Unity driver station — V1 plan

Last updated 2026-04-29. Read this alongside `STATE.md` at the workspace root and the existing `README.md` / `docs/architecture.md` in this folder.

## Where we are

The v0.1 Unity driver station in this folder is **~80% of what we need** for V1. The architecture is sound: Bootstrapper + Config + Network/Video/UI/Race/Audio modules, with a Python video-bridge sidecar that decodes RTP H.264 → JPEG TCP → Unity Texture2D.

The main blocker is that v0.1's wire protocol (24-byte i16 quantized + 40-byte telemetry) is **incompatible** with the v0.2 protocol (32-byte float32 + CRC32 + 16-byte echo) that the live Python `rcpilot` package on GitHub speaks. Until protocols align, the Unity app and the Jetson can't talk.

## What's done in this session

- **`Assets/Scripts/Network/Protocol.cs` is now v0.2.** Byte-identical to `rcpilot/src/rcpilot/protocol.py`. Includes a CRC32 implementation that matches Python's `zlib.crc32` exactly. Adds `EchoPacket`. Drops the v0.1 ControlPacket and TelemetryPacket structs.

  Once the next set of tasks is done, the Unity app will pack 32-byte control packets that the Python `rcpilot-echo-server` running on the Jetson can validate (CRC) and echo back.

## What's next (in priority order)

### 1. Refactor `ControlSender.cs` to compile against v0.2 Protocol.cs

`ControlSender.cs` currently has fields like `public short steering;` and packs the v0.1 `ControlPacket`. After the Protocol.cs update, it won't compile. Specific changes:

- Change `short steering, throttle, brake, reverse;` to `float steering, throttle, brake, clutch;`
- Remove `reverse`, `buttons`, `assist` fields (not in v0.2 wire format)
- Remove `SendDisarmAndQuit()` (no buttons in v0.2 — the watchdog is the estop)
- In `EmitOnce()`, replace the v0.1 `ControlPacket` initializer with the v0.2 one
- Default rate stays 200 Hz; bump to 250 Hz to match rcpilot/control_sender.py if you want exact parity

### 2. Replace `TelemetryReceiver.cs` with `EchoReceiver.cs`

V0.2 doesn't have a 40-byte telemetry packet. The car sends a 16-byte echo per control packet, and that's it. The cockpit computes RTT locally from a per-seq table of send times.

`TelemetryReceiver.cs` referenced fields like `batteryMv`, `pwmSteering`, `state`, `wifiRssi` — none of which exist in v0.2. Two paths:

- **Quick:** delete `TelemetryReceiver.cs`, write a fresh `EchoReceiver.cs` that listens on UDP 5005 (same port as control), unpacks 16-byte echoes via `EchoPacket.TryUnpack`, and exposes an RTT histogram (mean/p50/p95/p99) for the HUD.
- **Long-game:** keep `TelemetryReceiver.cs` but mark it dormant until we have actual sensor telemetry (IMU, ESC, battery) — then wire it up as a separate channel on a different port.

For V1 just do the Quick path. The HUD's `StatusPanel.cs` currently expects telemetry fields — it'll need to be reduced to "RTT + link health + last-packet-age" for V1.

### 3. Refactor `video-bridge/bridge.py` to single-camera

The current bridge expects two RTP streams (cam0 UDP 5000, cam1 UDP 5001) and emits two TCP feeds (9000 + 9001). V1 has one IMX219 camera on UDP 5004 per `rcpilot/config/default.yaml`. Strip the bridge to:

- One UDP listener on port 5004
- One TCP server on port 9000 (or whatever port `VideoBridgeClient` defaults to)
- Drop the dual-camera config, dual-process launch in `bridge.sh`

`VideoBridgeClient.cs` and `CockpitBuilder.cs` may also reference cam0/cam1 — likely both can stay if we treat the second camera as "not connected" gracefully (the existing JPEG-receive loop just times out for cam1).

### 4. Update `Assets/StreamingAssets/config.json`

Set defaults to V0.2:

```json
{
  "network": {
    "jetsonIp": "192.168.55.1",
    "controlPort": 5005,
    "videoPort": 5004,
    "videoBridgeTcpPort": 9000
  },
  "control": {
    "sendHz": 250,
    "watchdogMs": 200
  }
}
```

Drop the v0.1 button mapping section. Keep input axis mapping (we'll need it for the G920).

### 5. Verify it compiles + opens cleanly in Unity

Open the project in Unity Hub on the Windows cockpit machine (12700H + 3070 Ti). Steps the human does:

1. **Install Unity 2022.3.47f1** via Unity Hub. Add **Windows Build Support (IL2CPP)** module if it's not already installed.
2. Unity Hub → **Add** → point at `rc-pilot-code/unity-driver-station/`. Let it resolve packages on first open (~2-5 minutes).
3. Open the project. Check the **Console** for compile errors. Steps 1-4 above must be done first or the project won't compile.
4. **File → New Scene** → save in `Assets/Scenes/` as `Cockpit.unity`.
5. Create an empty GameObject in the scene, name it `Boot`, **Add Component → Bootstrapper**. The Bootstrapper script handles wiring the rest.
6. Hit Play with the Jetson reachable at `192.168.55.1`. The video bridge sidecar must be running first — `python3 video-bridge/bridge.py`.

## What V1 driver display looks like when done

- Live IMX219 video filling most of the screen, decoded via the Python sidecar
- A simple HUD overlay:
  - Steering meter (-1.0 to +1.0)
  - Throttle bar (0 to 1.0)
  - Brake bar (0 to 1.0)
  - RTT readout (mean / p95) — green if < 20 ms, yellow 20-50, red > 50
  - Connection status (link OK / link degraded / link lost)
  - "Last packet ago" indicator (so you can see packet flow at a glance)
- G920 wheel + pedals driving the control packets at 250 Hz to the Jetson
- Camera feed updates ~30 fps via the Python bridge (limited by JPEG re-encode, NOT by network or decode)

Things explicitly **NOT** in V1:
- Speed display (no ESC telemetry yet)
- Lap timing (no positioning system yet)
- AR cones-as-walls (Phase 2; needs YOLO + tracking)
- Driving line (Phase 3)
- VR / Pimax integration (later)
- Multi-car / ghost replays (later)

## Migration — should this folder become `rcpilot/unity/`?

Eventually yes — the Python codebase is now `rcpilot/` and lives on GitHub. The Unity client ought to live in the same repo so they version together. But not yet — let's get V1 compiling and running first, then move atomically once the protocol is definitely aligned.

When we do migrate:

```bash
# From the workspace root, after V1 is working:
mv rc-pilot-code/unity-driver-station rcpilot/unity
cd rcpilot/unity
# update README, paths, docs to reflect new location
git add . && git commit -m "Move Unity driver station into rcpilot/"
```

## Tasks tracked in the assistant's task list

The next-session work is filed as tasks #28-31:

- #28 Refactor `ControlSender.cs` for v0.2
- #29 Add `EchoReceiver.cs`
- #30 Update `video-bridge/bridge.py` to single-camera
- #31 Update Unity config.json + Bootstrapper to v0.2 defaults

After all four are done, V1 should compile and we move to scene setup + first Play test.

## File-level summary of v0.1 → v0.2 changes

| File | Status |
| --- | --- |
| `Assets/Scripts/Network/Protocol.cs` | ✅ Rewritten to v0.2 in this session |
| `Assets/Scripts/Network/ControlSender.cs` | ⏳ Needs refactor (task #28) |
| `Assets/Scripts/Network/TelemetryReceiver.cs` | ⏳ Replace with `EchoReceiver.cs` (task #29) |
| `Assets/Scripts/Network/VideoBridgeClient.cs` | ⏸ Likely fine; verify after compile |
| `Assets/Scripts/Util/ByteOps.cs` | ⏸ May need `WriteF32LE` / `ReadF32LE` if not present |
| `video-bridge/bridge.py` | ⏳ Single-camera (task #30) |
| `Assets/StreamingAssets/config.json` | ⏳ V0.2 defaults (task #31) |
| `Assets/Scripts/Core/Bootstrapper.cs` | ⏳ Drop telemetry-receiver wiring; add echo-receiver wiring (part of #31) |
| `Assets/Scripts/Core/Config.cs` | ⏳ Drop telemetry/button schema fields (part of #31) |
| `Assets/Scripts/UI/StatusPanel.cs` | ⏳ Reduce to RTT + link health (V1 doesn't have battery/RSSI yet) |
| Everything else (HUD, UI, Race, Audio, Sim, Util, Assists) | ⏸ Leave alone for now |

## Note for next session's assistant

When we resume, the highest-leverage path is: **task #28 → #29 → check it compiles → #30 → #31 → first Play test**. Don't rewrite anything else unless it blocks compilation. The user prefers concrete progress over architectural improvements.
