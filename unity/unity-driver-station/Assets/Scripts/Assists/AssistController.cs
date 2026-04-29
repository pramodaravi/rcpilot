using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;
using RcPilot.Network;
using RcPilot.Sim;
using RcPilot.UI;

namespace RcPilot.Assists
{
    /// <summary>
    /// Driver-assist integrator. Runs between WheelInput (execution order 0)
    /// and ControlSender (execution order 1000), reads the raw driver inputs,
    /// and writes an assisted version back into both <see cref="WheelInput.state"/>
    /// (so SimCar sees it too) and the ControlSender's latched fields (so the
    /// real car receives it when hardware is wired in).
    ///
    /// Three assists:
    ///   1. STEER MIX — blends the driver's steering with a target steering
    ///      computed to put the car onto the racing line. Higher tiers mix
    ///      more autopilot; Expert is 0 (pure driver).
    ///   2. SPEED GOVERNOR — looks a few car-lengths ahead on the racing line,
    ///      reads the max-safe-speed there, and if we're above it, scrubs
    ///      throttle (and optionally adds brake) proportional to the overshoot.
    ///   3. BARRIER WARNING — if a wall is within <see cref="AssistTierConfig.barrierWarnDistM"/>,
    ///      the HUD flashes and (when implemented) a haptic cue fires. Visual
    ///      is currently a toast; haptic goes live when the DD wheel exists.
    ///
    /// The racing line is built once at Start() from the SimTrack; rebuild by
    /// calling <see cref="RebuildRacingLine"/> if sim physics tuning changes
    /// (friction, top speed).
    ///
    /// When a real car exists (UWB / SLAM positioning), swap the SimCar/SimTrack
    /// pair in <see cref="Init"/> for equivalents that return world-pose and
    /// track-geometry from the real system. The assist math is identical.
    /// </summary>
    [DefaultExecutionOrder(500)]
    public class AssistController : MonoBehaviour
    {
        public AssistLevel level = AssistLevel.Off;
        public AssistTierConfig tier = new AssistTierConfig();

        private Config _cfg;
        private WheelInput _wheel;
        private ControlSender _sender;
        private SimCar _car;
        private SimTrack _track;
        private HUDController _hud;

        private RacingLineComputer _line;
        private RacingLineRenderer _lineRenderer;
        private ProximitySensor _proximity;

        // Latched state for HUD/UI to read.
        public float SpeedTargetMps { get; private set; }
        public float BarrierProximity01 { get; private set; }  // 0 clear, 1 at barrier
        public bool GovernorActive { get; private set; }
        public float SteerAssist01 { get; private set; }       // last steer mix magnitude

        // Barrier-warning cooldown so we don't spam toasts.
        private float _lastBarrierToast;

        public void Init(Config cfg, WheelInput wheel, ControlSender sender,
                         SimCar car, SimTrack track, HUDController hud)
        {
            _cfg = cfg;
            _wheel = wheel;
            _sender = sender;
            _car = car;
            _track = track;
            _hud = hud;

            _line = new RacingLineComputer();
            RebuildRacingLine();

            // Line renderer lives on its own GameObject under the sim track root.
            if (_track != null && _track.Root != null)
            {
                var rendererHost = new GameObject("RacingLineRenderer");
                rendererHost.transform.SetParent(_track.Root, false);
                rendererHost.AddComponent<LineRenderer>();
                _lineRenderer = rendererHost.AddComponent<RacingLineRenderer>();
                _lineRenderer.Init(_line, cfg.sim.straightSpeedMps);
            }

            // Proximity sensor parented to the car so we get world-correct rays.
            if (_car != null)
            {
                // Bootstrapper puts the car on layer 2 (Ignore Raycast), so
                // Physics.DefaultRaycastLayers already excludes it. The ray then
                // sees barriers (Default) only.
                _proximity = _car.gameObject.AddComponent<ProximitySensor>();
                _proximity.Init(_car.transform, Physics.DefaultRaycastLayers);
            }

            SetLevel(level);
            Log.Info($"AssistController ready: level={level}, line has {_line.points.Count} points");
        }

        public void RebuildRacingLine()
        {
            if (_track == null) return;
            float frictionCoef = Mathf.Clamp(_cfg.sim.gripN / 35f, 0.3f, 2.0f);
            _line.Build(_track, _cfg.sim.straightSpeedMps, frictionCoef);
            if (_lineRenderer != null) _lineRenderer.Rebuild();
        }

        public void SetLevel(AssistLevel lvl)
        {
            level = lvl;
            tier = AssistTierConfig.For(lvl);
            if (_lineRenderer != null) _lineRenderer.SetVisible(tier.showRacingLine);
            // V0.2 has no `assist` channel on the wire — the assist level is a
            // cockpit-side concept now. The car just sees the post-assist axes.
            Log.Info($"Assist level → {lvl} (steerMix={tier.steerMix:F2} " +
                     $"gov={tier.governorGain:F2} warn={tier.barrierWarnDistM:F1}m)");
        }

        private void Update()
        {
            // Nothing to do in a vacuum.
            if (_cfg == null || _wheel == null || _car == null || _track == null) return;
            if (level == AssistLevel.Off) { ResetLatched(); return; }

            var state = _wheel.state;
            float rawSteer = state.steering;
            float rawThr = state.throttle;
            float rawBrk = state.brake;

            float assistedSteer = rawSteer;
            float assistedThr = rawThr;
            float assistedBrk = rawBrk;

            // -------- 1. Racing-line steer mix --------
            if (tier.steerMix > 0f && _line.points.Count >= 4)
            {
                float targetSteer = ComputeTargetSteer();
                assistedSteer = Mathf.Lerp(rawSteer, targetSteer, tier.steerMix);
                assistedSteer = Mathf.Clamp(assistedSteer, -1f, 1f);
                SteerAssist01 = Mathf.Abs(assistedSteer - rawSteer);
            }
            else
            {
                SteerAssist01 = 0f;
            }

            // -------- 2. Speed governor --------
            GovernorActive = false;
            if (tier.governorGain > 0f && _line.points.Count >= 2)
            {
                // Look ahead 1.0 + 0.6*speed (seconds-of-travel heuristic): ~1 m at
                // rest, ~10 m at top speed. Enough to catch braking zones early.
                float lookAhead = 1.0f + 0.6f * _car.SpeedMps;
                float vTarget = _line.SpeedTargetAhead(_car.transform.position, lookAhead);
                SpeedTargetMps = vTarget;

                float v = _car.SpeedMps;
                if (v > vTarget)
                {
                    float over = (v - vTarget) / Mathf.Max(1f, vTarget); // normalized overshoot
                    float scrub = Mathf.Clamp01(over * 2f * tier.governorGain);
                    // Scrub throttle: fully lift at 2× overshoot
                    assistedThr = rawThr * (1f - scrub);
                    // Beyond 50% overshoot, layer in brake — arcade save from a botched
                    // corner entry. Only applies when driver isn't already braking hard.
                    if (over > 0.5f && rawBrk < 0.5f)
                        assistedBrk = Mathf.Max(rawBrk, (over - 0.5f) * tier.governorGain);
                    GovernorActive = scrub > 0.05f;
                }
            }
            else
            {
                SpeedTargetMps = float.MaxValue;
            }

            // -------- 3. Barrier warning (visual) --------
            if (tier.barrierWarnDistM > 0f && _proximity != null)
            {
                float d = _proximity.latest.minDistM;
                if (d < tier.barrierWarnDistM)
                {
                    // 0 at edge of warn zone, 1 at contact.
                    BarrierProximity01 = Mathf.Clamp01(1f - d / tier.barrierWarnDistM);

                    float now = Time.unscaledTime;
                    if (BarrierProximity01 > 0.5f && now - _lastBarrierToast > 0.6f)
                    {
                        _lastBarrierToast = now;
                        _hud?.toasts?.Show("WALL!", UiTheme.Bad, 0.35f);
                    }
                }
                else
                {
                    BarrierProximity01 = 0f;
                }
            }
            else
            {
                BarrierProximity01 = 0f;
            }

            // -------- Write assisted values back --------
            _wheel.state.steering = assistedSteer;
            _wheel.state.throttle = assistedThr;
            _wheel.state.brake    = assistedBrk;

            if (_sender != null)
            {
                // V0.2: float axes; no `assist` field on the wire.
                _sender.steering = assistedSteer;
                _sender.throttle = assistedThr;
                _sender.brake    = assistedBrk;
            }
        }

        private void ResetLatched()
        {
            SpeedTargetMps = float.MaxValue;
            BarrierProximity01 = 0f;
            GovernorActive = false;
            SteerAssist01 = 0f;
            // V0.2 has no `assist` channel — nothing to clear on the sender.
        }

        /// <summary>
        /// Pick a target aim-point on the racing line ahead of the car, then
        /// compute the steering needed (signed –1..+1, in the car's local frame)
        /// to put the car on that aim-point after the next fraction of a second.
        /// A "pure-pursuit"-style controller — cheap and robust.
        /// </summary>
        private float ComputeTargetSteer()
        {
            Vector3 carPos = _car.transform.position;
            Vector3 carFwd = _car.transform.forward;

            // Aim point: walk 2–8 m along the racing line depending on speed.
            float lookAheadM = Mathf.Clamp(2f + 0.4f * _car.SpeedMps, 2f, 8f);
            int idx = _line.NearestIndex(carPos);
            float walked = 0f;
            int cur = idx;
            int start = idx;
            while (walked < lookAheadM)
            {
                int nxt = (cur + 1) % _line.points.Count;
                walked += Vector3.Distance(_line.points[cur].pos, _line.points[nxt].pos);
                cur = nxt;
                if (cur == start) break;
            }
            Vector3 aim = _line.points[cur].pos;

            // Vector to aim, in car-local space.
            Vector3 toAim = aim - carPos;
            toAim.y = 0;
            if (toAim.sqrMagnitude < 1e-4f) return 0f;
            Vector3 local = _car.transform.InverseTransformDirection(toAim.normalized);
            // local.x is +ve when aim is to our right; steering convention here is +ve = right.
            // Map to [-1..+1] via sin of the heading error (cheap proxy for angle).
            float err = Mathf.Clamp(local.x, -1f, 1f);
            // Scale up so small errors still pull firmly. 1.6x with a hard clamp.
            return Mathf.Clamp(err * 1.6f, -1f, 1f);
        }
    }
}
