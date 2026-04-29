"""Jetson-side UDP echo server.

Listens for control packets from the cockpit, validates them (size + CRC32),
echoes back a small reply so the cockpit can compute round-trip latency, and
keeps a snapshot of the most recent valid input that downstream motor-driver
code can read.

A watchdog fires if no valid packet arrives within ``watchdog_ms``; in
production the failsafe ESP32-S3 will trigger off this signal to brake the
car. For now we just log and (in this Python-only stack) freeze the
last-known-good snapshot.

Run via the console-script entry point::

    rcpilot-echo-server

Or directly::

    python -m rcpilot.jetson.echo_server [--config path/to/config.yaml] [--port 5005]

Press Ctrl-C to stop. SIGTERM is also handled (clean shutdown for systemd).
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass

from rcpilot import __version__
from rcpilot import config as config_module
from rcpilot.protocol import CONTROL_SIZE, ControlPacket, EchoPacket

log = logging.getLogger("rcpilot.echo")


@dataclass
class _ServerStats:
    received: int = 0
    losses: int = 0
    bad_size: int = 0
    bad_crc: int = 0
    last_seq: int = -1


class EchoServer:
    """The UDP listener / echoer / watchdog. Single-threaded by design.

    Keeping it single-threaded means the most recent control packet is
    visible to downstream consumers (a future motor driver) by reading
    ``self.snapshot`` directly — no lock needed because Python GIL plus
    dataclass-frozen-True make the read atomic at the field level. If the
    motor driver lives in another thread later, we'll add an RLock around
    snapshot updates.
    """

    def __init__(
        self,
        listen_addr: str,
        port: int,
        watchdog_ms: int,
    ) -> None:
        self.listen_addr = listen_addr
        self.port = port
        self.watchdog_ms = watchdog_ms

        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._stats = _ServerStats()
        self._snapshot: ControlPacket | None = None
        self._snapshot_age_perf: float = 0.0

    @property
    def snapshot(self) -> ControlPacket | None:
        """Most recent *valid* control packet, or None if we've never received one."""
        return self._snapshot

    @property
    def is_fresh(self) -> bool:
        """True iff a packet has arrived within the watchdog window."""
        if self._snapshot is None:
            return False
        return (time.perf_counter() - self._snapshot_age_perf) * 1000 < self.watchdog_ms

    def stop(self) -> None:
        """Trigger a graceful shutdown. Safe to call from a signal handler."""
        self._stop.set()
        # Nudge recvfrom so it returns immediately.
        if self._sock is not None:
            try:
                self._sock.sendto(b"", ("127.0.0.1", self.port))
            except OSError:
                pass

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_addr, self.port))
        sock.settimeout(self.watchdog_ms / 1000.0)
        self._sock = sock

        log.info(
            "rcpilot.echo v%s listening on %s:%d, watchdog=%d ms",
            __version__,
            self.listen_addr,
            self.port,
            self.watchdog_ms,
        )

        last_print = time.perf_counter()
        last_packet_perf = time.perf_counter()
        watchdog_warned = False

        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(64)
                except TimeoutError:
                    if not watchdog_warned and self._snapshot is not None:
                        gap_ms = (time.perf_counter() - last_packet_perf) * 1000
                        if gap_ms >= self.watchdog_ms:
                            log.warning(
                                "WATCHDOG: no valid packet for %.0f ms — "
                                "production failsafe would brake here",
                                gap_ms,
                            )
                            watchdog_warned = True
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise

                recv_us = int(time.time() * 1e6)
                last_packet_perf = time.perf_counter()
                watchdog_warned = False

                if len(data) != CONTROL_SIZE:
                    self._stats.bad_size += 1
                    continue

                packet = ControlPacket.unpack(data)
                if packet is None:
                    self._stats.bad_crc += 1
                    log.debug("CRC fail or unpack error from %s", addr)
                    continue

                # Track packet loss via sequence numbers.
                if self._stats.last_seq >= 0:
                    gap = packet.seq - self._stats.last_seq
                    if gap > 1:
                        self._stats.losses += gap - 1
                self._stats.last_seq = packet.seq
                self._stats.received += 1

                self._snapshot = packet
                self._snapshot_age_perf = last_packet_perf

                # Echo: include processing time so the cockpit can debug
                # whether RTT spikes are network-side or car-side.
                proc_us = max(0, int(time.time() * 1e6) - recv_us)
                reply = EchoPacket(seq=packet.seq, ts_us=packet.ts_us, proc_us=proc_us).pack()
                try:
                    sock.sendto(reply, addr)
                except OSError as exc:
                    log.warning("echo send failed: %s", exc)

                # Periodic stats line.
                now = time.perf_counter()
                if now - last_print >= 1.0:
                    self._log_stats(packet)
                    last_print = now
        finally:
            sock.close()
            self._sock = None
            log.info("rcpilot.echo stopped")

    def _log_stats(self, last: ControlPacket) -> None:
        s = self._stats
        denom = max(1, s.received + s.losses)
        loss_pct = (s.losses / denom) * 100.0
        log.info(
            "rcvd=%6d  losses=%5d (%.2f%%)  bad_size=%d  bad_crc=%d  "
            "steer=%+.3f  thr=%.3f  brk=%.3f  clu=%.3f",
            s.received,
            s.losses,
            loss_pct,
            s.bad_size,
            s.bad_crc,
            last.steering,
            last.throttle,
            last.brake,
            last.clutch,
        )


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rcpilot-echo-server",
        description="UDP echo server for cockpit-to-car control packets.",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="YAML config file. Defaults to env RCPILOT_CONFIG, then config/local.yaml, "
        "then config/default.yaml.",
    )
    ap.add_argument("--listen", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=None, help="Override config control_port")
    ap.add_argument(
        "--watchdog-ms",
        type=int,
        default=None,
        help="Override config watchdog_ms",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v: INFO, -vv: DEBUG)",
    )
    return ap


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    _configure_logging(max(1, args.verbose))  # default to INFO so the listening line shows

    cfg = config_module.load(args.config)
    port = args.port if args.port is not None else cfg.network.control_port
    watchdog_ms = args.watchdog_ms if args.watchdog_ms is not None else cfg.control.watchdog_ms

    server = EchoServer(args.listen, port, watchdog_ms)

    def _handle_sig(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        server.stop()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
