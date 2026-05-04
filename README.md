# rcpilot — v0.2

Control + video + telemetry stack for the **Full Throttle Karting RC arcade attraction**: a customer drives an RC car on the indoor kart track from a sim cockpit (steering wheel + pedals + screen + force feedback). This repo is the software half — the Jetson onboard the car and the cockpit-side input/display code.

## Status

- **Today:** the bench loop works end-to-end. Two IMX219 cameras -> Jetson Orin Nano real-time warped/blended 2560x720 H.264 panorama -> Wi-Fi/USB-C network -> cockpit display. Control packets travel back the other way at 250 Hz with measured ~5 ms RTT over USB-C virtual ethernet. Force-feedback synthesis, sensor stack, and motor drivers are stubbed (no hardware connected yet).
- **Blocked on:** Jetson Orin NX 8GB module ordering (~$450, ~2-week lead). Once it lands, swap the Nano module → NX module on the existing dev kit carrier, set `RCPILOT_ENCODER=nvv4l2h264enc`, and end-to-end glass-to-glass latency drops from ~100 ms to ~35 ms.

## Repo layout

```
rcpilot/
├── pyproject.toml          # build + dep config
├── requirements.txt        # for environments without pyproject support
├── Makefile                # convenience targets (install, test, lint)
├── README.md               # you are here
├── LICENSE                 # MIT
├── config/
│   └── default.yaml        # default config; copy to local.yaml to override
├── src/rcpilot/
│   ├── protocol.py         # ControlPacket / EchoPacket dataclasses + CRC
│   ├── config.py           # YAML loader + dataclasses
│   ├── jetson/
│   │   └── echo_server.py  # car-side UDP echo server with watchdog
│   └── cockpit/
│       ├── joystick.py     # pygame-ce abstraction (Asetek + Xbox + ...)
│       └── control_sender.py
├── tests/
│   ├── test_protocol.py    # pack/unpack/CRC round-trip
│   ├── test_config.py      # YAML override behaviour
│   └── test_loop.py        # localhost integration test
├── scripts/
│   ├── jetson/
│   │   ├── start_video.sh  # single-camera GStreamer video sender
│   │   ├── start_video_stitched.sh  # two-camera real panorama sender
│   │   ├── stitch_video.py  # OpenCV alignment, warp, feather blend, RTP out
│   │   └── install.sh      # idempotent Jetson install
│   ├── cockpit/
│   │   ├── view_video.ps1  # Windows GStreamer receiver
│   │   ├── view_video.sh   # macOS / Linux receiver
│   │   └── start_sender.ps1
│   └── dev/
│       └── run_local_loop.sh   # localhost echo + fake sender, no hardware
└── docs/
    ├── architecture.md     # high-level architecture
    ├── protocol.md         # wire format spec
    └── deployment.md       # per-host bring-up
```

## Hit Play (the daily flow)

Once the one-time setup below has been done on each machine, the recurring workflow is:

1. **Power on the Jetson.** Wait ~30 seconds. systemd brings up `rcpilot-echo` and `rcpilot-video` automatically — both cameras, the stitched RTP H.264 stream, and the UDP echo server are all running before you sit down at the cockpit.
2. **Open Unity** at `unity/unity-driver-station/` and **hit Play**. The Bootstrapper auto-spawns the local Python video-bridge sidecar, opens the control socket, and starts painting frames. The HUD shows link state ("LINK OK / DEGRADED / NO LINK") and RTT in the top-left.
3. **Drive.**

That's it. No SSH, no terminal, no script juggling. If link state stays NO LINK for more than ~5 seconds, see *When it doesn't work* below.

## One-time setup

### Jetson side (the car) — once per Jetson

```bash
git clone <repo> ~/rcpilot && cd ~/rcpilot
sudo bash scripts/jetson/install.sh           # creates venv, installs deps
sudo bash scripts/jetson/install_services.sh  # enables auto-start on boot
```

`install_services.sh` is what makes "hit play and it just works" possible — it installs both systemd units, drops a documented `/etc/default/rcpilot-video` env file with every tunable, and enables the services so they come up on every boot.

To change a runtime knob (e.g. switch the seam-snap to skin-tone for testing):

```bash
sudo $EDITOR /etc/default/rcpilot-video      # uncomment a line, save
sudo systemctl restart rcpilot-video          # ~3s, then back to streaming
```

The defaults file is preserved across re-runs of `install_services.sh` — your edits won't be clobbered when you pull and re-install.

### Cockpit side (Windows) — once per cockpit

```powershell
git clone <repo> rcpilot
cd rcpilot
python -m pip install -e .[cockpit,dev]

# One-time: identify your wheel's axis layout. Wiggle each control, note
# which axis is which, edit config/local.yaml.
rcpilot-identify-joystick

# Open unity/unity-driver-station/ in Unity 2022.3.47f1 and hit Play.
```

### Running locally without any hardware

```bash
pip install -e .[dev]
pytest                              # full test suite
bash scripts/dev/run_local_loop.sh  # fake echo + sender on 127.0.0.1
```

## When it doesn't work

| Symptom | First thing to check |
| --- | --- |
| HUD says NO LINK after Play | Jetson powered on? On the same Wi-Fi? Try `ping 192.168.1.53` from the cockpit. |
| Link OK but black video | `journalctl -u rcpilot-video -f` on the Jetson. Camera open errors → `sudo systemctl restart nvargus-daemon`. |
| Doubled hand in the overlap region | Set `RCPILOT_STITCH_SEG=skin` in `/etc/default/rcpilot-video` and restart. Real ML segmentation (`yolo26`) is the long-term fix. |
| Cameras moved, image looks wrong | Set `RCPILOT_STITCH_RECALIBRATE=1` in `/etc/default/rcpilot-video`, restart, then revert the flag once the new homography is cached. |
| Cockpit IP changed (new DHCP lease) | `RCPILOT_COCKPIT_IP=<new-ip>` in `/etc/default/rcpilot-video`, restart. |

For deeper debugging, you can still run the pipeline manually instead of through systemd:

```bash
sudo systemctl stop rcpilot-video
RCPILOT_COCKPIT_IP=192.168.1.247 \
  RCPILOT_STITCH_SEG=skin \
  bash scripts/jetson/start_video_stitched.sh
```

## Configuration

Per-host overrides live in `config/local.yaml` (gitignored). Copy `config/default.yaml` and edit:

```yaml
# config/local.yaml
network:
  jetson_ip: 192.168.55.1   # or your Jetson's Wi-Fi IP
control:
  joystick_index: 0
  axes:
    steering: 0
    throttle: 5
    brake: 4
    clutch: 1
video:
  encoder: x264enc          # change to nvv4l2h264enc on modules with NVENC
  bitrate_kbps: 18000       # dual-camera panorama sender default
```

The Python entries (`rcpilot-echo-server`, `rcpilot-control-sender`) all accept `--config <path>` to point at any YAML file. The `start_video.sh` Jetson script reads the same values via env vars (`RCPILOT_COCKPIT_IP`, `RCPILOT_ENCODER`, etc.).

## What changed from v0.1

This codebase replaces `rc-pilot-code/` (committed Apr 23-24) and incorporates everything we learned during the Apr 29 bench bring-up. The architectural shape is the same; the differences are corrections:

| Concern | v0.1 | v0.2 (this) |
| --- | --- | --- |
| Compute assumption | "Orin Nano Super" with NVENC | Orin Nano (no NVENC) → swap to NX later |
| Wire protocol | 24-byte control + 40-byte telemetry | 32-byte control + 16-byte echo (tested end-to-end) |
| Wheel target | Logitech G920 | Asetek Forte Tony Kanaan + Xbox bench fallback |
| Config format | YAML (kept) | YAML (kept) — same loader pattern |
| Packaging | Loose scripts under jetson/ + windows/ | Proper Python package, console-script entry points |
| Tests | None | pytest suite covering protocol, config, integration |
| CRC | Documented but inconsistent | Mandatory, tested for corrupt-and-drop behaviour |

The v0.1 PWM driver, failsafe, and Unity driver-station code paths are still relevant and live in `rc-pilot-code/` for reference. They'll get migrated into v0.2 once the corresponding hardware (PCA9685, ESP32-S3, sensor breakouts) is on the bench — not before, because design choices depend on what the hardware actually does.

## License

MIT. See `LICENSE`.
