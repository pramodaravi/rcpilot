"""Tests for rcpilot.protocol — wire format, CRC, malformed inputs."""

from __future__ import annotations

import struct
import zlib

import pytest

from rcpilot.protocol import (
    CONTROL_FMT,
    CONTROL_SIZE,
    ECHO_FMT,
    ECHO_SIZE,
    ControlPacket,
    EchoPacket,
)

# ---- Sanity checks on the wire format constants --------------------------


def test_control_size_is_32() -> None:
    assert CONTROL_SIZE == 32
    assert struct.calcsize(CONTROL_FMT) == 32


def test_echo_size_is_16() -> None:
    assert ECHO_SIZE == 16
    assert struct.calcsize(ECHO_FMT) == 16


def test_format_is_little_endian() -> None:
    # Both formats must start with '<' so cross-platform alignment is fixed.
    assert CONTROL_FMT.startswith("<")
    assert ECHO_FMT.startswith("<")


# ---- ControlPacket round trips -------------------------------------------


@pytest.mark.parametrize(
    "packet",
    [
        # Use values that are exact in float32 (binary fractions) so the
        # equality assertion below isn't conflated with float32 rounding.
        ControlPacket(seq=0, ts_us=0, steering=0.0, throttle=0.0, brake=0.0, clutch=0.0),
        ControlPacket(seq=42, ts_us=1234567890, steering=0.5, throttle=0.25, brake=0.75, clutch=0.0),
        ControlPacket(seq=4_294_967_295, ts_us=2**63, steering=-1.0, throttle=1.0, brake=1.0, clutch=1.0),
        ControlPacket(seq=12345, ts_us=42, steering=-0.125, throttle=0.875, brake=0.5, clutch=0.0625),
    ],
)
def test_control_roundtrip(packet: ControlPacket) -> None:
    decoded = ControlPacket.unpack(packet.pack())
    assert decoded == packet


def test_control_roundtrip_with_float32_rounding() -> None:
    """Round-trip with values that DON'T have exact float32 representations.

    The wire format uses float32 by design (matches HID controller resolution
    and saves bytes). So a packet with `0.123` will come back as `0.12300...4`
    or thereabouts. We assert *approximate* equality here to verify the
    serialization is faithful within float32 precision.
    """
    p = ControlPacket(
        seq=99, ts_us=1, steering=-0.123, throttle=0.456, brake=0.789, clutch=0.999
    )
    decoded = ControlPacket.unpack(p.pack())
    assert decoded is not None
    assert decoded.seq == p.seq
    assert decoded.ts_us == p.ts_us
    # float32 has ~7 decimal digits of precision; 1e-6 is comfortably above the
    # quantization noise but below anything a wheel/pedal HID would emit.
    assert decoded.steering == pytest.approx(p.steering, abs=1e-6)
    assert decoded.throttle == pytest.approx(p.throttle, abs=1e-6)
    assert decoded.brake == pytest.approx(p.brake, abs=1e-6)
    assert decoded.clutch == pytest.approx(p.clutch, abs=1e-6)


def test_control_pack_size() -> None:
    p = ControlPacket(seq=1, ts_us=1, steering=0.0, throttle=0.0, brake=0.0, clutch=0.0)
    assert len(p.pack()) == CONTROL_SIZE


# ---- ControlPacket failure modes -----------------------------------------


def test_control_unpack_wrong_size_returns_none() -> None:
    assert ControlPacket.unpack(b"") is None
    assert ControlPacket.unpack(b"x" * 31) is None
    assert ControlPacket.unpack(b"x" * 33) is None
    assert ControlPacket.unpack(b"x" * 64) is None


def test_control_unpack_corrupt_payload_fails_crc() -> None:
    p = ControlPacket(seq=1, ts_us=2, steering=0.5, throttle=0.5, brake=0.5, clutch=0.5)
    raw = bytearray(p.pack())
    # Flip a bit in the steering field. CRC trailer is unchanged → mismatch.
    raw[12] ^= 0xFF
    assert ControlPacket.unpack(bytes(raw)) is None


def test_control_unpack_truncated_crc_returns_none() -> None:
    p = ControlPacket(seq=1, ts_us=2, steering=0.5, throttle=0.5, brake=0.5, clutch=0.5)
    raw = p.pack()[:-1]  # 31 bytes — wrong size
    assert ControlPacket.unpack(raw) is None


def test_control_unpack_corrupt_crc_only_returns_none() -> None:
    p = ControlPacket(seq=1, ts_us=2, steering=0.0, throttle=0.0, brake=0.0, clutch=0.0)
    raw = bytearray(p.pack())
    # Flip the last byte of CRC. Payload is intact but CRC differs.
    raw[-1] ^= 0xFF
    assert ControlPacket.unpack(bytes(raw)) is None


def test_control_crc_is_recomputed() -> None:
    # Sanity: pack must always emit a CRC matching the payload, no caching.
    p = ControlPacket(seq=99, ts_us=42, steering=0.1, throttle=0.2, brake=0.3, clutch=0.4)
    data = p.pack()
    payload, crc_bytes = data[:-4], data[-4:]
    expected = zlib.crc32(payload) & 0xFFFFFFFF
    actual = struct.unpack("<I", crc_bytes)[0]
    assert actual == expected


# ---- ControlPacket clamping ----------------------------------------------


def test_with_clamped_axes_clamps_out_of_range() -> None:
    p = ControlPacket(seq=1, ts_us=2, steering=2.0, throttle=-0.5, brake=1.5, clutch=-0.1)
    clamped = p.with_clamped_axes()
    assert clamped.steering == 1.0
    assert clamped.throttle == 0.0
    assert clamped.brake == 1.0
    assert clamped.clutch == 0.0


def test_with_clamped_axes_passes_through_in_range() -> None:
    p = ControlPacket(seq=1, ts_us=2, steering=-0.5, throttle=0.5, brake=0.0, clutch=1.0)
    clamped = p.with_clamped_axes()
    assert clamped == p


# ---- EchoPacket -----------------------------------------------------------


@pytest.mark.parametrize(
    "packet",
    [
        EchoPacket(seq=0, ts_us=0, proc_us=0),
        EchoPacket(seq=42, ts_us=12345, proc_us=100),
        EchoPacket(seq=4_294_967_295, ts_us=2**63, proc_us=4_294_967_295),
    ],
)
def test_echo_roundtrip(packet: EchoPacket) -> None:
    decoded = EchoPacket.unpack(packet.pack())
    assert decoded == packet


def test_echo_unpack_wrong_size_returns_none() -> None:
    assert EchoPacket.unpack(b"x" * 15) is None
    assert EchoPacket.unpack(b"x" * 17) is None
    assert EchoPacket.unpack(b"") is None
