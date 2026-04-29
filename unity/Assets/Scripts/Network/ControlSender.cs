using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// Sends a ControlPacket to the Jetson at a fixed rate (sendHz). Uses the
    /// main Unity update loop rather than a background thread — Update() is
    /// already pacing us at 60–240 Hz, and the socket send itself is non-blocking
    /// (SO_SNDBUF kept tiny to avoid queue build-up on link slow-down).
    ///
    /// WheelInput feeds this via SetInputs() each frame. We internally own the
    /// sequence number and timestamp.
    ///
    /// Execution order is set to 1000 so that any assist processors (which
    /// run at 500) have a chance to overwrite the raw inputs with their
    /// assisted-and-governed version before we pack + send.
    /// </summary>
    [DefaultExecutionOrder(1000)]
    public class ControlSender : MonoBehaviour
    {
        public event Action<ControlPacket> OnPacketSent;

        private UdpClient _udp;
        private IPEndPoint _dest;
        private int _sendHz = 200;
        private double _accum;
        private double _lastSend;
        private uint _seq;

        // Latest inputs set by WheelInput every frame.
        public short steering;
        public short throttle;
        public short brake;
        public short reverse;
        public byte  buttons;
        public byte  assist;

        public bool Ready { get; private set; }

        public int SendHz => _sendHz;
        public int LastSendAgeMs => _lastSend > 0
            ? Mathf.RoundToInt((float)((Time.realtimeSinceStartupAsDouble - _lastSend) * 1000.0))
            : -1;

        public void Configure(string jetsonIp, int controlPort, int sendHz)
        {
            _sendHz = Mathf.Clamp(sendHz, 10, 500);
            try
            {
                _udp = new UdpClient(AddressFamily.InterNetwork);
                _udp.Client.SendBufferSize = 2048; // match windows/control_sender.py
                _dest = new IPEndPoint(IPAddress.Parse(jetsonIp), controlPort);
                Ready = true;
                Log.Info($"ControlSender → {jetsonIp}:{controlPort} @ {_sendHz}Hz");
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

        public void EmitOnce()
        {
            if (!Ready) return;
            var pkt = new ControlPacket
            {
                seq = _seq++,
                tsUs = Protocol.NowUs(),
                steering = steering,
                throttle = throttle,
                brake = brake,
                reverse = reverse,
                buttons = buttons,
                assist = assist,
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

        /// <summary>
        /// Fire-and-forget polite shutdown: send a disarm + neutral inputs so the
        /// Jetson sees a quiet driver rather than waiting for the watchdog.
        /// </summary>
        public void SendDisarmAndQuit()
        {
            if (!Ready) return;
            steering = throttle = brake = reverse = 0;
            buttons = Protocol.BTN_DISARM;
            for (int i = 0; i < 6; i++) EmitOnce();
            buttons = 0;
            try { _udp.Close(); } catch { }
            Ready = false;
        }

        private void OnDestroy()
        {
            try { _udp?.Close(); } catch { }
        }
    }
}
