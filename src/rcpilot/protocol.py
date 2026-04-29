"""Wire protocol for cockpit ↔ car communication.

Two packet types travel between the sim cockpit and the car:

    Control (cockpit → car), 32 bytes, little-endian:

        offset  type    field
        0       uint32  seq            sequence number, increments per packet
        4       uint64  ts_us          cockpit microsecond timestamp
        12      float32 steering       -1.0 (full left) .. +1.0 (full right)
        16      float32 throttle       0.0 (released) .. 1.0 (full)
        20      float32 brake          0.0 .. 1.0
        24      float32 clutch         0.0 .. 1.0
        28      uint32  crc32          CRC32 of the preceding 28 bytes

    Echo (car → cockpit), 16 bytes, little-endian:

        offset  type    field
        0       uint32  seq            echoed sequence number
        4       uint64  ts_us          echoed cockpit timestamp
        12      uint32  proc_us        car-side processing time, microseconds

The cockpit uses ``proc_us`` as a sanity check on the car's responsiveness, but
RTT is computed locally on the cockpit side from a per-seq table of send times.

Why these specific choices, briefly:

    *  ``seq`` is uint32 — gives ~497 days at 100 Hz before wrap, far more than
       any session length.
    *  ``ts_us`` is uint64 — large enough for raw ``time.time() * 1e6`` values
       without bit-twiddling.
    *  Axis values are float32 — exact range matches what game-controller HID
       APIs emit; no scaling or quantization needed.
    *  CRC32 covers the 28-byte payload — catches single-bit corruption and
       most bursts. We do not authenticate; the link is on a private network.
    *  Little-endian throughout — matches both Linux/ARM and Windows/x86
       hosts; no byte-swap needed on either side.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

# ---- Wire format ----------------------------------------------------------

#: Format string for the *signed* portion of the control packet (28 bytes).
_CONTROL_PAYLOAD_FMT = "<IQffff"

#: Format string for the full control packet, including trailing CRC32 (32 bytes).
CONTROL_FMT = "<IQffffI"

#: Total control packet size on the wire.
CONTROL_SIZE = struct.calcsize(CONTROL_FMT)
assert CONTROL_SIZE == 32, "Control packet size drift; check struct format"

#: Format string for the echo reply.
ECHO_FMT = "<IQI"

#: Total echo packet size.
ECHO_SIZE = struct.calcsize(ECHO_FMT)
assert ECHO_SIZE == 16, "Echo packet size drift; check struct format"


# ---- Control packet -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlPacket:
    """A single cockpit-to-car control message.

    Use :meth:`pack` to serialize to bytes for sending, and
    :meth:`unpack` to deserialize incoming bytes (returns ``None`` if the
    packet is malformed or fails CRC).
    """

    seq: int
    ts_us: int
    steering: float
    throttle: float
    brake: float
    clutch: float

    def pack(self) -> bytes:
        """Serialize to a 32-byte wire packet (with CRC32 appended)."""
        payload = struct.pack(
            _CONTROL_PAYLOAD_FMT,
            self.seq,
            self.ts_us,
            self.steering,
            self.throttle,
            self.brake,
            self.clutch,
        )
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return payload + struct.pack("<I", crc)

    @classmethod
    def unpack(cls, data: bytes) -> ControlPacket | None:
        """Deserialize a 32-byte wire packet. Returns ``None`` on any failure.

        Failure modes detected:
            * Wrong size (not exactly 32 bytes)
            * CRC32 mismatch
            * struct.error on parse (wrong endian / wrong format)

        Callers can therefore treat ``None`` as "drop this packet".
        """
        if len(data) != CONTROL_SIZE:
            return None
        try:
            seq, ts_us, st, th, br, cl, crc = struct.unpack(CONTROL_FMT, data)
        except struct.error:
            return None
        expected = zlib.crc32(data[:-4]) & 0xFFFFFFFF
        if crc != expected:
            return None
        return cls(seq=seq, ts_us=ts_us, steering=st, throttle=th, brake=br, clutch=cl)

    def with_clamped_axes(self) -> ControlPacket:
        """Return a copy with axes clamped to their canonical ranges.

        Steering is clamped to ``[-1.0, +1.0]``; throttle / brake / clutch are
        clamped to ``[0.0, 1.0]``. Useful as a final safety pass before
        forwarding to a motor driver.
        """
        return ControlPacket(
            seq=self.seq,
            ts_us=self.ts_us,
            steering=_clamp(self.steering, -1.0, 1.0),
            throttle=_clamp(self.throttle, 0.0, 1.0),
            brake=_clamp(self.brake, 0.0, 1.0),
            clutch=_clamp(self.clutch, 0.0, 1.0),
        )


# ---- Echo packet ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EchoPacket:
    """A car-to-cockpit echo reply, used for round-trip latency measurement."""

    seq: int
    ts_us: int
    proc_us: int

    def pack(self) -> bytes:
        return struct.pack(ECHO_FMT, self.seq, self.ts_us, self.proc_us)

    @classmethod
    def unpack(cls, data: bytes) -> EchoPacket | None:
        if len(data) != ECHO_SIZE:
            return None
        try:
            seq, ts_us, proc_us = struct.unpack(ECHO_FMT, data)
        except struct.error:
            return None
        return cls(seq=seq, ts_us=ts_us, proc_us=proc_us)


# ---- Helpers --------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


__all__ = [
    "CONTROL_FMT",
    "CONTROL_SIZE",
    "ECHO_FMT",
    "ECHO_SIZE",
    "ControlPacket",
    "EchoPacket",
]
