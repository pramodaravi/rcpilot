using System.Collections.Generic;
using UnityEngine;
using RcPilot.Input;
using RcPilot.Network;

namespace RcPilot.Race
{
    /// <summary>
    /// Records per-lap input traces (steering/throttle/brake over time) so we
    /// can display a ghost against the best lap later. In v0.1 we don't have
    /// a ground-truth position sensor on the car, so "ghost" = the best lap's
    /// input profile played back visually on the HUD. Once we add wheel
    /// encoders / odometry we can elevate this to a true ghost car in the 3D
    /// cockpit space.
    /// </summary>
    public class GhostRecorder
    {
        public struct Sample
        {
            public float tSinceLapStart;
            public float steering;
            public float throttle;
            public float brake;
            public short pwmSteering;
            public short pwmThrottle;
        }

        public List<Sample> currentLap = new List<Sample>();
        public List<Sample> bestLap = new List<Sample>();
        public float bestLapDuration = -1;

        private float _lapStart = -1;

        public void StartNewLap()
        {
            // Snapshot the just-completed lap if it's a new best.
            if (currentLap.Count > 0)
            {
                float dur = currentLap[currentLap.Count - 1].tSinceLapStart;
                if (bestLapDuration < 0 || dur < bestLapDuration)
                {
                    bestLap = new List<Sample>(currentLap);
                    bestLapDuration = dur;
                }
            }
            currentLap.Clear();
            _lapStart = Time.unscaledTime;
        }

        public void Sample(WheelState ws, TelemetryReceiver telem)
        {
            if (_lapStart < 0) _lapStart = Time.unscaledTime;
            if (currentLap.Count >= 60000) return; // 10 min cap at 100 Hz

            var s = new Sample
            {
                tSinceLapStart = Time.unscaledTime - _lapStart,
                steering = ws.steering,
                throttle = ws.throttle,
                brake = ws.brake,
            };
            if (telem != null && telem.HasPacket)
            {
                s.pwmSteering = telem.Latest.pwmSteering;
                s.pwmThrottle = telem.Latest.pwmThrottle;
            }
            currentLap.Add(s);
        }

        /// <summary>
        /// Look up the best-lap input state at the given time within the lap.
        /// Returns false if we haven't recorded a best lap yet.
        /// </summary>
        public bool QueryBestAt(float t, out Sample sample)
        {
            sample = default;
            if (bestLap.Count == 0) return false;
            // Binary search by tSinceLapStart
            int lo = 0, hi = bestLap.Count - 1;
            while (lo < hi)
            {
                int mid = (lo + hi) >> 1;
                if (bestLap[mid].tSinceLapStart < t) lo = mid + 1;
                else hi = mid;
            }
            sample = bestLap[Mathf.Clamp(lo, 0, bestLap.Count - 1)];
            return true;
        }
    }
}
