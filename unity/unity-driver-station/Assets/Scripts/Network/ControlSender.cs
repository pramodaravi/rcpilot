using System;
using System.Net;
using System.Net.Sockets;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// Sends a v0.2 32-byte ControlPacket to the Jetson at a fixed rate (sendHz),
    /// and pumps incoming 16-byte EchoPackets off the same UDP socket so the
    /// cockpit can compute round-trip latency.
    ///
    /// Why combine send and receive on one socket: the Jetson echo server replies
    /// to the source address+port of the incoming control packet (standard UDP
    /// reply pattern). Our send socket therefore IS the echo receive socket; we
    /// just need to drain it each frame. Using two separate UdpClients on the
    /// same local port is impossible (only one can bind), and bind-to-known-port
    /// vs send-from-ephemeral splits don't buy anything for a single-driver app.
    ///
    /// We poll <see cref="UdpClient.Available"/> from <see cref="Update"/> rather
    /// than running a background recv thread — at our packet sizes and rates the
    /// poll cost is trivial, the code is simpler, and there's no thread-marshal
    /// dance to publish RTT data into Unity's main loop. <see cref="EchoReceiver"/>
    /// subscribes to the events we raise here.
    ///
    /// Execution order is set to 1000 so that any assist processors (which run at
    /// 500) have a chance to overwrite the raw inputs with their assisted-and-
    /// governed version before we pack + send.
    /// </summary>
    [DefaultExecutionOrder(1000)]
    public class ControlSender : MonoBehaviour
    {
        /// <summary>Fired AFTER a control packet hits the wire. Carries the
        /// packet that was just sent. Subscribers (e.g. EchoReceiver) use the
        /// seq number to record send-time for RTT calculation.</summary>
        public event Action<ControlPacket> OnPacketSent;

        /// <summary>Fired for every UDP datagram received on the control socket.
        /// Raw bytes — the subscriber is responsible for parsing (e.g. via
        /// <see cref="EchoPacket.TryUnpack"/>). Datagrams of unexpected sizes
        /// are still raised so debug paths can see them.</summary>
        public event Action<byte[]> OnBytesReceived;

        private UdpClient _udp;
        private IPEndPoint _dest;
        private int _sendHz = 250;     // matches rcpilot/cockpit/control_sender.py
        private double _accum;
        private double _lastSend;
        private uint _seq;

        // Latest inputs set by WheelInput (and possibly post-processed by
        // AssistController) every frame. Floats now — v0.2 wire format is
        // float32, axis ranges identical to HID joystick APIs.
        public float steering;   // -1.0 .. +1.0
        public float throttle;   // 0.0 .. 1.0
        public float brake;      // 0.0 .. 1.0
        public float clutch;     // 0.0 .. 1.0 (unused for V1 G920; reserved for sequential gearbox cars)

        public bool Ready { get; private set; }

        public int SendHz => _sendHz;
        public uint LastSeq => _seq;
        public int LastSendAgeMs => _lastSend > 0
            ? Mathf.RoundToInt((float)((Time.realtimeSinceStartupAsDouble - _lastSend) * 1000.0))
            : -1;

        public void Configure(string jetsonIp, int controlPort, int sendHz, int localPort = 0)
        {
            _sendHz = Mathf.Clamp(sendHz, 10, 500);
            try
            {
                _udp = localPort > 0
                    ? new UdpClient(new IPEndPoint(IPAddress.Any, localPort))
                    : new UdpClient(AddressFamily.InterNetwork);
                // Match rcpilot/cockpit/control_sender.py — small SNDBUF so a
                // wifi blip doesn't queue stale control packets.
                _udp.Client.SendBufferSize = 2048;
                // Ample RCVBUF for the echo stream — at 250 Hz we receive
                // 16-byte echoes, so even 1 second of unconsumed buffer is < 4 KB.
                _udp.Client.ReceiveBufferSize = 65536;
                // Don't block Update() if the kernel has nothing for us. We poll
                // via _udp.Available; this Receive timeout is a safety net.
                _udp.Client.ReceiveTimeout = 1;
                _dest = new IPEndPoint(IPAddress.Parse(jetsonIp), controlPort);
                Ready = true;
                string local = localPort > 0 ? localPort.ToString() : "ephemeral";
                Log.Info($"ControlSender :{local} -> {jetsonIp}:{controlPort} @ {_sendHz}Hz");
            }
            catch (Exception e)
            {
                Log.Err($"ControlSender bind failed: {e.Message}");
                Ready = false;
            }
        }

        private void Update()
        {
            if (!Ready) return;

            // Drain echoes first so RTT is computed against the freshest send-time
            // table BEFORE we add new entries from this frame's sends.
            DrainIncoming();

            double period = 1.0 / _sendHz;
            _accum += Time.unscaledDeltaTime;
            // Drain multiple packets in one frame if we fell behind; cap so a pause
            // doesn't cause a burst.
            int emitted = 0;
            while (_accum >= period && emitted < 4)
            {
                EmitOnce();
                _accum -= period;
                emitted++;
            }
        }

        private void DrainIncoming()
        {
            // _udp.Available is the byte count waiting in the OS receive buffer.
            // We loop because Receive returns one datagram at a time.
            while (_udp != null && _udp.Available > 0)
            {
                IPEndPoint from = new IPEndPoint(IPAddress.Any, 0);
                byte[] data;
                try
                {
                    data = _udp.Receive(ref from);
                }
                catch (SocketException)
                {
                    break; // timeout or transient — try again next frame
                }
                catch (Exception e)
                {
                    if (_seq % 200 == 0) Log.Warn($"ControlSender recv error: {e.Message}");
                    break;
                }
                OnBytesReceived?.Invoke(data);
            }
        }

        public void EmitOnce()
        {
            if (!Ready) return;
            var pkt = new ControlPacket
            {
                seq      = _seq++,
                tsUs     = Protocol.NowUs(),
                steering = steering,
                throttle = throttle,
                brake    = brake,
                clutch   = clutch,
            };
            byte[] data = pkt.Pack();
            try
            {
                _udp.Send(data, data.Length, _dest);
                _lastSend = Time.realtimeSinceStartupAsDouble;
                OnPacketSent?.Invoke(pkt);
            }
            catch (Exception e)
            {
                // Common during wifi drop — don't spam, just count.
                if (_seq % 200 == 0) Log.Warn($"ControlSender send error: {e.Message}");
            }
        }

        // SendDisarmAndQuit() removed — v0.2 has no button channel. The car uses
        // a watchdog: if no control packet arrives within watchdog_ms (~200ms),
        // the Jetson coasts the motor. On polite shutdown, simply ceasing to
        // send packets achieves the same thing within one watchdog interval.

        private void OnDestroy()
        {
            try { _udp?.Close(); } catch { }
        }
    }
}
