using System;

namespace RcPilot.Network
{
    /// <summary>
    /// V0.1 telemetry packet — RETAINED ONLY AS A SIM-MODE STUB.
    ///
    /// Wire protocol v0.2 does not include a telemetry packet. The car sends a
    /// 16-byte <see cref="EchoPacket"/> per control packet (for RTT measurement)
    /// and that's it. Battery, IMU, ESC etc. will arrive on a separate channel
    /// when we have those sensors — at which point this struct gets a wire
    /// format again.
    ///
    /// In the meantime, sim mode (<c>SimTelemetryBridge</c>) still synthesizes
    /// instances of this struct and pushes them into <see cref="TelemetryReceiver"/>
    /// so the HUD speedometer, engine synth, and ghost recorder can read state
    /// the same way they would from a real car. In hardware mode the receiver
    /// never produces packets — those readouts just stay at zero / "—".
    ///
    /// <see cref="TryUnpack"/> is a no-op — there is no v0.2 wire format for this.
    /// </summary>
    public struct TelemetryPacket
    {
        public uint   seq;
        public ulong  tsUs;
        public uint   lastCmdSeq;
        public ulong  lastCmdTsUs;
        public ushort batteryMv;
        public short  pwmSteering;
        public short  pwmThrottle;
        public byte   state;
        public byte   pktLossPct;
        public byte   wifiRssiNeg;   // dBm magnitude (e.g. 42 → -42 dBm)
        public ushort loopHz;

        /// <summary>
        /// Always returns null — there is no v0.2 telemetry wire format. The
        /// real-car path no longer feeds this struct from the network; it only
        /// gets populated by SimTelemetryBridge.InjectSimulated.
        /// </summary>
        public static TelemetryPacket? TryUnpack(byte[] data) => null;
    }

    /// <summary>
    /// V0.1 telemetry/state constants. Kept for sim mode's synthetic telemetry
    /// — see <see cref="TelemetryPacket"/>. Not on the v0.2 wire. Hardware mode
    /// holds <see cref="STATE_IDLE"/> indefinitely (zero is the struct default)
    /// since no real telemetry source updates the field; sim mode pushes
    /// <see cref="STATE_RUNNING"/> via SimTelemetryBridge.
    /// </summary>
    public static class TelemetryStates
    {
        public const byte STATE_IDLE     = 0;
        public const byte STATE_ARMED    = 1;
        public const byte STATE_RUNNING  = 2;
        public const byte STATE_ESTOP    = 3;
        public const byte STATE_FAULT    = 4;
        public const byte STATE_DISARMED = STATE_IDLE; // alias retained from v0.1
    }
}
