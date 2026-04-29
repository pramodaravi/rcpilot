# rcpilot wire protocol

Version: **0.2** (incompatible with v0.1's 24-byte format).

All packets are **little-endian**. All multi-byte integers are unsigned unless noted. The implementation is `src/rcpilot/protocol.py`; the tests in `tests/test_protocol.py` are the executable reference.

## Control packet (cockpit → car)

32 bytes total. Sent every 1/`rate_hz` seconds from the cockpit, default 250 Hz.

| Offset | Type | Field | Meaning |
| --- | --- | --- | --- |
| 0 | uint32 | `seq` | Monotonic sequence number, increments per packet. Wraps at 2³². |
| 4 | uint64 | `ts_us` | Cockpit timestamp at send time, microseconds since Unix epoch. |
| 12 | float32 | `steering` | -1.0 (full left) ... +1.0 (full right). |
| 16 | float32 | `throttle` | 0.0 (released) ... 1.0 (fully pressed). |
| 20 | float32 | `brake` | 0.0 ... 1.0. |
| 24 | float32 | `clutch` | 0.0 ... 1.0. Reserved for sequential gearbox cars; usually 0. |
| 28 | uint32 | `crc32` | CRC32 of bytes 0-27 inclusive. |

`crc32` uses Python's `zlib.crc32` (IEEE 802.3 polynomial). Receivers MUST drop any packet whose CRC does not match the recomputed value.

### Validation rules

A receiver MUST drop the packet (no echo, no state update) if any of:

1. UDP datagram length is not exactly 32 bytes.
2. CRC32 of the first 28 bytes ≠ the trailing CRC.
3. Any field cannot be parsed as the declared type. (struct.unpack errors silently → drop.)

A receiver MAY drop or clamp packets where:

* `steering` is outside `[-1.0, +1.0]`. The car-side code is expected to clamp; senders should not rely on the car for safety.
* Any pedal value is outside `[0.0, 1.0]`. Same.

## Echo packet (car → cockpit)

16 bytes. Sent in response to every valid control packet. Used by the cockpit to compute round-trip latency and confirm the car heard the most recent input.

| Offset | Type | Field | Meaning |
| --- | --- | --- | --- |
| 0 | uint32 | `seq` | Echoed sequence number from the matching control packet. |
| 4 | uint64 | `ts_us` | Echoed cockpit timestamp. Used to identify which sample this corresponds to even if `seq` is ambiguous. |
| 12 | uint32 | `proc_us` | Car-side processing time in microseconds. Diagnostic only. |

### Loss handling

The protocol has no retransmit. Loss recovery relies on:

* Sequence-number-driven freshness on the car side: if `(now - last_packet_ts) > watchdog_ms`, the failsafe kicks in. Default `watchdog_ms` is 200 ms.
* Cockpit-side RTT histogram tracking: if RTT p95 climbs sharply or echoes stop, the cockpit operator (or an automated supervisor in production) knows the link is degrading before the car actually fails.

## Time synchronization

The protocol does not require synchronized clocks — RTT is computed locally on the cockpit by tracking outbound `seq → time.perf_counter()` and diffing against the receive time of the echo. `ts_us` and `proc_us` are diagnostic.

In production we expect **PTP (IEEE 1588)** or **NTP** to keep the cockpit and the ops console within ~1 ms, primarily for telemetry recording / replay. That's a deployment concern, not a protocol concern.

## Backwards compatibility

The 24-byte v0.1 format from `rc-pilot-code/` is **not interoperable** with this. The v0.2 echo format is also new. Any system speaking v0.1 must be upgraded before this server / sender can talk to it; we made the change consciously rather than versioning the wire because (a) v0.1 was never deployed and (b) the new fields and CRC are worth the clean break.

A `version` byte is intentionally **not** present in the packet. If we ever need to add one in the future, the natural extension point is a new format-specifier byte at offset 0 — but the cost would be re-doing every receiver, and we've decided to defer that until there's a real reason. For now, the IP+port pair *is* the version: anything on UDP/5005 must speak this exact format.

## Why not Protobuf / FlatBuffers / msgpack?

Considered and rejected, briefly:

* **Latency floor.** Fixed binary layout is the lowest cost — no string keys, no length prefixes, no schema lookup.
* **Determinism.** Every packet is exactly 32 bytes, every time. Easy to budget MTU and serialize at hard real-time on a microcontroller (the eventual ESP32-S3 failsafe path will speak the same format).
* **Visibility.** The wire format is human-readable in `tcpdump -X` if you ever need to debug a flaky link.

If the project later needs richer telemetry going the other way (IMU, ESC, battery, load cell), that's a separate channel with its own packet format — likely Protobuf over UDP with a small header — covered in `docs/architecture.md` § "What's coming".
