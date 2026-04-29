"""Integration test: run the echo server on localhost, send packets at it,
verify the round trip works without involving any joystick hardware.

This is the closest thing to "actually drive a packet end-to-end" we can
do in CI — no GStreamer, no real Jetson, no real joystick — but it
exercises the same packing, network code, and CRC validation that the
real bench loop uses.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from rcpilot.jetson.echo_server import EchoServer
from rcpilot.protocol import ECHO_SIZE, ControlPacket, EchoPacket


def _free_port() -> int:
    """Bind to port 0, find out what we got, release it. Race-y but fine for tests."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def echo_server() -> _RunningServer:
    port = _free_port()
    server = EchoServer(listen_addr="127.0.0.1", port=port, watchdog_ms=200)
    thread = threading.Thread(target=server.serve_forever, name="echo-test", daemon=True)
    thread.start()
    # Give the bind a moment to settle.
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("127.0.0.1", port))
            probe.close()
            break
        except OSError:  # pragma: no cover — only triggers on unusual hosts
            time.sleep(0.01)

    running = _RunningServer(server=server, thread=thread, port=port)
    yield running
    running.stop()


class _RunningServer:
    def __init__(self, server: EchoServer, thread: threading.Thread, port: int) -> None:
        self.server = server
        self.thread = thread
        self.port = port

    def stop(self) -> None:
        self.server.stop()
        self.thread.join(timeout=2.0)


def _send_and_recv(target_port: int, packet: bytes, timeout: float = 1.0) -> bytes | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.sendto(packet, ("127.0.0.1", target_port))
    try:
        data, _ = sock.recvfrom(64)
        return data
    except TimeoutError:
        return None
    finally:
        sock.close()


def test_packet_round_trip_returns_matching_echo(echo_server: _RunningServer) -> None:
    sent = ControlPacket(seq=42, ts_us=12345, steering=0.5, throttle=0.25, brake=0.0, clutch=0.0)
    reply = _send_and_recv(echo_server.port, sent.pack())

    assert reply is not None, "expected an echo within timeout"
    assert len(reply) == ECHO_SIZE
    echo = EchoPacket.unpack(reply)
    assert echo is not None
    assert echo.seq == sent.seq
    assert echo.ts_us == sent.ts_us
    # Server proc time should be small but non-negative.
    assert echo.proc_us >= 0


def test_corrupted_packet_is_dropped(echo_server: _RunningServer) -> None:
    # Build a valid packet, flip a payload byte (CRC will fail), expect no echo.
    sent = ControlPacket(seq=7, ts_us=99, steering=0.0, throttle=0.0, brake=0.0, clutch=0.0)
    raw = bytearray(sent.pack())
    raw[10] ^= 0xFF
    reply = _send_and_recv(echo_server.port, bytes(raw), timeout=0.3)
    assert reply is None, "echo server should have dropped corrupted packet"


def test_wrong_size_packet_is_dropped(echo_server: _RunningServer) -> None:
    reply = _send_and_recv(echo_server.port, b"this is not a valid packet", timeout=0.3)
    assert reply is None


def test_snapshot_reflects_last_valid_packet(echo_server: _RunningServer) -> None:
    sent = ControlPacket(seq=1, ts_us=1, steering=-0.7, throttle=0.3, brake=0.0, clutch=0.0)
    reply = _send_and_recv(echo_server.port, sent.pack())
    assert reply is not None  # sanity

    # Server may not have updated the snapshot yet; give it a beat.
    deadline = time.perf_counter() + 0.5
    while time.perf_counter() < deadline:
        snap = echo_server.server.snapshot
        if snap is not None and snap.seq == sent.seq:
            break
        time.sleep(0.01)

    snap = echo_server.server.snapshot
    assert snap is not None
    assert snap.seq == 1
    assert snap.steering == pytest.approx(-0.7, abs=1e-6)
    assert echo_server.server.is_fresh is True


def test_burst_round_trips_under_load(echo_server: _RunningServer) -> None:
    """Send 50 packets in tight succession; expect at least 80% to round-trip."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    received = 0
    n = 50
    expected_seqs: set[int] = set()
    try:
        for seq in range(n):
            packet = ControlPacket(
                seq=seq, ts_us=seq * 1000, steering=0.0, throttle=0.0, brake=0.0, clutch=0.0
            )
            sock.sendto(packet.pack(), ("127.0.0.1", echo_server.port))
            expected_seqs.add(seq)

        deadline = time.perf_counter() + 2.0
        seen: set[int] = set()
        while time.perf_counter() < deadline and len(seen) < n:
            try:
                data, _ = sock.recvfrom(64)
            except TimeoutError:
                break
            echo = EchoPacket.unpack(data)
            if echo is not None and echo.seq in expected_seqs:
                seen.add(echo.seq)
        received = len(seen)
    finally:
        sock.close()

    # UDP loopback is essentially lossless on a quiet machine, but we don't
    # demand 100% — kernel buffering + CI hosts can drop a packet here and
    # there. 80% gives us a robust signal that the loop works.
    assert received >= int(0.8 * n), f"only {received}/{n} round-tripped"
