using System;
using System.Net;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// Wire protocol v0.2. Byte-identical to <c>rcpilot/src/rcpilot/protocol.py</c>
    /// in the Python repo at github.com/pramodaravi/rcpilot.
    ///
    /// Two packet types travel between the cockpit and the car:
    ///
    /// <para>
    /// <b>Control (cockpit → car), 32 bytes, little-endian:</b>
    /// <code>
    ///   offset  type     field
    ///   0       uint32   seq            sequence number, increments per packet
    ///   4       uint64   ts_us          cockpit microsecond timestamp
    ///   12      float32  steering       -1.0 (full left) .. +1.0 (full right)
    ///   16      float32  throttle       0.0 (released) .. 1.0 (full)
    ///   20      float32  brake          0.0 .. 1.0
    ///   24      float32  clutch         0.0 .. 1.0
    ///   28      uint32   crc32          CRC32 of the preceding 28 bytes
    /// </code>
    /// </para>
    ///
    /// <para>
    /// <b>Echo (car → cockpit), 16 bytes, little-endian:</b>
    /// <code>
    ///   offset  type     field
    ///   0       uint32   seq            echoed sequence number
    ///   4       uint64   ts_us          echoed cockpit timestamp
    ///   12      uint32   proc_us        car-side processing time, microseconds
    /// </code>
    /// </para>
    ///
    /// RTT is computed locally on the cockpit side from a per-seq table of send
    /// times and the corresponding echo arrival.
    ///
    /// Why these specific choices:
    /// <list type="bullet">
    ///   <item><description>seq is uint32 — gives ~497 days at 100 Hz before wrap.</description></item>
    ///   <item><description>ts_us is uint64 — large enough for raw <c>time.time() * 1e6</c>.</description></item>
    ///   <item><description>Axis values are float32 — exact range matches HID joystick APIs.</description></item>
    ///   <item><description>CRC32 covers the 28-byte payload — catches single-bit corruption and most bursts.</description></item>
    ///   <item><description>Little-endian throughout — matches both Linux/ARM and Windows/x86.</description></item>
    /// </list>
    ///
    /// Buttons / state machine codes from v0.1 are <i>not</i> in v0.2's wire format.
    /// V1 uses watchdog-based estop (car coasts after watchdog_ms with no packet).
    /// If we need explicit button channels later, that's a wire-format extension.
    /// </summary>
    public static class Protocol
    {
        public const int ControlSize = 32;
        public const int EchoSize = 16;

        /// <summary>
        /// Cockpit-side timestamp source. Must be monotonic and microsecond-granular.
        /// Receivers diff timestamps; absolute epoch alignment is not required.
        /// </summary>
        public static ulong NowUs()
        {
            return (ulong)(Time.realtimeSinceStartupAsDouble * 1e6);
        }
    }


    /// <summary>
    /// A single cockpit-to-car control message.
    /// Use <see cref="Pack"/> to serialize for the wire and <see cref="TryUnpack"/>
    /// to deserialize incoming bytes. CRC32 is appended automatically on Pack and
    /// validated on Unpack — a packet that fails CRC unpacks as null.
    /// </summary>
    public struct ControlPacket
    {
        public uint  seq;
        public ulong tsUs;
        public float steering;   // -1.0 .. +1.0
        public float throttle;   // 0.0 .. 1.0
        public float brake;      // 0.0 .. 1.0
        public float clutch;     // 0.0 .. 1.0 (reserved for sequential-gearbox cars; usually 0)

        public byte[] Pack()
        {
            var b = new byte[Protocol.ControlSize];
            ByteOps.WriteU32LE(b, 0, seq);
            ByteOps.WriteU64LE(b, 4, tsUs);
            ByteOps.WriteF32LE(b, 12, steering);
            ByteOps.WriteF32LE(b, 16, throttle);
            ByteOps.WriteF32LE(b, 20, brake);
            ByteOps.WriteF32LE(b, 24, clutch);
            uint crc = Crc32.Compute(b, 0, 28);
            ByteOps.WriteU32LE(b, 28, crc);
            return b;
        }

        public static ControlPacket? TryUnpack(byte[] data)
        {
            if (data == null || data.Length != Protocol.ControlSize) return null;
            uint expected = Crc32.Compute(data, 0, 28);
            uint crc = ByteOps.ReadU32LE(data, 28);
            if (crc != expected) return null;
            return new ControlPacket
            {
                seq      = ByteOps.ReadU32LE(data, 0),
                tsUs     = ByteOps.ReadU64LE(data, 4),
                steering = ByteOps.ReadF32LE(data, 12),
                throttle = ByteOps.ReadF32LE(data, 16),
                brake    = ByteOps.ReadF32LE(data, 20),
                clutch   = ByteOps.ReadF32LE(data, 24),
            };
        }

        /// <summary>
        /// Return a copy with axes clamped to their canonical ranges. Useful as a
        /// final safety pass before dispatching to a motor driver.
        /// </summary>
        public ControlPacket Clamped()
        {
            return new ControlPacket
            {
                seq = seq,
                tsUs = tsUs,
                steering = Mathf.Clamp(steering, -1f, 1f),
                throttle = Mathf.Clamp01(throttle),
                brake    = Mathf.Clamp01(brake),
                clutch   = Mathf.Clamp01(clutch),
            };
        }
    }


    /// <summary>
    /// A car-to-cockpit echo reply, used for round-trip latency measurement.
    /// </summary>
    public struct EchoPacket
    {
        public uint  seq;
        public ulong tsUs;
        public uint  procUs;

        public byte[] Pack()
        {
            var b = new byte[Protocol.EchoSize];
            ByteOps.WriteU32LE(b, 0, seq);
            ByteOps.WriteU64LE(b, 4, tsUs);
            ByteOps.WriteU32LE(b, 12, procUs);
            return b;
        }

        public static EchoPacket? TryUnpack(byte[] data)
        {
            if (data == null || data.Length != Protocol.EchoSize) return null;
            return new EchoPacket
            {
                seq    = ByteOps.ReadU32LE(data, 0),
                tsUs   = ByteOps.ReadU64LE(data, 4),
                procUs = ByteOps.ReadU32LE(data, 12),
            };
        }
    }


    /// <summary>
    /// CRC32 (IEEE 802.3 polynomial, reflected) — matches Python's <c>zlib.crc32()</c>
    /// output bit-for-bit. The Python <c>rcpilot</c> package uses zlib; this class
    /// must agree with it byte-for-byte or every packet will be dropped.
    /// </summary>
    public static class Crc32
    {
        private static readonly uint[] _table = MakeTable();

        private static uint[] MakeTable()
        {
            var t = new uint[256];
            const uint poly = 0xEDB88320u; // reflected IEEE 802.3
            for (uint i = 0; i < 256; i++)
            {
                uint c = i;
                for (int k = 0; k < 8; k++)
                {
                    c = (c & 1u) != 0u ? (poly ^ (c >> 1)) : (c >> 1);
                }
                t[i] = c;
            }
            return t;
        }

        public static uint Compute(byte[] data, int offset, int count)
        {
            uint crc = 0xFFFFFFFFu;
            for (int i = 0; i < count; i++)
            {
                crc = _table[(crc ^ data[offset + i]) & 0xFFu] ^ (crc >> 8);
            }
            return crc ^ 0xFFFFFFFFu;
        }
    }
}
