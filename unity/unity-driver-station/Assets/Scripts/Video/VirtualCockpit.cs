using System;
using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;
using RcPilot.Network;
using RcPilot.Sim;

namespace RcPilot.Video
{
    /// <summary>
    /// Turns the bare cockpit (windshield + pillars + dashboard slab) into
    /// something that FEELS like sitting in a kart. Builds, at runtime:
    ///   1. A 3D steering wheel that rotates with the driver's steer input.
    ///   2. A virtual kart body (chassis + nose cone + fenders) so the driver
    ///      has peripheral width cues — even though the real FPV camera is
    ///      at the bumper and can't see the car itself.
    ///   3. Dashboard gauges (analog speedo, analog throttle-tach, status
    ///      cluster, 3-digit lap counter) on the dashboard slab.
    ///
    /// Works with or without sim mode:
    ///   - Sim: speed comes from <see cref="speedMpsProvider"/> (Bootstrapper
    ///     wires it to SimCar.SpeedMps).
    ///   - Real: Bootstrapper can pass a provider that reads whatever speed
    ///     estimate the positioning system provides (UWB-derived velocity,
    ///     IMU integration, etc.). When nothing is available, returns 0.
    ///
    /// Math + design lives in docs/cockpit-realism.md. The short version:
    ///   - Driver eye at (0, 1.15, -0.4), cockpit camera FOV 55°.
    ///   - Virtual kart is 1.2m wide × 1.6m long × 0.3m tall, positioned so
    ///     the fenders poke into the lower corners of the main screen at ~27°
    ///     off centerline — matches where a real kart driver sees their
    ///     fenders.
    ///   - All geometry is primitives; no art assets.
    /// </summary>
    public class VirtualCockpit : MonoBehaviour
    {
        // --- User-tunable knobs ---
        [Tooltip("Scale on the virtual kart body width. 1.0 = kart-size.")]
        public float widthScale = 1.0f;
        [Tooltip("Max wheel rotation at full lock, degrees. Real karts are 1.5 turns = 270°.")]
        public float wheelLockDeg = 270f;

        // --- Data sources set via Init ---
        private WheelInput _wheel;
        private TelemetryReceiver _telemetry;
        private Func<float> _speedMpsProvider;
        private float _topSpeedKmh;
        private Func<int> _lapProvider;   // current completed lap count

        // --- Scene objects built in Init ---
        private Transform _wheelXform;
        private Transform _speedoNeedle;
        private Transform _tachNeedle;
        private MeshRenderer _armLight;
        private MeshRenderer _gripLight;
        private MeshRenderer _heatLight;
        private MeshRenderer _pbLight;
        private TextMesh _lapCounter;

        // Smoothing so needles don't jitter.
        private float _smoothedSpeedKmh;
        private float _smoothedThr;

        public void Init(Transform cockpitRoot, Config cfg, WheelInput wheel,
                         TelemetryReceiver telemetry,
                         Func<float> speedMpsProvider,
                         Func<int> lapProvider)
        {
            _wheel = wheel;
            _telemetry = telemetry;
            _speedMpsProvider = speedMpsProvider ?? (() => 0f);
            _lapProvider = lapProvider ?? (() => 0);
            _topSpeedKmh = cfg.sim.straightSpeedMps * 3.6f;
            if (_topSpeedKmh < 10f) _topSpeedKmh = 60f; // fallback when sim config absent

            var host = new GameObject("VirtualCockpit").transform;
            host.SetParent(cockpitRoot, false);

            // Kart body and steering wheel are now owned by VirtualCockpitV2
            // (which models a race-buggy cockpit, not a kart). We keep this
            // class only for the dashboard gauges (speedo, tach, status
            // lights, lap counter) which V2 doesn't build.
            // BuildKartBody(host);          // disabled — V2 owns body geometry
            // BuildSteeringWheel(host);     // disabled — V2 owns the wheel
            BuildDashboardGauges(host);

            Log.Info("VirtualCockpit: dashboard gauges built (body+wheel deferred to V2)");
        }

        // ------------------------------------------------------------------
        // KART BODY — chassis slab + nose cone + two fenders. Positioned so
        // the fender tips sit at ~27° off centerline from the driver's eye,
        // which lands them just inside the lower corners of the main screen.
        // ------------------------------------------------------------------
        private void BuildKartBody(Transform parent)
        {
            var bodyRoot = new GameObject("KartBody").transform;
            bodyRoot.SetParent(parent, false);

            // Chassis — the main "I am sitting in this" slab. Sits just under
            // the dashboard shelf, straddling driver's lap area.
            SpawnPrim(bodyRoot, PrimitiveType.Cube, "Chassis",
                new Vector3(0f, 0.35f, 0.30f),
                new Vector3(1.20f * widthScale, 0.30f, 1.60f),
                new Color(0.12f, 0.12f, 0.14f));

            // Nose cone — small tapered slab forward of driver. Peeks into
            // the bottom of the FPV video as "the hood in front of me".
            var nose = SpawnPrim(bodyRoot, PrimitiveType.Cube, "NoseCone",
                new Vector3(0f, 0.55f, 0.90f),
                new Vector3(0.45f * widthScale, 0.15f, 0.50f),
                new Color(0.85f, 0.20f, 0.10f));   // kart nose tends to be the livery color
            // Tilt the front edge down slightly — gives it a wedged kart look.
            nose.localRotation = Quaternion.Euler(-8f, 0, 0);

            // Fenders — left and right. Tip positions land at ±0.6m wide,
            // 0.95m forward, y=0.52m high → 26.6° off centerline, 24.8° below
            // horizon from driver eye (see docs/cockpit-realism.md).
            SpawnPrim(bodyRoot, PrimitiveType.Cube, "FenderL",
                new Vector3(-0.55f * widthScale, 0.45f, 0.70f),
                new Vector3(0.25f, 0.15f, 0.50f),
                new Color(0.10f, 0.10f, 0.12f));
            SpawnPrim(bodyRoot, PrimitiveType.Cube, "FenderR",
                new Vector3(+0.55f * widthScale, 0.45f, 0.70f),
                new Vector3(0.25f, 0.15f, 0.50f),
                new Color(0.10f, 0.10f, 0.12f));

            // Front tires — visual only, no physics. Black cylinders flanking
            // the nose. Give the kart that "I can see my wheels" feel.
            for (int s = -1; s <= 1; s += 2)
            {
                var wheel = SpawnPrim(bodyRoot, PrimitiveType.Cylinder, $"Tire{(s < 0 ? "L" : "R")}",
                    new Vector3(0.55f * widthScale * s, 0.35f, 0.95f),
                    new Vector3(0.18f, 0.08f, 0.18f),
                    new Color(0.03f, 0.03f, 0.03f));
                wheel.localRotation = Quaternion.Euler(0, 0, 90f);
            }
        }

        // ------------------------------------------------------------------
        // STEERING WHEEL — 6 cylinders forming a hexagonal rim + 3 spokes +
        // center boss. Rotates on local Z-axis with WheelInput.state.steering.
        // ------------------------------------------------------------------
        private void BuildSteeringWheel(Transform parent)
        {
            var wheelHost = new GameObject("SteeringWheelHost").transform;
            wheelHost.SetParent(parent, false);
            wheelHost.localPosition = new Vector3(0f, 0.95f, 0.45f);
            // Tilt the wheel toward the driver like a real cockpit wheel.
            wheelHost.localRotation = Quaternion.Euler(-75f, 0, 0);

            const float RIM_RADIUS = 0.175f;  // 35 cm diameter wheel
            const int RIM_SEGMENTS = 12;
            const float RIM_THICKNESS = 0.018f;
            for (int i = 0; i < RIM_SEGMENTS; i++)
            {
                float a0 = (i / (float)RIM_SEGMENTS) * Mathf.PI * 2f;
                float a1 = ((i + 1) / (float)RIM_SEGMENTS) * Mathf.PI * 2f;
                Vector3 p0 = new Vector3(Mathf.Cos(a0), Mathf.Sin(a0), 0) * RIM_RADIUS;
                Vector3 p1 = new Vector3(Mathf.Cos(a1), Mathf.Sin(a1), 0) * RIM_RADIUS;
                Vector3 mid = (p0 + p1) * 0.5f;
                float len = Vector3.Distance(p0, p1);
                var seg = SpawnPrim(wheelHost, PrimitiveType.Cube, $"Rim{i}",
                    mid,
                    new Vector3(RIM_THICKNESS, RIM_THICKNESS, len * 1.02f),
                    new Color(0.08f, 0.08f, 0.08f));
                Vector3 tangent = (p1 - p0).normalized;
                seg.localRotation = Quaternion.LookRotation(tangent, Vector3.forward);
            }

            // Three spokes at -90°, +30°, +150° (flat-bottom feel — +90° is
            // intentionally empty for the F1-style cutout).
            float[] spokeAngles = { -90f, 30f, 150f };
            foreach (var deg in spokeAngles)
            {
                float rad = deg * Mathf.Deg2Rad;
                Vector3 tip = new Vector3(Mathf.Cos(rad), Mathf.Sin(rad), 0) * (RIM_RADIUS * 0.85f);
                SpawnPrim(wheelHost, PrimitiveType.Cube, $"Spoke{deg:F0}",
                    tip * 0.5f,
                    new Vector3(0.02f, RIM_RADIUS * 0.85f, 0.01f),
                    new Color(0.08f, 0.08f, 0.08f)).localRotation
                    = Quaternion.Euler(0, 0, deg - 90f);
            }

            // Center boss with a painted accent — reads as "branded wheel".
            SpawnPrim(wheelHost, PrimitiveType.Cylinder, "Boss",
                new Vector3(0, 0, -0.012f),
                new Vector3(0.06f, 0.015f, 0.06f),
                new Color(0.05f, 0.05f, 0.06f))
                .localRotation = Quaternion.Euler(90f, 0, 0);
            SpawnPrim(wheelHost, PrimitiveType.Cube, "BossAccent",
                new Vector3(0, 0, -0.025f),
                new Vector3(0.06f, 0.008f, 0.002f),
                new Color(0.0f, 0.78f, 1.0f));

            _wheelXform = wheelHost;
        }

        // ------------------------------------------------------------------
        // DASHBOARD GAUGES — speedometer on left, throttle-tach on right,
        // status light cluster in middle, 3-digit lap counter.
        // ------------------------------------------------------------------
        private void BuildDashboardGauges(Transform parent)
        {
            var dashHost = new GameObject("DashGauges").transform;
            dashHost.SetParent(parent, false);
            // Sits on the existing dashboard slab; same tilt (-22° on X).
            dashHost.localPosition = new Vector3(0, 1.05f, 0.55f);
            dashHost.localRotation = Quaternion.Euler(-22f, 0, 0);

            // Speedometer — left side
            _speedoNeedle = BuildGauge(dashHost, "Speedo",
                new Vector3(-0.32f, 0, 0), new Color(0.0f, 0.78f, 1.0f));

            // Throttle-tach — right side (no real RPM telemetry from ESC)
            _tachNeedle = BuildGauge(dashHost, "Tach",
                new Vector3(+0.32f, 0, 0), new Color(1.0f, 0.75f, 0.0f));

            // Status light cluster — center, above the gauges
            var cluster = new GameObject("Status").transform;
            cluster.SetParent(dashHost, false);
            cluster.localPosition = new Vector3(0f, 0.04f, 0f);

            _armLight  = BuildStatusLight(cluster, "ARM",  new Vector3(-0.09f, 0.03f, 0), new Color(1.0f, 0.75f, 0.0f));
            _gripLight = BuildStatusLight(cluster, "GRIP", new Vector3(-0.03f, 0.03f, 0), new Color(1.0f, 0.2f, 0.2f));
            _heatLight = BuildStatusLight(cluster, "HEAT", new Vector3(+0.03f, 0.03f, 0), new Color(1.0f, 0.3f, 0.05f));
            _pbLight   = BuildStatusLight(cluster, "PB",   new Vector3(+0.09f, 0.03f, 0), new Color(0.2f, 0.95f, 0.4f));

            // Lap counter — centered below the status lights
            var lapGO = new GameObject("LapCount");
            lapGO.transform.SetParent(cluster, false);
            lapGO.transform.localPosition = new Vector3(0f, -0.025f, 0f);
            lapGO.transform.localScale = Vector3.one * 0.01f;
            _lapCounter = lapGO.AddComponent<TextMesh>();
            _lapCounter.text = "L00";
            _lapCounter.fontSize = 32;
            _lapCounter.anchor = TextAnchor.MiddleCenter;
            _lapCounter.color = new Color(0.75f, 0.82f, 0.88f);
            _lapCounter.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            _lapCounter.GetComponent<MeshRenderer>().material =
                new Material(_lapCounter.font.material) { mainTexture = _lapCounter.font.material.mainTexture };
        }

        private Transform BuildGauge(Transform parent, string name,
                                     Vector3 localPos, Color accent)
        {
            var dial = SpawnPrim(parent, PrimitiveType.Cylinder, name + "Dial",
                localPos + Vector3.forward * -0.005f,
                new Vector3(0.12f, 0.005f, 0.12f),
                new Color(0.04f, 0.04f, 0.05f));
            dial.localRotation = Quaternion.Euler(90f, 0f, 0f);

            // Tick marks — 9 ticks around the 260° arc from -130° to +130°.
            for (int k = 0; k <= 8; k++)
            {
                float t = k / 8f;
                float deg = Mathf.Lerp(-130f, 130f, t);
                float rad = deg * Mathf.Deg2Rad;
                Vector3 tickPos = localPos + new Vector3(Mathf.Sin(rad), Mathf.Cos(rad), -0.008f) * 0.052f;
                Color c = k >= 7 ? new Color(1.0f, 0.2f, 0.2f) : Color.white;
                SpawnPrim(parent, PrimitiveType.Cube, name + "Tick" + k,
                    tickPos,
                    new Vector3(0.004f, 0.012f, 0.001f),
                    c).localRotation = Quaternion.Euler(0, 0, -deg);
            }

            // Needle pivot — rotates around Z.
            var needle = new GameObject(name + "Needle").transform;
            needle.SetParent(parent, false);
            needle.localPosition = localPos + Vector3.forward * -0.012f;
            var needleArm = SpawnPrim(needle, PrimitiveType.Cube, "Arm",
                new Vector3(0, 0.03f, 0),
                new Vector3(0.005f, 0.06f, 0.001f),
                accent);
            // Emissive — dial needles should glow a bit so they're readable at night.
            var mr = needleArm.GetComponent<MeshRenderer>();
            mr.material.EnableKeyword("_EMISSION");
            if (mr.material.HasProperty("_EmissionColor"))
                mr.material.SetColor("_EmissionColor", accent * 1.8f);
            return needle;
        }

        private MeshRenderer BuildStatusLight(Transform parent, string label,
                                              Vector3 localPos, Color onColor)
        {
            var t = SpawnPrim(parent, PrimitiveType.Cube, "Light_" + label,
                localPos,
                new Vector3(0.04f, 0.015f, 0.001f),
                new Color(0.1f, 0.1f, 0.1f));
            var mr = t.GetComponent<MeshRenderer>();
            mr.material.EnableKeyword("_EMISSION");
            // Store the "on" color in a shared dictionary? Easier: store via userData-ish.
            // MeshRenderer doesn't have userData, so we tag the GameObject.name with the
            // desired color already baked in by the caller's choice. For the Update loop
            // we just flip between near-black (off) and onColor (on).
            t.gameObject.AddComponent<StatusLightState>().onColor = onColor;

            // Label text above the lamp.
            var lblGO = new GameObject(label + "Lbl");
            lblGO.transform.SetParent(parent, false);
            lblGO.transform.localPosition = localPos + new Vector3(0, 0.013f, 0);
            lblGO.transform.localScale = Vector3.one * 0.004f;
            var tm = lblGO.AddComponent<TextMesh>();
            tm.text = label;
            tm.fontSize = 48;
            tm.anchor = TextAnchor.MiddleCenter;
            tm.color = new Color(0.6f, 0.68f, 0.76f);
            tm.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            tm.GetComponent<MeshRenderer>().sharedMaterial = tm.font.material;
            return mr;
        }

        // ------------------------------------------------------------------
        // Per-frame update: needles, lights, steering wheel.
        // ------------------------------------------------------------------
        private void Update()
        {
            // Steering wheel rotation
            if (_wheelXform != null && _wheel != null)
            {
                float steer = Mathf.Clamp(_wheel.state.steering, -1f, 1f);
                // The wheelHost already tilts toward the driver via its parent
                // rotation. The steering rotation is applied on the LOCAL Z
                // axis before that parent tilt, which is what we want.
                var tilt = Quaternion.Euler(-75f, 0, 0);
                var spin = Quaternion.Euler(0, 0, -steer * wheelLockDeg);
                _wheelXform.localRotation = tilt * spin;
            }

            // Speedo needle
            if (_speedoNeedle != null)
            {
                float kmh = _speedMpsProvider() * 3.6f;
                _smoothedSpeedKmh = Mathf.Lerp(_smoothedSpeedKmh, kmh, Time.deltaTime * 6f);
                float t = Mathf.Clamp01(_smoothedSpeedKmh / Mathf.Max(1f, _topSpeedKmh));
                float deg = Mathf.Lerp(-130f, 130f, t);
                _speedoNeedle.localRotation = Quaternion.Euler(0, 0, -deg);
            }

            // Throttle-tach needle (throttle as a proxy for RPM — brushless ESCs
            // don't give us real RPM out, and tach needles jumping wildly looks
            // wrong anyway).
            if (_tachNeedle != null && _wheel != null)
            {
                _smoothedThr = Mathf.Lerp(_smoothedThr, _wheel.state.throttle,
                                          Time.deltaTime * 8f);
                float deg = Mathf.Lerp(-130f, 130f, Mathf.Clamp01(_smoothedThr));
                _tachNeedle.localRotation = Quaternion.Euler(0, 0, -deg);
            }

            // Status lights — derive from whatever's available.
            UpdateStatusLights();

            // Lap counter
            if (_lapCounter != null && _lapProvider != null)
            {
                int laps = _lapProvider();
                _lapCounter.text = $"L{Mathf.Clamp(laps, 0, 999):00}";
            }
        }

        private void UpdateStatusLights()
        {
            // ARM is lit when either ControlSender is sending an arm bit or
            // telemetry says state = armed/running. Simpler: just use throttle>0
            // as a "driver is live" signal until we plug into GameState.
            bool arm = _wheel != null && (_wheel.state.throttle > 0.05f || _wheel.state.brake > 0.05f);
            Flip(_armLight, arm);

            // GRIP — lit red when lateral slip exceeds a threshold. Sim mode
            // fills this from SimCar; in real mode, it'll stay dark until we
            // have IMU-derived lateral-accel telemetry.
            var sim = Bootstrapper.Instance?.simCar;
            bool lowGrip = sim != null && Mathf.Abs(sim.LateralSlip) > 1.2f;
            Flip(_gripLight, lowGrip);

            // HEAT — telemetry-driven once the Jetson reports ESC temp. For
            // now stays off.
            Flip(_heatLight, false);

            // PB — pulses green on a personal-best lap. Hook up via RaceManager
            // event when available; for now, dark.
            Flip(_pbLight, false);
        }

        private static void Flip(MeshRenderer mr, bool on)
        {
            if (mr == null) return;
            var s = mr.GetComponent<StatusLightState>();
            Color target = on ? s.onColor : new Color(0.06f, 0.06f, 0.07f);
            mr.material.color = target;
            if (mr.material.HasProperty("_BaseColor")) mr.material.SetColor("_BaseColor", target);
            if (mr.material.HasProperty("_EmissionColor"))
                mr.material.SetColor("_EmissionColor", on ? s.onColor * 2.2f : Color.black);
        }

        // ------------------------------------------------------------------
        // Primitive spawn helper.
        // ------------------------------------------------------------------
        private static Transform SpawnPrim(Transform parent, PrimitiveType type, string name,
                                           Vector3 localPos, Vector3 localScale, Color color)
        {
            var go = GameObject.CreatePrimitive(type);
            go.name = name;
            var col = go.GetComponent<Collider>();
            if (col != null) Destroy(col);
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPos;
            go.transform.localScale = localScale;

            var mr = go.GetComponent<MeshRenderer>();
            Shader s = Shader.Find("Universal Render Pipeline/Lit")
                    ?? Shader.Find("Standard")
                    ?? Shader.Find("Unlit/Color");
            var mat = new Material(s);
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color"))     mat.SetColor("_Color", color);
            mat.color = color;
            mr.material = mat;
            return go.transform;
        }
    }

    /// <summary>Attached to each status-light cube so the flip code knows
    /// what the "on" color should be without a shared dictionary.</summary>
    public class StatusLightState : MonoBehaviour
    {
        public Color onColor = Color.white;
    }
}
