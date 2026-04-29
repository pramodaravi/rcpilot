# rcpilot — v0.2

Control + video + telemetry stack for the **Full Throttle Karting RC arcade attraction**: a customer drives an RC car on the indoor kart track from a sim cockpit (steering wheel + pedals + screen + force feedback). This repo is the software half — the Jetson onboard the car and the cockpit-side input/display code.

## Status

- **Today:** the bench loop works end-to-end. IMX219 camera → Jetson Orin Nano (software-encoded H.264) → Wi-Fi/USB-C network → cockpit display. Control packets travel back the other way at 250 Hz with measured ~5 ms RTT over USB-C virtual ethernet. Force-feedback synthesis, sensor stack, and motor drivers are stubbed (no hardware connected yet).
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
│   │   ├── start_video.sh  # GStreamer video sender
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

## Quick start

### Jetson side (the car)

```bash
git clone <repo> rcpilot && cd rcpilot
sudo bash scripts/jetson/install.sh

# Activate the venv the installer created.
source /opt/rcpilot/venv/bin/activate

# Run the echo server in one shell.
rcpilot-echo-server -v

# In a second shell, run the video sender pointed at the cockpit:
RCPILOT_COCKPIT_IP=192.168.55.100 bash scripts/jetson/start_video.sh
```

### Cockpit side (Windows)

```powershell
# One-time setup
git clone <repo> rcpilot
cd rcpilot
python -m pip install -e .[cockpit,dev]

# Identify your wheel/controller's axis layout (one-time):
rcpilot-identify-joystick
# Wiggle each control, note which axis is which, edit config/local.yaml.

# Start the video receiver:
.\scripts\cockpit\view_video.ps1

# In a separate window, start the control sender:
.\scripts\cockpit\start_sender.ps1
```

### Cockpit side (macOS / Linux)

```bash
git clone <repo> rcpilot && cd rcpilot
python3 -m pip install -e .[cockpit,dev]

# Same workflow, bash equivalents:
bash scripts/cockpit/view_video.sh
python -m rcpilot.cockpit.control_sender
```

### Running locally without any hardware

```bash
pip install -e .[dev]
pytest                              # full test suite
bash scripts/dev/run_local_loop.sh  # fake echo + sender on 127.0.0.1
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
  encoder: x264enc          # change to nvv4l2h264enc after Orin NX swap
  bitrate_kbps: 8000
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
