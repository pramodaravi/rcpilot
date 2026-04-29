using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// Listens for telemetry packets on a background thread and snapshots the
    /// latest one (plus a few derived stats) so the Unity main thread can read
    /// lock-free in Update().
    ///
    /// Background-thread pattern chosen over BeginReceiveFrom because:
    ///   1) simpler control flow (blocking recv with a socket timeout)
    ///   2) doesn't spam the main thread scheduler with callbacks at 50Hz+
    ///   3) exact same pattern as windows/telemetry_receiver.py so behavior
    ///      matches the tele-op stack the rest of the project was designed around.
    /// </summary>
    public class TelemetryReceiver : MonoBehaviour
    {
        public event Action<TelemetryPacket> OnPacket;

        private Thread _thread;
        private volatile bool _running;
        private UdpClient _udp;
        private readonly object _lock = new object();

        // Main-thread visible state.
        public TelemetryPacket Latest { get; private set; }
        public bool HasPacket { get; private set; }
        public double LastPacketTime { get; private set; } = -1.0;

        // Derived rolling stats.
        private readonly MovingAverage _ageMa = new MovingAverage(30);
        private readonly MovingAverage _hzMa  = new MovingAverage(30);
        private double _prevPacketTime = -1.0;
        private int _packetCount;

        public float AgeMs
        {
            get
            {
                if (LastPacketTime < 0) return 9999f;
                return (float)((Time.realtimeSinceStartupAsDouble - LastPacketTime) * 1000.0);
            }
        }

        public float SmoothedHz => _hzMa.Value;
        public int PacketCount => _packetCount;

        public void Configure(int port)
        {
            try
            {
                _udp = new UdpClient(port);
                _udp.Client.ReceiveTimeout = 200; // ms
                _running = true;
                _thread = new Thread(RxLoop) { IsBackground = true, Name = "TelemetryRx" };
                _thread.Start();
                Log.Info($"TelemetryReceiver listening on :{port}");
            }
            catch (Exception e)
            {
                Log.Err($"TelemetryReceiver bind failed on port {port}: {e.Message}");
            }
        }

        private void RxLoop()
        {
            IPEndPoint any = new IPEndPoint(IPAddress.Any, 0);
            while (_running)
            {
                byte[] data;
                try
                {
                    data = _udp.Receive(ref any);
                }
                catch (SocketException)
                {
                    continue; // timeout, loop
                }
                catch (Exception e)
                {
                    if (_running) Log.Warn($"TelemetryReceiver: {e.Message}");
                    break;
                }
                var maybe = TelemetryPacket.TryUnpack(data);
                if (!maybe.HasValue) continue;

                lock (_lock)
                {
                    Latest = maybe.Value;
                    HasPacket = true;
                    double now = Time.realtimeSinceStartupAsDouble;
                    if (_prevPacketTime > 0)
                    {
                        double dt = now - _prevPacketTime;
                        if (dt > 0) _hzMa.Push((float)(1.0 / dt));
                    }
                    _prevPacketTime = now;
                    LastPacketTime = now;
                    _packetCount++;
                }
            }
        }

        public void GetAgeSnapshot(out float ageMs, out float smoothedHz)
        {
            lock (_lock)
            {
                ageMs = AgeMs;
                smoothedHz = _hzMa.Value;
            }
        }

        private void Update()
        {
            // Re-raise OnPacket on the main thread for subscribers (HUD, race, audio).
            if (!HasPacket) return;
            TelemetryPacket snap;
            lock (_lock) snap = Latest;
            OnPacket?.Invoke(snap);
            _ageMa.Push(AgeMs);
        }

        /// <summary>
        /// Sim-mode entry point. Pushes a synthetic packet straight into the
        /// main-thread state so the rest of the app can't tell the difference.
        /// Safe to call from Update() — it bypasses the socket reader thread
        /// entirely. Don't call from a background thread.
        /// </summary>
        public void InjectSimulated(TelemetryPacket pkt)
        {
            Latest = pkt;
            HasPacket = true;
            double now = Time.realtimeSinceStartupAsDouble;
            if (_prevPacketTime > 0)
            {
                double dt = now - _prevPacketTime;
                if (dt > 0) _hzMa.Push((float)(1.0 / dt));
            }
            _prevPacketTime = now;
            LastPacketTime = now;
            _packetCount++;
            // OnPacket will fire from the next Update() tick, same as UDP path.
        }

        private void OnDestroy()
        {
            _running = false;
            try { _udp?.Close(); } catch { }
            try { _thread?.Join(250); } catch { }
        }
    }
}
