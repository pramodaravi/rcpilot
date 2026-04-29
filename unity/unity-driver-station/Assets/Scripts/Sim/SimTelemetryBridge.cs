using UnityEngine;
using RcPilot.Core;
using RcPilot.Network;

namespace RcPilot.Sim
{
    /// <summary>
    /// Synthesizes TelemetryPackets from the SimCar's state and pushes them
    /// into TelemetryReceiver. The rest of the app (speedometer, engine synth,
    /// status panel) reads telemetry the same way it would from the real car —
    /// the only difference is which thread/path puts the data there.
    ///
    /// Rate: 50 Hz. That matches the real Jetson's intended telemetry cadence.
    /// </summary>
    public class SimTelemetryBridge : MonoBehaviour
    {
        public SimCar car;
        public TelemetryReceiver sink;

        private const float PublishHz = 50f;
        private double _accum;
        private uint _seq;
        private uint _lastCmdSeq;

        public void Init(SimCar carRef, TelemetryReceiver sinkRef)
        {
            car = carRef;
            sink = sinkRef;
        }

        private void Update()
        {
            if (car == null || sink == null) return;
            double period = 1.0 / PublishHz;
            _accum += Time.unscaledDeltaTime;
            if (_accum < period) return;
            _accum -= period;
            if (_accum > 0.1) _accum = 0; // catch up after a hiccup

            // Map sim state → protocol units.
            // pwmSteering: ±10000 from current commanded steer (read from control sender).
            // pwmThrottle: 0..+10000 proportional to longitudinal velocity as % of max.
            float vRatio = Mathf.Clamp01(car.SpeedMps / Mathf.Max(0.1f, car.cfg.sim.straightSpeedMps));
            short pwmSteer = (short)Mathf.RoundToInt(Mathf.Clamp(car.transform.InverseTransformDirection(
                car.GetComponent<Rigidbody>().velocity).x * -500f, -10000f, 10000f));
            short pwmThrottle = (short)Mathf.RoundToInt(vRatio * 10000f);

            // Simulate a healthy link.
            var pkt = new TelemetryPacket
            {
                seq = _seq++,
                tsUs = Protocol.NowUs(),
                lastCmdSeq = _lastCmdSeq++,       // incrementing so HUD doesn't flag stale
                lastCmdTsUs = Protocol.NowUs() - 5000, // ~5 ms behind command
                batteryMv = 7800,                 // 2S LiPo nominal
                pwmSteering = pwmSteer,
                pwmThrottle = pwmThrottle,
                state = TelemetryStates.STATE_RUNNING,
                pktLossPct = 0,
                wifiRssiNeg = 42,                 // dBm -42 is a strong link
                loopHz = 50,
            };
            sink.InjectSimulated(pkt);
        }
    }
}
