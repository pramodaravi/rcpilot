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

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run with sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR=/etc/systemd/system

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

# rcpilot-video1.service is from the previous dual-stream architecture; remove
# any stale copy so it doesn't fight rcpilot-video for sensor 1.
if [[ -f "${SYSTEMD_DIR}/rcpilot-video1.service" ]]; then
    systemctl disable --now rcpilot-video1.service 2>/dev/null || true
    rm -f "${SYSTEMD_DIR}/rcpilot-video1.service"
    echo "removed legacy /etc/systemd/system/rcpilot-video1.service"
fi

systemctl daemon-reload
systemctl enable --now rcpilot-echo.service rcpilot-video.service

echo
echo "Services installed and started. Check status with:"
echo "  systemctl status rcpilot-echo"
echo "  systemctl status rcpilot-video    # dual-cam stitched on UDP 5004"
