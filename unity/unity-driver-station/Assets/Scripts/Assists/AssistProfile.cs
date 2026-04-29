using System;

namespace RcPilot.Assists
{
    /// <summary>
    /// Driver-aid tiers exposed to the customer. Higher tier = more help. Kept
    /// deliberately short (3 tiers + off) because the customer picks one from a
    /// check-in UI — more tiers just confuses the casual driver Full Throttle
    /// is targeting.
    ///
    /// Mapping to assist byte in ControlPacket (shared with Jetson):
    ///   Off = 0, Beginner = 1, Intermediate = 2, Expert = 3
    /// </summary>
    public enum AssistLevel
    {
        Off          = 0,
        Beginner     = 1,
        Intermediate = 2,
        Expert       = 3,
    }

    /// <summary>
    /// Per-tier knob values. Each tier is a weighted combination of:
    ///   - showRacingLine: is the line visible at all?
    ///   - steerMix: how much of the driver's steer is overridden toward the
    ///     line (0 = pure driver; 1 = autopilot to line). In practice we cap
    ///     this at ~0.5 even on Beginner so the driver always feels connected.
    ///   - governorGain: how aggressively throttle is scrubbed when the car
    ///     would enter a corner faster than the racing line's speed hint.
    ///   - barrierWarnDistM: distance at which the barrier warning fires.
    ///     Warning intensity scales as (distM / warn) — 0 at the edge of the
    ///     warning zone, 1 at barrier contact.
    /// </summary>
    [Serializable]
    public class AssistTierConfig
    {
        public bool showRacingLine = false;
        public float steerMix = 0f;
        public float governorGain = 0f;
        public float barrierWarnDistM = 0f;

        public static AssistTierConfig For(AssistLevel lvl)
        {
            switch (lvl)
            {
                case AssistLevel.Beginner:
                    return new AssistTierConfig
                    {
                        showRacingLine   = true,
                        steerMix         = 0.45f,
                        governorGain     = 1.0f,
                        barrierWarnDistM = 0.9f,
                    };
                case AssistLevel.Intermediate:
                    return new AssistTierConfig
                    {
                        showRacingLine   = true,
                        steerMix         = 0.20f,
                        governorGain     = 0.5f,
                        barrierWarnDistM = 0.5f,
                    };
                case AssistLevel.Expert:
                    return new AssistTierConfig
                    {
                        showRacingLine   = true,
                        steerMix         = 0.0f,
                        governorGain     = 0.0f,
                        barrierWarnDistM = 0.0f,
                    };
                case AssistLevel.Off:
                default:
                    return new AssistTierConfig
                    {
                        showRacingLine   = false,
                        steerMix         = 0f,
                        governorGain     = 0f,
                        barrierWarnDistM = 0f,
                    };
            }
        }
    }
}
