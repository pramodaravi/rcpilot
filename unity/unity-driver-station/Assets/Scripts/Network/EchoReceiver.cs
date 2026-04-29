using System;
using System.Collections.Generic;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// V0.2 round-trip latency tracker. Subscribes to <see cref="ControlSender"/>'s
    /// OnPacketSent / OnBytesReceived events, matches incoming 16-byte
    /// <see cref="EchoPacket"/>s against a per-seq table of send-times, and
    /// publishes a rolling RTT histogram (mean / p50 / p95 / p99) for the HUD.
    ///
    /// This replaces the v0.1 <see cref="TelemetryReceiver"/> for the V1 driver
    /// display. TelemetryReceiver still exists for sim mode (where SimTelemetryBridge
    /// injects synthetic packets), but no longer binds a UDP port in hardware mode.
    ///
    /// Why a separate class instead of folding RTT into ControlSender: it lets
    /// the sender stay focused on "pack-and-fire 250 times a second", and lets
    /// us keep the matching logic / percentile computation easy to test in
    /// isolation. Future: a parallel class will receive the eventual telemetry
    /// channel (battery, IMU, ESC) once those sensors exist.
    ///
    /// Stats are recomputed once per <see cref="Update"/> rather than on every
    /// echo arrival, so the per-frame cost stays bounded even at 250 Hz.
    /// </summary>
    public class EchoReceiver : MonoBehaviour
    {
        // Fired (on the main thread) when a fresh echo arrives. Subscribers like
        // a future "RTT spike" detector can hook in without polling.
        public event Action<float> OnRttSample;   // RTT in milliseconds

        private ControlSender _sender;

        // Per-seq send-time table. We allow a packet to age out at <c>_dropWindow</c>
        // packets behind the latest send before we count it as lost — anything
        // older almost certainly isn't coming back.
        private readonly Dictionary<uint, double> _sendTimes = new Dictionary<uint, double>();
        private const int DropWindow = 250;       // ~1 second at 250 Hz

        // Rolling RTT samples for percentile stats. ~1 second of history.
        private const int MaxSamples = 250;
        private readonly Queue<float> _rtts = new Queue<float>();

        // Snapshot stats — updated once per frame in Update().
        public float RttMeanMs    { get; private set; }
        public float RttP50Ms     { get; private set; }
        public float RttP95Ms     { get; private set; }
        public float RttP99Ms     { get; private set; }
        public int   SamplesInWindow => _rtts.Count;

        public uint   PacketsSent       { get; private set; }
        public uint   EchoesReceived    { get; private set; }
        public uint   PacketsLost       { get; private set; }   // sends without an echo within DropWindow
        public double LastEchoTime      { get; private set; } = -1.0;
        public double LastSendTime      { get; private set; } = -1.0;
        public uint   LastEchoProcUs    { get; private set; }   // car-side processing time (informational)

        /// <summary>
        /// Milliseconds since the most recent echo arrived. Returns a large
        /// sentinel (9999 ms) when no echo has ever been seen — callers can use
        /// this directly to drive a "link lost" indicator (e.g. red if &gt; 500).
        /// </summary>
        public float LastEchoAgeMs => LastEchoTime < 0
            ? 9999f
            : (float)((Time.realtimeSinceStartupAsDouble - LastEchoTime) * 1000.0);

        /// <summary>
        /// Quick health bucket: 0 = OK, 1 = degraded, 2 = lost. Used by the HUD
        /// to pick a colour without re-encoding the same thresholds in three
        /// places. Tunable here — these match the python <c>rcpilot</c> CLI.
        /// </summary>
        public int LinkHealth
        {
            get
            {
                if (LastEchoTime < 0) return 2;            // never connected
                float age = LastEchoAgeMs;
                if (age > 500f) return 2;                  // lost
                if (age > 100f) return 1;                  // degraded
                if (RttMeanMs > 50f) return 1;             // high latency
                return 0;                                  // OK
            }
        }

        private bool _statsDirty;

        public void Configure(ControlSender sender)
        {
            if (_sender != null) Unhook();
            _sender = sender;
            if (_sender == null)
            {
                Log.Warn("EchoReceiver: no ControlSender — RTT stats will not update");
                return;
            }
            _sender.OnPacketSent    += HandlePacketSent;
            _sender.OnBytesReceived += HandleBytesReceived;
            Log.Info("EchoReceiver attached to ControlSender");
        }

        private void Unhook()
        {
            if (_sender == null) return;
            _sender.OnPacketSent    -= HandlePacketSent;
            _sender.OnBytesReceived -= HandleBytesReceived;
            _sender = null;
        }

        private void HandlePacketSent(ControlPacket pkt)
        {
            double now = Time.realtimeSinceStartupAsDouble;
            _sendTimes[pkt.seq] = now;
            LastSendTime = now;
            PacketsSent++;

            // Age out send-times that are too old to ever match — count them
            // as lost and free the dictionary slot. Fixed-size dictionary
            // avoids unbounded growth on a long session.
            if (_sendTimes.Count > DropWindow + 32)
            {
                uint cutoff = pkt.seq > DropWindow ? pkt.seq - (uint)DropWindow : 0;
                // Collect-then-remove because we can't mutate while iterating.
                List<uint> stale = null;
                foreach (var kv in _sendTimes)
                {
                    if (kv.Key < cutoff)
                    {
                        if (stale == null) stale = new List<uint>(8);
                        stale.Add(kv.Key);
                    }
                }
                if (stale != null)
                {
                    foreach (uint k in stale) _sendTimes.Remove(k);
                    PacketsLost += (uint)stale.Count;
                }
            }
        }

        private void HandleBytesReceived(byte[] data)
        {
            // Anything that isn't 16 bytes can't be an echo — silently ignore
            // so debug datagrams or future telemetry don't spam warnings here.
            if (data == null || data.Length != Protocol.EchoSize) return;
            var maybe = EchoPacket.TryUnpack(data);
            if (!maybe.HasValue) return;
            var echo = maybe.Value;

            if (!_sendTimes.TryGetValue(echo.seq, out double sentAt))
            {
                // Echo for a seq we already aged out (or never sent). Just count.
                EchoesReceived++;
                return;
            }
            _sendTimes.Remove(echo.seq);

            double now = Time.realtimeSinceStartupAsDouble;
            float rttMs = (float)((now - sentAt) * 1000.0);
            _rtts.Enqueue(rttMs);
            while (_rtts.Count > MaxSamples) _rtts.Dequeue();

            LastEchoTime = now;
            LastEchoProcUs = echo.procUs;
            EchoesReceived++;
            _statsDirty = true;

            OnRttSample?.Invoke(rttMs);
        }

        private void Update()
        {
            if (!_statsDirty) return;
            _statsDirty = false;
            RecomputeStats();
        }

        private void RecomputeStats()
        {
            int n = _rtts.Count;
            if (n == 0)
            {
                RttMeanMs = RttP50Ms = RttP95Ms = RttP99Ms = 0f;
                return;
            }

            float[] arr = _rtts.ToArray();
            Array.Sort(arr);

            float sum = 0f;
            for (int i = 0; i < n; i++) sum += arr[i];
            RttMeanMs = sum / n;

            RttP50Ms = arr[n / 2];
            RttP95Ms = arr[Mathf.Clamp(Mathf.CeilToInt(n * 0.95f) - 1, 0, n - 1)];
            RttP99Ms = arr[Mathf.Clamp(Mathf.CeilToInt(n * 0.99f) - 1, 0, n - 1)];
        }

        /// <summary>
        /// Reset all stats. Useful when the user changes session / track and
        /// wants a fresh RTT histogram (e.g. after a Wi-Fi swap).
        /// </summary>
        public void ResetStats()
        {
            _sendTimes.Clear();
            _rtts.Clear();
            RttMeanMs = RttP50Ms = RttP95Ms = RttP99Ms = 0f;
            PacketsSent = EchoesReceived = PacketsLost = 0;
            LastEchoTime = -1.0;
            LastSendTime = -1.0;
            LastEchoProcUs = 0;
            _statsDirty = false;
        }

        private void OnDestroy()
        {
            Unhook();
        }
    }
}
