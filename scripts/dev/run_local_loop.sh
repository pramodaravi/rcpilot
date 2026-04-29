#!/usr/bin/env bash
#
# Spin up an echo server and a fake control sender on localhost so you can
# poke at the loop without any hardware. Useful for verifying changes to
# protocol.py / config.py without a Jetson on the desk.
#
# Requires: pip install -e .[dev] (gives you pytest etc. — pygame not
# required because this script doesn't open a real joystick).

set -euo pipefail

PORT="${PORT:-5005}"
DURATION="${DURATION:-5}"

echo "[loop] starting echo server on 127.0.0.1:${PORT} for ${DURATION}s"
rcpilot-echo-server --listen 127.0.0.1 --port "${PORT}" -v &
SERVER_PID=$!

cleanup() {
    if kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

sleep 0.3

echo "[loop] sending fake packets for ${DURATION}s"
python - "${PORT}" "${DURATION}" <<'PY'
import socket, struct, sys, time, zlib

port = int(sys.argv[1])
duration = float(sys.argv[2])
end = time.perf_counter() + duration

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.1)

seq = 0
received = 0
sent = 0

while time.perf_counter() < end:
    payload = struct.pack("<IQffff", seq, int(time.time() * 1e6), 0.0, 0.0, 0.0, 0.0)
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    pkt = payload + struct.pack("<I", crc)
    sock.sendto(pkt, ("127.0.0.1", port))
    sent += 1
    try:
        sock.recvfrom(64)
        received += 1
    except socket.timeout:
        pass
    seq += 1
    time.sleep(0.004)

print(f"[loop] sent={sent} received={received} ({100 * received / max(sent, 1):.1f}% round-trip)")
PY

echo "[loop] done"
