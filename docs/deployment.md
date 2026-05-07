# rcpilot — deployment

Per-host bring-up. Assumes you've cloned the repo on the host in question.

## Jetson (the car)

Tested on **Jetson Orin Nano 8GB Dev Kit**, JetPack 6.2, NVMe boot. Same instructions apply to **Jetson Orin NX 8GB** on the same carrier — the only change is `RCPILOT_ENCODER=nvv4l2h264enc` instead of the default `x264enc`.

### One-time install

```bash
sudo bash scripts/jetson/install.sh
```

This:

1. `apt install`s the GStreamer plugins missing from JetPack's stripped-down base image (`gstreamer1.0-plugins-bad`, `-ugly`, `-libav`, plus `nano` because the minimal image strips that too).
2. Creates a venv at `/opt/rcpilot/venv` and installs `rcpilot` in editable mode.
3. Smoke-checks the GStreamer elements we depend on.

### IMX219 device-tree configuration (one-time)

JetPack 6 ships the Orin Nano dev kit with no camera enabled in the device tree. After install:

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
```

Choose **Configure Jetson 24pin CSI Connector** → **Camera IMX219 Dual** → **Save and reboot**. After reboot, `ls /dev/video0` should show the camera. (See `docs/architecture.md` for sensor-mode → resolution table.)

### Running

```bash
source /opt/rcpilot/venv/bin/activate

# Echo server (control packets in, echoes out)
rcpilot-echo-server -v &

# Dual-camera sender - Jetson -> cockpit
RCPILOT_COCKPIT_IP=192.168.55.100 bash scripts/jetson/start_video_stitched.sh
```

### As a systemd service (production)

Install the echo and dual-camera panorama video services with:

```bash
sudo bash scripts/jetson/install_services.sh
```

The video unit runs `scripts/jetson/start_video_stitched.sh`. The default mode
is `RCPILOT_VIDEO_MODE=stitch`: one warped/blended panorama sent on UDP 5004.
On JetPack this path defaults to `RCPILOT_STITCH_ACCEL=vpi`, using NVIDIA VPI
CUDA remap for the per-frame image warps. It also enables
`RCPILOT_STITCH_DYNAMIC=1`, which refreshes the alignment from live frame pairs
in the background so camera flex or test-stand camera movement does not require
a service restart. Use `RCPILOT_VIDEO_MODE=native-sbs` only as a rescue/debug
mode if you need to prove both CSI cameras are alive.

## Cockpit — Windows

Tested on **Windows 10 + Python 3.10/3.11/3.13 with pygame-ce**. PowerShell, not the cmd prompt.

### One-time install

```powershell
git clone <repo> rcpilot
cd rcpilot
python -m pip install -e .[cockpit,dev]
```

Install **GStreamer for Windows** (Complete MSVC 64-bit installer from <https://gstreamer.freedesktop.org/download/>). Choose "Add to PATH" during install. Reboot or open a fresh PowerShell after install so PATH propagates.

If you skipped Add-to-PATH, our `view_video.ps1` script falls back to checking the standard install paths automatically.

### Identify your wheel/controller

```powershell
rcpilot-identify-joystick
```

Wiggle each control. Note which axis number moves for steering / throttle / brake / clutch. Edit `config/local.yaml`:

```yaml
control:
  axes:
    steering: 0   # whatever moved when you turned the wheel
    throttle: 5   # whatever moved when you pressed throttle
    brake: 4
    clutch: 1
```

### Running

Two PowerShell windows:

```powershell
# Window 1: video receiver
.\scripts\cockpit\view_video.ps1

# Window 2: control sender
.\scripts\cockpit\start_sender.ps1
```

Or directly via Python module:

```powershell
python -m rcpilot.cockpit.control_sender -v
```

### Common Windows gotchas

* **`gst-launch-1.0` not recognized**: GStreamer install path isn't in PATH. Either re-install with the Add-to-PATH option, or use the `view_video.ps1` script which has fallback path resolution.
* **`pygame` won't install**: try `pip install pygame-ce` instead. The community fork ships wheels for newer Python versions much faster than upstream.
* **SSH paste eats characters**: Windows PowerShell's SSH client sometimes drops the first ~100 bytes when pasting large blocks. Workaround is to either save scripts to a file locally first and `scp` them over, or paste in chunks. We've avoided this by making `rcpilot` a proper installable package — once installed, you don't need to paste any code into a terminal.

## Cockpit — macOS / Linux

```bash
git clone <repo> rcpilot && cd rcpilot
python3 -m pip install -e .[cockpit,dev]

# macOS: brew install gstreamer
# Ubuntu: sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,libav}

bash scripts/cockpit/view_video.sh
python -m rcpilot.cockpit.control_sender -v
```

## Local-only loopback (no hardware at all)

For verifying changes without a Jetson on the desk:

```bash
pip install -e .[dev]
pytest                              # full suite (~3 seconds)
bash scripts/dev/run_local_loop.sh  # fake echo + fake sender on 127.0.0.1
```

The integration test in `tests/test_loop.py` runs the real echo server against synthetic packets and validates the round trip. CI-friendly — no hardware, no GStreamer, no joystick.

## Picking IPs

| Scenario | jetson_ip | cockpit_ip |
| --- | --- | --- |
| Direct USB-C cable bench bring-up | `192.168.55.1` | `192.168.55.100` |
| Both on home Wi-Fi | Whatever `hostname -I` prints on the Jetson | Whatever the cockpit's NIC has |
| Production (Wi-Fi 6E AP) | Static-leased per car | Static-leased per cockpit |

Edit `config/local.yaml` once, then `--jetson <ip>` overrides on the command line for one-offs.
