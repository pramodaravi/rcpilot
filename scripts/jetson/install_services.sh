#!/usr/bin/env bash
# Install + enable rcpilot systemd services on the Jetson so the echo server
# and video sender autostart on boot. Run once with sudo:
#
#   cd ~/rcpilot
#   sudo bash scripts/jetson/install_services.sh
#
# After this, the Jetson runs both services every boot. To inspect:
#   systemctl status rcpilot-echo
#   systemctl status rcpilot-video
#   journalctl -u rcpilot-echo -f
#   journalctl -u rcpilot-video -f

set -euo pipefail

if [[ $EUID -ne 0 && "${SKIP_ROOT_CHECK:-0}" != "1" ]]; then
    echo "ERROR: run with sudo." >&2
    echo "  (set SKIP_ROOT_CHECK=1 + custom SYSTEMD_DIR/DEFAULTS_DIR for unprivileged smoke tests)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
DEFAULTS_DIR="${DEFAULTS_DIR:-/etc/default}"
SKIP_SYSTEMCTL="${SKIP_SYSTEMCTL:-0}"

mkdir -p "$SYSTEMD_DIR" "$DEFAULTS_DIR"

for svc in rcpilot-echo.service rcpilot-video.service; do
    src="${SCRIPT_DIR}/${svc}"
    dst="${SYSTEMD_DIR}/${svc}"
    if [[ ! -f "$src" ]]; then
        echo "ERROR: missing $src" >&2
        exit 1
    fi
    install -m 0644 "$src" "$dst"
    echo "installed $dst"
done

# Per-host config drop-in for rcpilot-video. Lets you change
# RCPILOT_STITCH_SEG / RCPILOT_STITCH_ACCEL / etc. by editing one file
# instead of `systemctl edit`. We DO NOT clobber an existing
# /etc/default/rcpilot-video — that's where Promo's local tuning lives.
env_src="${SCRIPT_DIR}/rcpilot-video.env.example"
env_dst="${DEFAULTS_DIR}/rcpilot-video"
if [[ ! -f "$env_src" ]]; then
    echo "ERROR: missing $env_src" >&2
    exit 1
fi
if [[ -f "$env_dst" ]]; then
    echo "kept existing $env_dst (not overwritten)"
    echo "  diff against shipped template at ${env_src}:"
    diff -u "$env_src" "$env_dst" 2>/dev/null | sed 's/^/    /' | head -40 || true
else
    install -m 0644 "$env_src" "$env_dst"
    echo "installed $env_dst (defaults — edit to enable RCPILOT_STITCH_SEG etc.)"
fi

# rcpilot-video1.service is from the previous dual-stream architecture; remove
# any stale copy so it doesn't fight rcpilot-video for sensor 1.
if [[ -f "${SYSTEMD_DIR}/rcpilot-video1.service" ]]; then
    if [[ "$SKIP_SYSTEMCTL" != "1" ]]; then
        systemctl disable --now rcpilot-video1.service 2>/dev/null || true
    fi
    rm -f "${SYSTEMD_DIR}/rcpilot-video1.service"
    echo "removed legacy ${SYSTEMD_DIR}/rcpilot-video1.service"
fi

if [[ "$SKIP_SYSTEMCTL" == "1" ]]; then
    echo
    echo "SKIP_SYSTEMCTL=1 — skipping systemctl daemon-reload / enable. Files are in place."
    exit 0
fi

systemctl daemon-reload
systemctl enable --now rcpilot-echo.service rcpilot-video.service

echo
echo "Services installed and started. Check status with:"
echo "  systemctl status rcpilot-echo"
echo "  systemctl status rcpilot-video    # dual-cam stitched on UDP 5004"
echo
echo "To change runtime knobs without touching the unit file:"
echo "  sudo \$EDITOR ${env_dst}"
echo "  sudo systemctl restart rcpilot-video"
