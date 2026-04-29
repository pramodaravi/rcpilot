# Unity driver station — V1 plan

Last updated 2026-04-29. Read this alongside `STATE.md` at the workspace root and the existing `README.md` / `docs/architecture.md` in this folder.

## Where we are

The v0.1 Unity driver station in this folder is **~80% of what we need** for V1. The architecture is sound: Bootstrapper + Config + Network/Video/UI/Race/Audio modules, with a Python video-bridge sidecar that decodes RTP H.264 → JPEG TCP → Unity Texture2D.

The wire-protocol incompatibility that was blocking us (v0.1's 24-byte i16 quantized + 40-byte telemetry vs. the live Python `rcpilot` package's v0.2 32-byte float32 + CRC32 + 16-byte echo) **has been resolved.** The Unity app now packs 32-byte v0.2 control packets that the Python `rcpilot-echo-server` on the Jetson can validate and echo back.

## What's done

### Session 1 (Protocol.cs)

- **`Assets/Scripts/Network/Protocol.cs`** rewritten to v0.2. Byte-identical to `rcpilot/src/rcpilot/protocol.py`. Includes a CRC32 implementation that matches Python's `zlib.crc32` exactly. Adds `EchoPacket`. Drops the v0.1 ControlPacket / TelemetryPacket structs (TelemetryPacket re-added as a sim-mode-only stub in session 2).
- **`Assets/Scripts/Util/ByteOps.cs`** gained `WriteF32LE` / `ReadF32LE` helpers.

### Session 2 (everything else needed for compile + run)

- **`Assets/Scripts/Network/ControlSender.cs`** refactored for v0.2:
  - `short steering, throttle, brake, reverse` → `float steering, throttle, brake, clutch`
  - `buttons`, `assist`, `reverse` fields removed
  - `SendDisarmAndQuit()` removed — watchdog is the estop
  - Default rate 200 Hz → 250 Hz to match rcpilot/cockpit/control_sender.py
  - Added an `OnBytesReceived` event + a per-frame socket-drain loop so echoes flow without a background thread
- **`Assets/Scripts/Network/EchoReceiver.cs`** added. Subscribes to `ControlSender.OnPacketSent` (records send time per seq) and `ControlSender.OnBytesReceived` (matches 16-byte echoes, computes RTT). Exposes mean / p50 / p95 / p99 RTT, total packets sent / lost / received, and a 0-1-2 LinkHealth bucket for the HUD.
- **`Assets/Scripts/Network/TelemetryPacket.cs`** added as a sim-mode-only stub. Sim mode (`SimTelemetryBridge`) keeps synthesizing v0.1-shaped telemetry packets so the HUD speedometer, engine synth, and ghost recorder still work. Hardware mode never populates it. Includes a `TelemetryStates` static class with the v0.1 STATE_* constants (formerly on `Protocol`).
- **`Assets/Scripts/Network/TelemetryReceiver.cs`** preserved but **no longer binds a UDP port** in hardware mode — Bootstrapper just creates the component as the sim-mode sink for `InjectSimulated`.
- **`Assets/Scripts/Input/WheelInput.cs`** updated:
  - Sender writes are float, not short ×10000
  - `_sender.buttons / .assist / .reverse` writes removed
  - The button-bit constants (`BTN_ARM`, `BTN_DISARM`, etc.) moved from `Protocol` into `WheelState` since they're now cockpit-local
- **`Assets/Scripts/Assists/AssistController.cs`** updated:
  - Sender writes are float, not short ×10000
  - `_sender.assist` writes removed
- **`Assets/Scripts/Audio/EngineSynth.cs`** + **`Assets/Scripts/UI/StatusPanel.cs`** + **`Assets/Scripts/Sim/SimTelemetryBridge.cs`** swapped `Protocol.STATE_*` → `TelemetryStates.STATE_*`
- **`Assets/Scripts/Core/Bootstrapper.cs`** rewired:
  - Adds `EchoReceiver` and calls `echo.Configure(controlSender)` in hardware mode
  - Skips `telemetry.Configure(port)` (no v0.2 telemetry stream)
  - V1 single-camera by default; only builds `cam1` when `config.video.cam1Port > 0`
  - `OnApplicationQuit` no longer calls `SendDisarmAndQuit` (it's gone)
- **`Assets/Scripts/Core/Config.cs`** + **`Assets/StreamingAssets/config.json`**:
  - `network.jetsonIp` → `192.168.55.1` (USB-C virtual ethernet, matches bench bring-up)
  - `network.controlPort` → `5005`
  - `network.sendHz` → `250`
  - `network.telemetryPort` → `5006` (reserved, not bound in V1)
  - `video.cam1Port` → `0` (V1 single camera; set >0 to enable)
- **`video-bridge/bridge.py`** docstring + default `--in-port` 5000 → 5004 (matches `rcpilot/config/default.yaml`)
- **`video-bridge/bridge.sh`** + **`video-bridge/bridge.bat`** rewritten to launch a single bridge (RTP UDP 5004 → JPEG TCP 9000)

## What's next

### 1. Migrate to the Windows cockpit machine

The Mac is disk-tight; the cockpit is a 12700H + 3070 Ti + 32 GB + 600 GB free Windows 10 box and is the actual build target anyway. Migration plan in two passes:

**On the Mac (one-time):**

```bash
cd "/Users/pramodaravi/Documents/Claude/Projects/RC kart proposal"
git clone https://github.com/pramodaravi/rcpilot.git ~/rcpilot
cp -R "rc-pilot-code/unity-driver-station" ~/rcpilot/unity
# Add a Unity-standard .gitignore at ~/rcpilot/unity/.gitignore (Library/, Temp/,
# Obj/, Build/, Logs/, UserSettings/, *.csproj, *.sln, etc.) — see Unity's
# template at https://github.com/github/gitignore/blob/main/Unity.gitignore
cd ~/rcpilot
git add unity
git commit -m "Add Unity driver station (v0.2 protocol)"
git push origin main
```

**On the Windows cockpit machine:**

1. Install **Unity Hub** + **Unity 2022.3.47f1 LTS**. Add modules: **Visual Studio Community 2022**, **Windows Build Support (IL2CPP)**, **Documentation**.
2. Install **Git for Windows** and **GitHub CLI**, then `gh auth login` (browser OAuth).
3. `git clone https://github.com/pramodaravi/rcpilot.git C:\Users\Promo\rcpilot`
4. Unity Hub → **Add → Add project from disk** → `C:\Users\Promo\rcpilot\unity`
5. Open the project; Unity will resolve packages on first open (~2-5 min).
6. Console should be clean. If anything fails to compile, check that the new `EchoReceiver.cs` / `TelemetryPacket.cs` files made it across.

### 2. First scene + Play test

Once the project opens cleanly:

1. **File → New Scene** → save as `Assets/Scenes/Cockpit.unity`.
2. Add a Camera (or accept the Main Camera that comes with a new scene). Add an `AudioListener` to it.
3. Add an empty GameObject named `Boot`, **Add Component → Bootstrapper**.
4. Make sure the Jetson is up at `192.168.55.1` and the `rcpilot-echo-server` systemd unit is running.
5. In a separate Windows shell, run the bridge: `python video-bridge\bridge.py` (after `pip install -r video-bridge\requirements.txt`).
6. On the Jetson, start the H.264 sender: see `rcpilot/scripts/jetson-video-send.sh`.
7. Hit Play in Unity. Expected:
   - HUD paints
   - Camera feed appears in cockpit slot 0 (the only slot in V1)
   - RTT readout populates (~5-15 ms wired, 10-30 ms wireless)
   - G920 wheel + pedals drive the control packets

### 3. Reduce StatusPanel for V1

`StatusPanel.cs` currently reads telemetry fields (`batteryMv`, `wifiRssiNeg`, `state`) that hardware mode never populates. Reduce it to: RTT mean / p95 (from `Bootstrapper.Instance.echo`), link health colour (from `echo.LinkHealth`), last-echo-age in ms, packets sent / lost. Sim mode keeps showing the synthesized fields.

### 4. Verify it compiles + opens cleanly in Unity

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

Tasks #27–#31 are all complete:

- #27 ✅ Update Protocol.cs to v0.2 wire format
- #28 ✅ Refactor `ControlSender.cs` for v0.2
- #29 ✅ Add `EchoReceiver.cs`
- #30 ✅ Update `video-bridge/bridge.py` to single-camera
- #31 ✅ Update Unity config.json + Bootstrapper to v0.2 defaults

V1 should compile in Unity. Next: migrate to Windows, scene setup, first Play test.

## File-level summary of v0.1 → v0.2 changes

| File | Status |
| --- | --- |
| `Assets/Scripts/Network/Protocol.cs` | ✅ V0.2 wire format (32-byte control + 16-byte echo + CRC32) |
| `Assets/Scripts/Network/ControlSender.cs` | ✅ Float axes, 250 Hz, OnBytesReceived event |
| `Assets/Scripts/Network/EchoReceiver.cs` | ✅ New — RTT histogram, link health, packet stats |
| `Assets/Scripts/Network/TelemetryPacket.cs` | ✅ New — sim-mode-only stub + STATE_* constants |
| `Assets/Scripts/Network/TelemetryReceiver.cs` | ✅ Preserved; no longer binds UDP in hardware mode |
| `Assets/Scripts/Network/VideoBridgeClient.cs` | ⏸ Unchanged; works for V1 single-camera |
| `Assets/Scripts/Util/ByteOps.cs` | ✅ Has `WriteF32LE` / `ReadF32LE` |
| `video-bridge/bridge.py` | ✅ Default `--in-port 5004` to match Jetson config |
| `video-bridge/bridge.sh` + `.bat` | ✅ Single-camera launchers |
| `Assets/StreamingAssets/config.json` | ✅ jetsonIp 192.168.55.1, controlPort 5005, sendHz 250 |
| `Assets/Scripts/Core/Bootstrapper.cs` | ✅ Wires EchoReceiver; skips TelemetryReceiver UDP bind; cam1 only if cam1Port>0 |
| `Assets/Scripts/Core/Config.cs` | ✅ V0.2 network/video defaults |
| `Assets/Scripts/Input/WheelInput.cs` | ✅ Float-axis writes; BTN_* moved into WheelState |
| `Assets/Scripts/Assists/AssistController.cs` | ✅ Float-axis writes; assist field references gone |
| `Assets/Scripts/Audio/EngineSynth.cs` | ✅ STATE_* references point at TelemetryStates |
| `Assets/Scripts/UI/StatusPanel.cs` | ⚠ Compiles but mostly shows zeros in hardware mode — slated for V1 reduction |
| `Assets/Scripts/Sim/SimTelemetryBridge.cs` | ✅ STATE_RUNNING reference fixed |
| Everything else (HUD, Race, Audio, Util) | ⏸ Compatible, no edits needed |

## Note for next session's assistant

The Unity project should compile cleanly on first open. The highest-leverage path is now: **migrate to Windows → first Unity Play test → reduce StatusPanel for V1 → tune G920 axis indices via logDiscovery**. Don't rewrite anything else unless a Play test surfaces a real problem. The user prefers concrete progress over architectural improvements.
