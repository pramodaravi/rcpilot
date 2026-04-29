# Unity setup — opening the driver station on macOS

This project ships as source only — no binary `.asset` or `.prefab` files,
no Library/ or Temp/ caches. Unity regenerates everything on first open.

macOS (Apple Silicon or Intel) is the supported host. The Jetson still runs
Linux; only the driver station runs on the Mac.

## 1. Install Unity 2022.3 LTS

1. Install [Unity Hub](https://unity.com/download).
2. In Unity Hub → Installs → Install Editor.
3. Pick **2022.3** LTS. Any recent patch (2022.3.40 or later) is fine.
4. Modules to include:
   - **Mac Build Support (IL2CPP)** — for shipping a `.app`.
     On Apple Silicon, IL2CPP compiles ARM64 natively; no Rosetta needed.
   - Documentation (optional).

The editor is ~5 GB. While it downloads, set up Python and FFmpeg below.

## 2. Install FFmpeg and Python

The video bridge uses **PyAV**, which wraps FFmpeg. PyAV's pip wheel is
self-contained on macOS, so you only technically need FFmpeg installed if
you want the `ffmpeg` CLI for debugging (highly recommended — it's the
fastest way to prove the Jetson is actually streaming).

```
# One-time setup (skip any step you already have):
brew install python@3.12 ffmpeg
```

Verify:
```
python3 --version        # 3.11+ required, 3.12 recommended
ffmpeg -version          # any recent 6.x is fine
```

## 3. Install the bridge's Python deps

```
cd rc-pilot-code/unity-driver-station
python3 -m pip install -r video-bridge/requirements.txt
```

Smoke test:
```
python3 -c "import av; print('PyAV', av.__version__, 'FFmpeg lib OK')"
python3 -c "from PIL import Image; print('Pillow', Image.__version__)"
```

## 4. Open the project in Unity

1. Unity Hub → Projects → **Add** → pick
   `rc-pilot-code/unity-driver-station`.
2. If Hub asks to "update" to your installed editor patch, say yes.
3. First open takes 2–5 minutes while Unity imports packages and compiles C#.

## 5. Make a scene and attach the Bootstrapper

We don't ship a `.unity` scene file (those are binary and easy to corrupt
in git). Creating one takes 20 seconds:

1. In Unity, **File → New Scene → Basic (Built-in)** → Create.
2. **Save** the scene in `Assets/Scenes/` as `Main.unity`.
3. Delete the default `Main Camera` and `Directional Light` from the
   Hierarchy — the `Bootstrapper` creates its own camera and lighting.
4. **GameObject → Create Empty** → name it `Boot`.
5. With `Boot` selected, in the Inspector click **Add Component** →
   type `Bootstrapper` → Enter.
6. **File → Build Settings** → click **Add Open Scenes** so this scene is
   the startup scene when you build.
7. **Edit → Project Settings → Player → Resolution and Presentation** →
   set "Fullscreen Mode" to "Fullscreen Window" for the shipping build.

## 6. Pair a gamepad

Any standard gamepad Unity's legacy input sees as joystick 1 works:
Xbox (wired or Bluetooth), PlayStation (DS4/DualSense over Bluetooth),
Switch Pro, or an MFi controller.

- Bluetooth: **System Settings → Bluetooth**, put the controller in pair
  mode (Xbox: hold the pair button next to the LB bumper until the Xbox
  logo flashes fast; PS5: hold Share + PS button).
- USB: just plug it in.

Once it's connected, the driver station will log its name at startup
(e.g. `WheelInput: joystick[1] = 'Xbox Wireless Controller'`).

**If your gamepad's axes differ from the defaults** (Xbox-on-macOS
layout): see `docs/input-mapping.md` for how to discover the right
axis/button indices and update `config.json`.

## 7. Configure

Edit `Assets/StreamingAssets/config.json`:
- `network.jetsonIp` — the Jetson's IP on your Wi-Fi.
- `wheel.*` — axes and button indices. Defaults target an Xbox-layout
  controller on macOS Sonoma+. PlayStation DualSense idles its triggers at
  0 instead of -1, so set `"triggersAreBiPolar": false` for DS4/DualSense.

No network firewall tweaks are usually needed — macOS will prompt the
first time the Unity editor or built player opens a listening socket; hit
Allow.

## 8. Run

1. Start the Jetson service — follow `docs/bring-up.md` in the top-level
   `rc-pilot-code/`. Jetson should be streaming H.264 RTP to this Mac's
   ports 5000 and 5001.
2. From `unity-driver-station`, run `./start-with-unity.sh` — this spawns
   both video bridges in the background (logs in `.bridge-logs/`).
3. In Unity, press **Play**.

You should see:
- A dark cockpit with two rectangles on screen. Within 1–2 seconds of the
  bridges connecting, the rectangles turn into live camera feeds.
- The top-left status panel turns green, shows RSSI, telemetry age, loss.
- The lap timer ticks forward the moment you hit `L` (or the lap-mark
  button on the gamepad).

If nothing comes up, check the Unity Console (Window → General → Console)
and the bridge logs (`tail -f .bridge-logs/cam0.log`). Both use `[rc-pilot]`
prefixes to make grep easy.

## 9. Build a shipping player (optional)

1. File → Build Settings → **macOS** / Apple Silicon (or Intel) → Build.
2. Pick an output folder outside the project — e.g. `rc-pilot-code/dist/`.
3. `Assets/StreamingAssets/config.json` is packaged into the .app and
   stays editable at `<Build>.app/Contents/Resources/Data/StreamingAssets/config.json`
   for post-build tweaks.
4. The Python video bridge is NOT packaged into the .app. Ship the
   `video-bridge/` folder alongside the .app and document launching the
   bridges (or the `start-with-unity.sh` wrapper) first.

When distributing, macOS will quarantine an unsigned .app — users will
have to right-click → Open the first time. For a real install we'll
codesign + notarize before the arcade deployment; not required for the
pilot.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Black camera planes, "NO SIGNAL" pattern | Video bridges not running, or the Jetson isn't streaming. `tail -f .bridge-logs/cam0.log` — look for `RTP stream open` and `client connected`. |
| Status says "NO LINK" | Telemetry UDP port 7412 not reachable. Confirm Mac's Wi-Fi IP matches what the Jetson's `config.yaml` has as `driver_ip`. macOS firewall only blocks inbound on restrictive setups — usually fine. |
| Steering does nothing | Axis index wrong for your gamepad. Set `"logDiscovery": true` in `config.json`, press Play, wiggle the left stick, and watch the Unity Console — it'll print the axis number. Then set `wheel.steeringAxis` to that. Or hold the backtick key at runtime to flip discovery on temporarily. |
| Triggers full-on at rest | Your pad idles triggers at 0, not -1. Set `"triggersAreBiPolar": false`. |
| Engine sound stutters | Unity's audio DSP buffer too small — Edit → Project Settings → Audio → DSP Buffer Size → "Best latency" → "Good latency". |
| Unity complains about missing packages | Packages/manifest.json references URP + TMP; Unity should resolve these on first open. If it doesn't, Window → Package Manager → click "Refresh". |
| `pip install av` fails on Apple Silicon | Update pip: `python3 -m pip install --upgrade pip`. PyAV has prebuilt arm64 wheels on 11.0+; older pip may not find them. |
