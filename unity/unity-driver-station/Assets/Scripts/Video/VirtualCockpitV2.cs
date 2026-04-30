using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Video
{
    /// <summary>
    /// Procedural race-buggy driver cockpit, built from Unity primitives only.
    /// Originated from a Gemini design pass against the Traxxas-style POV
    /// reference image; integration fixes applied:
    ///   - Removed stub Config class that shadowed RcPilot.Core.Config
    ///   - Added `using RcPilot.Core;` so Build(Config) resolves correctly
    ///   - Wheel tilt direction flipped to lean the wheel face toward the
    ///     driver (top closer to driver eye than bottom), per the reference
    ///   - Self-wires Steering / SpeedMph / Rpm from Bootstrapper.Instance in
    ///     Update so external code doesn't have to push values every frame
    ///
    /// Build(Transform, Config) is meant to be called once from
    /// CockpitBuilder.Build after the windshield Quad and floor exist. This
    /// class then constructs the cage, branding bar, dash slab + switches,
    /// shock towers, right-side brand sticker stack, steering wheel, hub
    /// display, spokes, and hand fists.
    /// </summary>
    public class VirtualCockpitV2 : MonoBehaviour
    {
        [Header("Telemetry Data — auto-wired in Update if Bootstrapper exists")]
        public float SpeedMph = 0f;
        public float Rpm = 0f;
        public int Gear = 1;
        [Range(-1f, 1f)]
        public float Steering = 0f;

        // Tilt angle of the wheel face toward the driver. Negative = top of
        // wheel toward driver. -65° matches the reference photo's wheel rake.
        [Header("Wheel rake")]
        public float WheelTiltDeg = -65f;

        private Transform _steeringWheelRoot;
        private TextMesh _telemetryText;

        public void Build(Transform root, Config cfg)
        {
            // 1. Core Materials
            Material matCage = CreateStandardMaterial(new Color(0.2f, 0.2f, 0.22f));
            Material matBlack = CreateStandardMaterial(new Color(0.1f, 0.1f, 0.1f));
            Material matCarbon = CreateStandardMaterial(new Color(0.15f, 0.15f, 0.15f));
            Material matShockBody = CreateStandardMaterial(new Color(0.3f, 0.3f, 0.3f));
            Material matShockCoil = CreateStandardMaterial(new Color(0.1f, 0.4f, 1.0f));
            Material matDisplay = new Material(Shader.Find("Unlit/Color"));
            matDisplay.color = Color.black;
            matDisplay.SetColor("_Color", Color.black);

            // 2. Roll Cage (Framing the quad at z=1.5, x=±1.4, y=0.45 to 2.05)
            // Placing cage just in front of the screen at z=1.45 so it doesn't clip or get occluded
            CreateCylinder(root, new Vector3(-1.45f, 1.25f, 1.45f), new Vector3(0.05f, 0.8f, 0.05f), Vector3.zero, matCage); // Left A-Pillar
            CreateCylinder(root, new Vector3(1.45f, 1.25f, 1.45f), new Vector3(0.05f, 0.8f, 0.05f), Vector3.zero, matCage);  // Right A-Pillar
            CreateCylinder(root, new Vector3(0f, 2.08f, 1.45f), new Vector3(0.05f, 1.5f, 0.05f), new Vector3(0, 0, 90), matCage); // Top Bar
            CreateCylinder(root, new Vector3(0f, 2.08f, 0.5f), new Vector3(0.04f, 1.0f, 0.04f), new Vector3(90, 0, 0), matCage);  // Roof spine

            // 3. Branded Top Bar ("RCPILOT")
            Transform topBar = CreatePrimitive(PrimitiveType.Cube, root, new Vector3(0f, 2.08f, 1.40f), new Vector3(1.0f, 0.15f, 0.05f), Vector3.zero, matBlack);
            CreateText(topBar, "RCPILOT", new Vector3(0, 0, -0.03f), 0.02f, Color.red, TextAlignment.Center, TextAnchor.MiddleCenter);

            // 4. Dashboard Slab
            // Reserved bottom 30% (below roughly y=0.35 from driver eye). Placing dash at y=0.4, z=1.1
            Transform dash = CreatePrimitive(PrimitiveType.Cube, root, new Vector3(0f, 0.4f, 1.1f), new Vector3(3.0f, 0.5f, 0.4f), new Vector3(-15, 0, 0), matCarbon);

            // Dash Details (switches/knobs)
            CreatePrimitive(PrimitiveType.Cube, dash, new Vector3(-0.5f, 0.25f, -0.1f), new Vector3(0.02f, 0.05f, 0.02f), Vector3.zero, matBlack);
            CreatePrimitive(PrimitiveType.Cube, dash, new Vector3(-0.4f, 0.25f, -0.1f), new Vector3(0.02f, 0.05f, 0.02f), Vector3.zero, matBlack);
            CreatePrimitive(PrimitiveType.Cylinder, dash, new Vector3(0.6f, 0.25f, -0.1f), new Vector3(0.04f, 0.02f, 0.04f), new Vector3(90, 0, 0), matBlack);

            // 5. Front Shock Towers (Visible through corners of the windshield perspective)
            // Placed just in front of the screen bounds so they don't get occluded by the video feed
            BuildShockTower(root, new Vector3(-1.1f, 0.7f, 1.42f), matShockBody, matShockCoil);
            BuildShockTower(root, new Vector3( 1.1f, 0.7f, 1.42f), matShockBody, matShockCoil);

            // 6. Right Side Panel
            Transform sidePanel = CreatePrimitive(PrimitiveType.Cube, root, new Vector3(1.2f, 0.8f, 0.6f), new Vector3(0.1f, 0.8f, 0.8f), new Vector3(0, -10, 0), matBlack);
            string[] brands = { "RPM", "PRO-LINE", "TEKIN", "AVOX", "HOBBYWING" };
            for (int i = 0; i < brands.Length; i++)
            {
                float yOffset = 0.3f - (i * 0.15f);
                CreateText(sidePanel, brands[i], new Vector3(-0.06f, yOffset, 0), 0.012f, Color.white, TextAlignment.Center, TextAnchor.MiddleCenter, new Vector3(0, -90, 0));
            }

            // 7. Steering Wheel & Telemetry
            _steeringWheelRoot = new GameObject("SteeringWheelRoot").transform;
            _steeringWheelRoot.SetParent(root);
            _steeringWheelRoot.localPosition = new Vector3(0f, 0.7f, 0.4f); // Driver eye is at y=1.15, z=-0.4. This is comfortably in front and down.

            // Wheel rim (procedural segmented ring)
            int segments = 16;
            float radius = 0.18f;
            for (int i = 0; i < segments; i++)
            {
                float angle1 = i * Mathf.PI * 2 / segments;
                float angle2 = (i + 1) * Mathf.PI * 2 / segments;
                Vector3 p1 = new Vector3(Mathf.Cos(angle1), Mathf.Sin(angle1), 0) * radius;
                Vector3 p2 = new Vector3(Mathf.Cos(angle2), Mathf.Sin(angle2), 0) * radius;
                Vector3 mid = (p1 + p2) / 2f;
                float length = Vector3.Distance(p1, p2);

                Transform seg = CreatePrimitive(PrimitiveType.Cylinder, _steeringWheelRoot, mid, new Vector3(0.025f, length / 2f, 0.025f), Vector3.zero, matBlack);
                seg.up = p2 - p1; // Orient along the ring perimeter
            }

            // Wheel hub & display
            Transform hub = CreatePrimitive(PrimitiveType.Cube, _steeringWheelRoot, Vector3.zero, new Vector3(0.15f, 0.1f, 0.03f), Vector3.zero, matDisplay);
            _telemetryText = CreateText(hub, "0 MPH\n0 RPM\nG1", new Vector3(0, 0, -0.016f), 0.008f, Color.green, TextAlignment.Center, TextAnchor.MiddleCenter);

            // Spokes connecting hub to rim
            CreatePrimitive(PrimitiveType.Cube, _steeringWheelRoot, new Vector3(-0.1f, 0, 0), new Vector3(0.1f, 0.02f, 0.01f), Vector3.zero, matCarbon);
            CreatePrimitive(PrimitiveType.Cube, _steeringWheelRoot, new Vector3(0.1f, 0, 0), new Vector3(0.1f, 0.02f, 0.01f), Vector3.zero, matCarbon);
            CreatePrimitive(PrimitiveType.Cube, _steeringWheelRoot, new Vector3(0, -0.1f, 0), new Vector3(0.02f, 0.1f, 0.01f), Vector3.zero, matCarbon);

            // Hands (fists)
            CreatePrimitive(PrimitiveType.Sphere, _steeringWheelRoot, new Vector3(-radius, 0f, -0.02f), new Vector3(0.08f, 0.08f, 0.08f), Vector3.zero, matBlack);
            CreatePrimitive(PrimitiveType.Sphere, _steeringWheelRoot, new Vector3( radius, 0f, -0.02f), new Vector3(0.08f, 0.08f, 0.08f), Vector3.zero, matBlack);
        }

        private void Update()
        {
            // Self-wire from Bootstrapper if present so external code doesn't
            // have to push these values every frame.
            var boot = Bootstrapper.Instance;
            if (boot != null)
            {
                if (boot.wheelInput != null)
                {
                    Steering = boot.wheelInput.state.steering;
                    // RPM proxy: throttle * a believable peak. Real RPM telemetry
                    // doesn't exist yet (no ESC pickup). Keeps the readout alive.
                    Rpm = boot.wheelInput.state.throttle * 14000f;
                }
                if (boot.simCar != null) SpeedMph = boot.simCar.SpeedMps * 2.237f;
            }

            if (_steeringWheelRoot != null)
            {
                // Wheel tilts toward driver (top toward driver eye), then
                // rotates around its own Z axis based on Steering input.
                _steeringWheelRoot.localRotation =
                    Quaternion.Euler(WheelTiltDeg, 0f, -Steering * 270f);
            }

            if (_telemetryText != null)
            {
                _telemetryText.text = $"{SpeedMph:F0} MPH\n{Rpm:F0} RPM\nG{Gear}";
            }
        }

        // --- Utility Methods ---

        private Material CreateStandardMaterial(Color c)
        {
            Material m = new Material(Shader.Find("Standard"));
            m.color = c;
            m.SetColor("_Color", c);
            return m;
        }

        private Transform CreatePrimitive(PrimitiveType type, Transform parent, Vector3 pos, Vector3 scale, Vector3 eulerAngles, Material mat)
        {
            GameObject go = GameObject.CreatePrimitive(type);
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            go.transform.localScale = scale;
            go.transform.localEulerAngles = eulerAngles;

            if (go.TryGetComponent<MeshRenderer>(out var renderer))
                renderer.material = mat;

            var col = go.GetComponent<Collider>();
            if (col != null) Destroy(col); // No colliders needed for visual cockpit
            return go.transform;
        }

        private Transform CreateCylinder(Transform parent, Vector3 pos, Vector3 scale, Vector3 eulerAngles, Material mat)
        {
            return CreatePrimitive(PrimitiveType.Cylinder, parent, pos, scale, eulerAngles, mat);
        }

        private TextMesh CreateText(Transform parent, string text, Vector3 localPos, float characterSize, Color color, TextAlignment alignment, TextAnchor anchor, Vector3? eulerAngles = null)
        {
            GameObject go = new GameObject("Text");
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPos;
            go.transform.localEulerAngles = eulerAngles ?? Vector3.zero;

            TextMesh tm = go.AddComponent<TextMesh>();
            Font legacyFont = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            tm.font = legacyFont;

            MeshRenderer renderer = go.GetComponent<MeshRenderer>();
            renderer.material = legacyFont.material;

            tm.text = text;
            tm.color = color;
            tm.characterSize = characterSize;
            tm.alignment = alignment;
            tm.anchor = anchor;

            return tm;
        }

        private void BuildShockTower(Transform parent, Vector3 position, Material bodyMat, Material coilMat)
        {
            Transform towerRoot = new GameObject("ShockTower").transform;
            towerRoot.SetParent(parent, false);
            towerRoot.localPosition = position;
            // Angle them slightly inwards
            towerRoot.localEulerAngles = new Vector3(-20f, 0, position.x > 0 ? 10f : -10f);

            // Main body
            CreateCylinder(towerRoot, Vector3.zero, new Vector3(0.08f, 0.4f, 0.08f), Vector3.zero, bodyMat);

            // Coil springs (stack of thin rings)
            int numCoils = 8;
            for (int i = 0; i < numCoils; i++)
            {
                float yPos = -0.3f + (i * 0.08f);
                CreateCylinder(towerRoot, new Vector3(0, yPos, 0), new Vector3(0.12f, 0.015f, 0.12f), Vector3.zero, coilMat);
            }
        }
    }
}
