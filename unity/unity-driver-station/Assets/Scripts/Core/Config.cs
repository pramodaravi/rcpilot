using System;
using System.IO;
using UnityEngine;

namespace RcPilot.Core
{
    /// <summary>
    /// Runtime configuration loaded from StreamingAssets/config.json.
    /// Falls back to safe defaults when the file is missing. Keeping this in
    /// StreamingAssets (not Resources) lets a user edit it post-build without
    /// rebuilding the player — which is exactly what a tech walking around
    /// the pits wants.
    /// </summary>
    [Serializable]
    public class Config
    {
        public NetworkConfig network = new NetworkConfig();
        public VideoConfig video = new VideoConfig();
        public WheelConfig wheel = new WheelConfig();
        public RaceConfig race = new RaceConfig();
        public HudConfig hud = new HudConfig();
        public AudioConfig audio = new AudioConfig();
        public SimConfig sim = new SimConfig();
        public AssistsConfig assists = new AssistsConfig();

        public static Config LoadOrDefault()
        {
            string path = Path.Combine(Application.streamingAssetsPath, "config.json");
            try
            {
                if (File.Exists(path))
                {
                    string json = File.ReadAllText(path);
                    Config c = JsonUtility.FromJson<Config>(json);
                    if (c != null)
                    {
                        Log.Info($"Config loaded from {path}");
                        return c;
                    }
                }
                Log.Warn($"Config not found at {path}, using defaults");
            }
            catch (Exception e)
            {
                Log.Err($"Config load failed: {e.Message} — using defaults");
            }
            return new Config();
        }
    }

    [Serializable]
    public class NetworkConfig
    {
        // V0.2 defaults — match rcpilot/config/default.yaml on the Jetson side.
        // 192.168.1.53 is the Jetson's bench Wi-Fi IP on the Starlink subnet;
        // set to 192.168.55.1 (USB-C virtual ethernet) for cabled bring-up.
        // controlPort 5005 is where the echo server listens; the cockpit's
        // send socket is also where echoes come back, so we don't need a
        // separate echo port. telemetryPort is retained as a stub for the
        // future battery/IMU/ESC channel — currently unused.
        public string jetsonIp = "192.168.1.53";
        public int localControlPort = 5005;
        public int controlPort = 5005;
        public int telemetryPort = 5006;   // reserved; not bound in V1
        public int sendHz = 250;            // matches rcpilot/cockpit/control_sender.py
    }

    [Serializable]
    public class VideoConfig
    {
        // The video-bridge/ sidecar decodes RTP H.264 from the Jetson and
        // forwards raw RGB frames over TCP by default (JPEG is only a fallback).
        // If the sidecar is not running, the camera plane shows a "NO SIGNAL"
        // static overlay and the rest of the app still works.
        //
        // Hardware mode receives one already-stitched panorama. cam1Port is
        // retained for legacy dual-stream benches and sim/chase views.
        public string bridgeHost = "127.0.0.1";
        public int cam0Port = 9000;
        public int cam1Port = 0;            // stitched sender uses one TCP bridge
        public int texWidth = 2560;
        public int texHeight = 720;
    }

    [Serializable]
    public class WheelConfig
    {
        // Name kept as WheelConfig because the rest of the codebase refers to
        // cfg.wheel.* everywhere — renaming it would churn a dozen files and
        // a config.json schema for no functional gain. "Wheel" just means
        // "whatever driver input device is plugged in" now.
        //
        // Defaults target a wired or Bluetooth-paired Xbox controller on
        // macOS Sonoma+. If your gamepad lays axes out differently, set
        // logDiscovery=true (or press the backtick key at runtime) and watch
        // the Unity console — it'll tell you which axis moved when you
        // wiggle a stick or squeeze a trigger.
        public int steeringAxis = 1;      // Xbox left-stick X on macOS is axis 1
        public int throttleAxis = 6;      // right trigger on macOS Xbox layout
        public int brakeAxis = 5;         // left trigger on macOS Xbox layout
        public float steeringDeadzone = 0.08f;  // sticks need more deadzone than a wheel

        // Trigger polarity:
        //   true  = triggers idle at -1 and travel to +1 (Xbox on macOS)
        //   false = triggers idle at  0 and travel to +1 (DS4/DualSense, some PS layouts)
        public bool triggersAreBiPolar = true;
        public bool invertSteering = false;

        // Button indices — Xbox controller on macOS maps
        //   A=0, B=1, X=2, Y=3, LB=4, RB=5, Back=6, Start=7, LS=8, RS=9
        public int btnArm = 0;        // A
        public int btnDisarm = 1;     // B
        public int btnEstop = 2;      // X  (also spacebar on keyboard)
        public int btnReset = 3;      // Y
        public int btnToggleCam = 4;  // LB
        public int btnLapMark = 5;    // RB

        // Startup diagnostic: when true (or when backtick is held), print all
        // non-zero axes and any pressed buttons to the console at ~2 Hz so
        // you can map an unfamiliar gamepad in one pass.
        public bool logDiscovery = false;
    }

    [Serializable]
    public class RaceConfig
    {
        public int targetLapCount = 5;
        public float minLapSeconds = 10f;      // debounce: ignore lap marks < this
        public bool autoLapFromButton = true;  // manual lap mark via wheel button
        public string driverName = "Driver 1";
        public string trackName  = "Full Throttle Novi — Main Loop";
    }

    [Serializable]
    public class HudConfig
    {
        public bool showTelemetryOverlay = true;
        public bool showInputsOverlay = true;
        public bool showRaceHud = true;
        public float hudAlpha = 0.85f;
    }

    [Serializable]
    public class AudioConfig
    {
        public bool engineSoundEnabled = true;
        public float masterVolume = 0.7f;
        public float engineMinHz = 80f;
        public float engineMaxHz = 420f;
    }

    /// <summary>
    /// Sim-mode configuration. When <see cref="enabled"/> is false (default),
    /// the app runs its normal telemetry-over-UDP + video-bridge-over-TCP
    /// pipeline against a real Jetson. When true, the Bootstrapper builds a
    /// procedural Novi-inspired track + arcade car physics + in-engine FPV
    /// camera, and the rest of the app (HUD, race manager, audio) reads from
    /// the simulated car instead of the network. Useful for:
    ///   1. Demos when no car is attached.
    ///   2. Iterating on the driver-assist system.
    ///   3. Tuning the cockpit/HUD visuals without bench hardware.
    /// </summary>
    [Serializable]
    public class SimConfig
    {
        public bool enabled = false;

        // Car physics. Values calibrated for "1/8 RC scale on a kart track",
        // so top speed ~50 km/h, 0-50 km/h in ~1.5 s, grip consistent with
        // foam-tire on indoor polished concrete.
        public float straightSpeedMps = 13.5f;  // ~50 km/h top speed
        public float accelMps2 = 9.0f;          // 0 → top speed in ~1.5 s
        public float brakeMps2 = 18.0f;         // strong regen brakes
        public float maxSteerDeg = 35f;         // front-wheel deflection
        public float wheelbaseM = 0.33f;        // 1/8 RC
        public float gripN = 35f;               // lateral grip budget
        public float drag = 1.2f;               // linear air drag coefficient

        // Track. The shape is baked into SimTrack.cs; width is tunable here.
        public float trackWidthM = 4.0f;        // kart-track-ish width at RC scale
        public string trackName = "Novi — Main Loop (sim)";
    }

    /// <summary>
    /// Driver-assist system configuration. Assists are a tiered feature (Off,
    /// Beginner, Intermediate, Expert — see <see cref="RcPilot.Assists.AssistLevel"/>).
    /// The default level is selected here and can be changed at runtime from
    /// the menu. Currently the assists stack runs in sim mode always, and on
    /// the real car once indoor positioning (UWB / visual SLAM) exists.
    ///
    /// The per-tier knobs live in <see cref="RcPilot.Assists.AssistTierConfig"/>
    /// and are deliberately NOT exposed here — having 12 assist knobs in a
    /// config.json is a foot-gun. Pick the tier; tune it in code if you must.
    /// </summary>
    [Serializable]
    public class AssistsConfig
    {
        /// <summary>Starting assist level. 0=Off, 1=Beginner, 2=Intermediate, 3=Expert.</summary>
        public int defaultLevel = 1;

        /// <summary>Force-disable the assist stack entirely (no racing line, no
        /// governor, nothing). Useful if we want a raw demo to show what the
        /// assists are actually buying.</summary>
        public bool disabled = false;
    }
}
