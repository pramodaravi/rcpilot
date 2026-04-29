using System;

namespace RcPilot.Core
{
    /// <summary>
    /// Little-endian helpers matching Python's struct module exactly.
    /// The Jetson and the existing Windows driver station both use little-endian
    /// packed structs; we stay on that wire format so Unity can drop in as a
    /// one-for-one replacement for windows/driver_station.py.
    /// </summary>
    public static class ByteOps
    {
        public static void WriteU32LE(byte[] b, int off, uint v)
        {
            b[off + 0] = (byte)(v);
            b[off + 1] = (byte)(v >> 8);
            b[off + 2] = (byte)(v >> 16);
            b[off + 3] = (byte)(v >> 24);
        }

        public static void WriteI16LE(byte[] b, int off, short v)
        {
            ushort u = (ushort)v;
            b[off + 0] = (byte)(u);
            b[off + 1] = (byte)(u >> 8);
        }

        public static void WriteU16LE(byte[] b, int off, ushort v)
        {
            b[off + 0] = (byte)(v);
            b[off + 1] = (byte)(v >> 8);
        }

        public static void WriteU64LE(byte[] b, int off, ulong v)
        {
            for (int i = 0; i < 8; i++) b[off + i] = (byte)(v >> (8 * i));
        }

        public static uint ReadU32LE(byte[] b, int off) =>
            (uint)(b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24));

        public static ulong ReadU64LE(byte[] b, int off)
        {
            ulong v = 0;
            for (int i = 0; i < 8; i++) v |= ((ulong)b[off + i]) << (8 * i);
            return v;
        }

        public static short ReadI16LE(byte[] b, int off) =>
            (short)(b[off] | (b[off + 1] << 8));

        public static ushort ReadU16LE(byte[] b, int off) =>
            (ushort)(b[off] | (b[off + 1] << 8));

        // ---- float32 ---------------------------------------------------------
        //
        // Used by the v0.2 control packet (steering / throttle / brake / clutch).
        // BitConverter is endian-dependent on the host; on every desktop and ARM
        // platform Unity targets, IsLittleEndian == true, so the fast path is
        // a direct copy. The branch handles theoretical big-endian hosts.

        public static void WriteF32LE(byte[] b, int off, float v)
        {
            var src = BitConverter.GetBytes(v);
            if (BitConverter.IsLittleEndian)
            {
                b[off + 0] = src[0]; b[off + 1] = src[1];
                b[off + 2] = src[2]; b[off + 3] = src[3];
            }
            else
            {
                b[off + 0] = src[3]; b[off + 1] = src[2];
                b[off + 2] = src[1]; b[off + 3] = src[0];
            }
        }

        public static float ReadF32LE(byte[] b, int off)
        {
            if (BitConverter.IsLittleEndian)
            {
                return BitConverter.ToSingle(b, off);
            }
            var swapped = new byte[4]
            {
                b[off + 3], b[off + 2], b[off + 1], b[off + 0],
            };
            return BitConverter.ToSingle(swapped, 0);
        }
    }
}
