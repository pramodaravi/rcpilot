"""Cockpit-side control sender: read joystick → send UDP to car → measure RTT.

Replaces the inline scripts that lived under ``bench-bringup/cockpit/``.
This version uses the :mod:`rcpilot.protocol` packet types, the
:mod:`rcpilot.config` loader, and the :mod:`rcpilot.cockpit.joystick`
abstraction — so changing axis mapping, IPs, or rates is now a YAML edit
rather than a code edit.

Usage::

    rcpilot-control-sender                              # all defaults
    rcpilot-control-sender --jetson 10.0.0.42           # one-off IP override
    rcpilot-control-sender --rate-hz 125                # slower send rate
    rcpilot-control-sender --config path/to/local.yaml  # explicit config

Press Ctrl-C to stop. Stats line prints once per second:

    #seq    steer=±0.000 thr=0.000 brk=0.000  RTT(ms) mean=4.6 p50=4.5 p95=5.2

RTT is computed locally — we record ``perf_counter()`` per outbound seq, then
diff against the time we receive the matching echo. The Jetson's ``proc_us``
field is logged only at DEBUG level since it's mostly a sanity check.
"""

from __future__ import annotations

import argparse
import collections
import logging
import signal
import socket
import sys
import threading
import time

from rcpilot import config as config_module
from rcpilot.cockpit.joystick import JoystickAdapter
from rcpilot.protocol import ECHO_SIZE, ControlPacket, EchoPacket

log = logging.getLogger("rcpilot.control_sender")


# DSCP Expedited Forwarding (RFC 3246) — marks the IP header so QoS-aware
# switches and APs prioritize control packets. Equivalent to TOS byte 0xb8.
_DSCP_EF = 0xB8


class _RTTHistogram:
    """Sliding-window RTT samples with mean / p50 / p95 / p99 readouts."""

    def __init__(self, capacity: int = 2000) -> None:
        self._samples: collections.deque[float] = collections.deque(maxlen=capacity)

    def add(self, rtt_ms: float) -> None:
        self._samples.append(rtt_ms)

    def __len__(self) -> int:
        return len(self._samples)

    def summary(self) -> dict[str, float]:
        if not self._samples:
            return {"mean": float("nan"), "p50": float("nan"), "p95": float("nan"), "p99": float("nan")}
        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)
        mean = sum(sorted_samples) / n
        p50 = sorted_samples[n // 2]
        p95 = sorted_samples[min(n - 1, int(n * 0.95))]
        p99 = sorted_samples[min(n - 1, int(n * 0.99))]
        return {"mean": mean, "p50": p50, "p95": p95, "p99": p99}


class ControlSender:
    """Pumps the joystick and sends control packets at a fixed rate.

    All long-running state lives on the instance so it's easier to test
    pieces in isolation. Run with :meth:`run`.
    """

    def __init__(
        self,
        joystick: JoystickAdapter,
        target: tuple[str, int],
        rate_hz: int,
    ) -> None:
        self._joystick = joystick
        self._target = target
        self._rate_hz = rate_hz
        self._period = 1.0 / rate_hz

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Mark control packets as expedited so QoS-aware networks prioritize them.
        try:
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, _DSCP_EF)
        except OSError:  # pragma: no cover — non-fatal, some platforms reject.
            log.debug("setsockopt(IP_TOS) failed; continuing without DSCP")
        self._sock.setblocking(False)

        self._stop = threading.Event()
        self._seq = 0
        self._in_flight: dict[int, float] = {}
        self._in_flight_keys: collections.deque[int] = collections.deque(maxlen=4096)
        self._rtt = _RTTHistogram()

    def stop(self) -> None:
        """Signal a clean shutdown. Safe to call from a signal handler."""
        self._stop.set()

    def run(self) -> None:
        log.info(
            "Sending to %s:%d at %d Hz (joystick: %s, %d axes)",
            self._target[0],
            self._target[1],
            self._rate_hz,
            self._joystick.device_name,
            self._joystick.num_axes,
        )

        next_send = time.perf_counter()
        last_print = next_send

        try:
            while not self._stop.is_set():
                self._send_one()
                self._drain_echoes()

                now = time.perf_counter()
                if now - last_print >= 1.0:
                    self._log_status()
                    last_print = now

                # Pace the loop with absolute scheduling; if we fall >50 ms
                # behind (long GC, system hiccup), reset rather than burst.
                next_send += self._period
                sleep_for = next_send - time.perf_counter()
                if sleep_for > 0:
                    self._stop.wait(timeout=sleep_for)
                elif sleep_for < -0.05:
                    next_send = time.perf_counter()
        finally:
            self._sock.close()

    def _send_one(self) -> None:
        reading = self._joystick.read()
        ts_us = int(time.time() * 1e6)
        packet = ControlPacket(
            seq=self._seq,
            ts_us=ts_us,
            steering=reading.steering,
            throttle=reading.throttle,
            brake=reading.brake,
            clutch=reading.clutch,
        ).pack()

        try:
            self._sock.sendto(packet, self._target)
            self._in_flight[self._seq] = time.perf_counter()
            # Bound the in-flight table so memory stays flat even if echoes
            # never come back. The deque does the eviction in O(1).
            if len(self._in_flight_keys) == self._in_flight_keys.maxlen:
                evicted = self._in_flight_keys[0]
                self._in_flight.pop(evicted, None)
            self._in_flight_keys.append(self._seq)
        except BlockingIOError:
            # Send buffer is full — UDP semantics, just drop the packet.
            pass
        except OSError as exc:
            log.warning("sendto failed: %s", exc)

        self._seq += 1

    def _drain_echoes(self) -> None:
        while True:
            try:
                data, _ = self._sock.recvfrom(64)
            except BlockingIOError:
                return
            if len(data) != ECHO_SIZE:
                continue
            echo = EchoPacket.unpack(data)
            if echo is None:
                continue
            send_t = self._in_flight.pop(echo.seq, None)
            if send_t is None:
                continue
            rtt_ms = (time.perf_counter() - send_t) * 1000.0
            self._rtt.add(rtt_ms)
            log.debug("seq=%d rtt=%.2f ms proc=%d us", echo.seq, rtt_ms, echo.proc_us)

    def _log_status(self) -> None:
        reading = self._joystick.read()
        if len(self._rtt) > 0:
            stats = self._rtt.summary()
            log.info(
                "#%6d  steer=%+.3f thr=%.3f brk=%.3f  "
                "RTT(ms) mean=%.1f p50=%.1f p95=%.1f p99=%.1f  in_flight=%d",
                self._seq,
                reading.steering,
                reading.throttle,
                reading.brake,
                stats["mean"],
                stats["p50"],
                stats["p95"],
                stats["p99"],
                len(self._in_flight),
            )
        else:
            log.info(
                "#%6d  steer=%+.3f thr=%.3f brk=%.3f  no echoes yet (in_flight=%d)",
                self._seq,
                reading.steering,
                reading.throttle,
                reading.brake,
                len(self._in_flight),
            )


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rcpilot-control-sender",
        description="Read joystick / wheel and stream control packets to the car.",
    )
    ap.add_argument("--config", default=None, help="YAML config file (overrides default lookup).")
    ap.add_argument("--jetson", default=None, help="Override config network.jetson_ip")
    ap.add_argument("--port", type=int, default=None, help="Override config network.control_port")
    ap.add_argument("--rate-hz", type=int, default=None, help="Override config control.rate_hz")
    ap.add_argument("--joy", type=int, default=None, help="Override config control.joystick_index")
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
    _configure_logging(max(1, args.verbose))

    cfg = config_module.load(args.config)
    target = (
        args.jetson if args.jetson is not None else cfg.network.jetson_ip,
        args.port if args.port is not None else cfg.network.control_port,
    )
    rate_hz = args.rate_hz if args.rate_hz is not None else cfg.control.rate_hz
    joystick_index = args.joy if args.joy is not None else cfg.control.joystick_index

    joystick = JoystickAdapter(joystick_index=joystick_index, axes=cfg.control.axes)
    sender = ControlSender(joystick=joystick, target=target, rate_hz=rate_hz)

    def _handle_sig(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        sender.stop()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    try:
        sender.run()
    except KeyboardInterrupt:
        pass
    finally:
        joystick.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
