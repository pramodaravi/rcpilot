using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Video
{
    /// <summary>
    /// Builds the driver's 3D cockpit. If a real buggy model is available at
    /// Assets/Resources/Buggy/buggy.fbx, Unity loads it. If not, this builds a
    /// procedural race-buggy cockpit with layered depth cues around the live
    /// camera feed: windshield frame, side rails, dash, hood, shocks, fenders,
    /// and a steering wheel.
    /// </summary>
    public class BuggyCockpit : MonoBehaviour
    {
        [Header("Buggy model placement")]
        public Vector3 buggyPosition = Vector3.zero;
        public Vector3 buggyEulerAngles = new Vector3(0f, 180f, 0f);
        public float buggyScale = 1f;

        [Header("Windshield Quad")]
        public bool relocateWindshield = true;
        public Vector3 windshieldPosition = new Vector3(0f, 1.24f, 1.55f);
        public Vector2 windshieldSize = new Vector2(2.75f, 1.50f);

        public GameObject buggyInstance;
        public bool modelLoaded;

        private Transform _steeringWheelRoot;
        // Saved at build time so steering rotation is applied ON TOP of the
        // wheel's authored tilt (e.g. -66° X from Blender) rather than
        // overwriting it. localRotation = _wheelBase * Quaternion(0,0,-steer*lock)
        // spins the rim around its own face-normal axis without flattening.
        private Quaternion _wheelBaseRotation = Quaternion.identity;
        // Max wheel-lock angle in degrees at full steering. Real karts use
        // 270°; tweak per-car as needed.
        public float wheelLockDeg = 270f;

        public void Build(Transform root, Config cfg, Transform windshieldQuad)
        {
            GameObject prefab = Resources.Load<GameObject>("Buggy/buggy");
            if (prefab != null)
            {
                BuildModel(root, windshieldQuad, prefab);
                return;
            }

            Log.Warn("BuggyCockpit: Resources/Buggy/buggy not found. Building procedural cockpit.");
            BuildProcedural(root, windshieldQuad);
        }

        private void BuildModel(Transform root, Transform windshieldQuad, GameObject prefab)
        {
            modelLoaded = true;
            buggyInstance = Instantiate(prefab, root);
            buggyInstance.name = "Buggy";
            buggyInstance.transform.localPosition = buggyPosition;
            buggyInstance.transform.localEulerAngles = buggyEulerAngles;
            buggyInstance.transform.localScale = Vector3.one * buggyScale;

            foreach (Collider col in buggyInstance.GetComponentsInChildren<Collider>())
            {
                Destroy(col);
            }

            if (relocateWindshield && windshieldQuad != null)
            {
                windshieldQuad.localPosition = windshieldPosition;
                windshieldQuad.localScale = new Vector3(windshieldSize.x, windshieldSize.y, 1f);
                windshieldQuad.localEulerAngles = Vector3.zero;
            }

            // Find the steering wheel transform inside the imported model so
            // Update() can rotate it with the driver's steering input. The
            // Blender script names this object "SteeringWheel" (the parent
            // empty containing rim + boss + spokes + display). FBX export
            // preserves the name on the corresponding GameObject.
            _steeringWheelRoot = FindChildByName(buggyInstance.transform, "SteeringWheel");
            if (_steeringWheelRoot == null)
            {
                Log.Warn("BuggyCockpit: 'SteeringWheel' not found in imported FBX — wheel won't animate. " +
                         "Check the model contains an object named exactly 'SteeringWheel'.");
            }
            else
            {
                _wheelBaseRotation = _steeringWheelRoot.localRotation;
            }

            Log.Info("BuggyCockpit: loaded Resources/Buggy/buggy");
        }

        /// <summary>Recursively search a transform hierarchy for a child with
        /// the given name. Case-sensitive, returns the first match.</summary>
        private static Transform FindChildByName(Transform parent, string name)
        {
            if (parent.name == name) return parent;
            for (int i = 0; i < parent.childCount; i++)
            {
                var found = FindChildByName(parent.GetChild(i), name);
                if (found != null) return found;
            }
            return null;
        }

        private void BuildProcedural(Transform root, Transform windshieldQuad)
        {
            if (windshieldQuad != null)
            {
                windshieldQuad.localPosition = windshieldPosition;
                windshieldQuad.localScale = new Vector3(windshieldSize.x, windshieldSize.y, 1f);
                windshieldQuad.localEulerAngles = Vector3.zero;
            }

            Material cage = Mat(new Color(0.13f, 0.14f, 0.15f), 0.35f);
            Material cageDark = Mat(new Color(0.035f, 0.038f, 0.042f), 0.55f);
            Material body = Mat(new Color(0.82f, 0.10f, 0.06f), 0.25f);
            Material bodyDark = Mat(new Color(0.09f, 0.095f, 0.105f), 0.45f);
            Material dash = Mat(new Color(0.018f, 0.020f, 0.023f), 0.65f);
            Material rubber = Mat(new Color(0.015f, 0.015f, 0.016f), 0.80f);
            Material metal = Mat(new Color(0.55f, 0.58f, 0.60f), 0.30f);
            Material coil = Mat(new Color(0.05f, 0.38f, 1.0f), 0.20f);
            Material accent = Mat(new Color(0.0f, 0.72f, 1.0f), 0.10f);

            // Cockpit tub, hood, and fenders. These sit low in the frame so the
            // driver sees "the car" without blocking the live track feed.
            Box(root, "CenterTunnel", new Vector3(0f, 0.28f, 0.52f), new Vector3(0.42f, 0.32f, 1.55f), bodyDark);
            Box(root, "LeftFender", new Vector3(-0.82f, 0.42f, 0.90f), new Vector3(0.42f, 0.22f, 1.20f), body);
            Box(root, "RightFender", new Vector3(0.82f, 0.42f, 0.90f), new Vector3(0.42f, 0.22f, 1.20f), body);
            Transform nose = Box(root, "Nose", new Vector3(0f, 0.48f, 1.10f), new Vector3(1.05f, 0.20f, 0.86f), body);
            nose.localEulerAngles = new Vector3(-5f, 0f, 0f);

            // Dashboard and controls.
            Transform dashSlab = Box(root, "BuggyDash", new Vector3(0f, 0.78f, 0.67f), new Vector3(2.45f, 0.34f, 0.42f), dash);
            dashSlab.localEulerAngles = new Vector3(-10f, 0f, 0f);
            Box(root, "DashAccent", new Vector3(0f, 0.95f, 0.44f), new Vector3(1.1f, 0.025f, 0.035f), accent);
            for (int i = 0; i < 5; i++)
            {
                float x = -0.36f + i * 0.18f;
                Box(root, "Toggle" + i, new Vector3(x, 0.94f, 0.40f), new Vector3(0.055f, 0.10f, 0.04f), metal);
            }

            // Windshield frame. It sits slightly closer than the video plane,
            // turning the live image into a physical aperture.
            float z = 1.48f;
            Tube(root, "A_Pillar_L", new Vector3(-1.43f, 0.48f, z), new Vector3(-1.27f, 2.03f, z), 0.035f, cage);
            Tube(root, "A_Pillar_R", new Vector3(1.43f, 0.48f, z), new Vector3(1.27f, 2.03f, z), 0.035f, cage);
            Tube(root, "RoofBar", new Vector3(-1.32f, 2.02f, z), new Vector3(1.32f, 2.02f, z), 0.04f, cage);
            Tube(root, "DashCrossBar", new Vector3(-1.38f, 0.52f, z), new Vector3(1.38f, 0.52f, z), 0.035f, cage);
            Tube(root, "RoofSpine", new Vector3(0f, 2.02f, 1.40f), new Vector3(0f, 1.88f, 0.30f), 0.03f, cageDark);

            // Side rails and bracing for parallax during head/camera motion.
            Tube(root, "LeftSideRail", new Vector3(-1.15f, 0.42f, -0.15f), new Vector3(-1.43f, 0.56f, 1.42f), 0.04f, cageDark);
            Tube(root, "RightSideRail", new Vector3(1.15f, 0.42f, -0.15f), new Vector3(1.43f, 0.56f, 1.42f), 0.04f, cageDark);
            Tube(root, "LeftDoorBar", new Vector3(-1.18f, 0.92f, 0.05f), new Vector3(-1.37f, 1.25f, 1.32f), 0.03f, cage);
            Tube(root, "RightDoorBar", new Vector3(1.18f, 0.92f, 0.05f), new Vector3(1.37f, 1.25f, 1.32f), 0.03f, cage);

            BuildSteeringWheel(root, rubber, metal, accent);
            BuildShock(root, new Vector3(-1.04f, 0.70f, 1.32f), -12f, metal, coil);
            BuildShock(root, new Vector3(1.04f, 0.70f, 1.32f), 12f, metal, coil);
            BuildBrandPlate(root, cageDark, accent);

            Log.Info("BuggyCockpit: procedural cockpit built");
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (_steeringWheelRoot != null && boot != null && boot.wheelInput != null)
            {
                float steer = Mathf.Clamp(boot.wheelInput.state.steering, -1f, 1f);
                // Multiply: spin around local Z first (the wheel's face normal),
                // then apply the wheel's authored tilt. For the procedural build
                // _wheelBaseRotation is identity so the old (-66 X tilt baked
                // into the rotation) behavior changes — handled below.
                _steeringWheelRoot.localRotation =
                    _wheelBaseRotation * Quaternion.Euler(0f, 0f, -steer * wheelLockDeg);
            }
        }

        private void BuildSteeringWheel(Transform root, Material rubber, Material metal, Material accent)
        {
            _steeringWheelRoot = new GameObject("BuggySteeringWheel").transform;
            _steeringWheelRoot.SetParent(root, false);
            _steeringWheelRoot.localPosition = new Vector3(0f, 0.72f, 0.18f);
            _steeringWheelRoot.localEulerAngles = new Vector3(-66f, 0f, 0f);
            // Capture the authored tilt as the base so Update() can multiply
            // a steering rotation onto it without flattening this -66° rake.
            _wheelBaseRotation = _steeringWheelRoot.localRotation;

            const int segments = 18;
            const float radius = 0.22f;
            for (int i = 0; i < segments; i++)
            {
                float a0 = i * Mathf.PI * 2f / segments;
                float a1 = (i + 1) * Mathf.PI * 2f / segments;
                Vector3 p0 = new Vector3(Mathf.Cos(a0), Mathf.Sin(a0), 0f) * radius;
                Vector3 p1 = new Vector3(Mathf.Cos(a1), Mathf.Sin(a1), 0f) * radius;
                Tube(_steeringWheelRoot, "Rim" + i, p0, p1, 0.018f, rubber);
            }

            Tube(_steeringWheelRoot, "SpokeL", Vector3.zero, new Vector3(-0.16f, 0.02f, 0f), 0.012f, metal);
            Tube(_steeringWheelRoot, "SpokeR", Vector3.zero, new Vector3(0.16f, 0.02f, 0f), 0.012f, metal);
            Tube(_steeringWheelRoot, "SpokeD", Vector3.zero, new Vector3(0f, -0.15f, 0f), 0.012f, metal);
            Box(_steeringWheelRoot, "Hub", Vector3.zero, new Vector3(0.16f, 0.10f, 0.035f), metal);
            Box(_steeringWheelRoot, "HubStripe", new Vector3(0f, 0.065f, -0.025f), new Vector3(0.10f, 0.012f, 0.010f), accent);
        }

        private void BuildShock(Transform root, Vector3 pos, float yaw, Material metal, Material coil)
        {
            Transform shock = new GameObject("FrontShock").transform;
            shock.SetParent(root, false);
            shock.localPosition = pos;
            shock.localEulerAngles = new Vector3(-24f, yaw, 0f);

            Tube(shock, "Shaft", new Vector3(0f, -0.28f, 0f), new Vector3(0f, 0.34f, 0f), 0.025f, metal);
            for (int i = 0; i < 9; i++)
            {
                float y = -0.22f + i * 0.055f;
                Primitive(PrimitiveType.Cylinder, shock, "Coil" + i,
                    new Vector3(0f, y, 0f), new Vector3(0.10f, 0.006f, 0.10f), coil);
            }
        }

        private void BuildBrandPlate(Transform root, Material plate, Material accent)
        {
            Transform bar = Box(root, "TopBrandPlate", new Vector3(0f, 2.08f, 1.42f),
                new Vector3(0.92f, 0.14f, 0.04f), plate);
            TextMesh text = Text(bar, "RCPILOT", new Vector3(0f, 0f, -0.026f), 0.055f, accent.color);
            text.anchor = TextAnchor.MiddleCenter;
            text.alignment = TextAlignment.Center;
        }

        private static Transform Box(Transform parent, string name, Vector3 pos, Vector3 scale, Material mat)
        {
            return Primitive(PrimitiveType.Cube, parent, name, pos, scale, mat);
        }

        private static Transform Primitive(PrimitiveType type, Transform parent, string name, Vector3 pos, Vector3 scale, Material mat)
        {
            GameObject go = GameObject.CreatePrimitive(type);
            go.name = name;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            go.transform.localScale = scale;
            if (go.TryGetComponent(out MeshRenderer renderer)) renderer.material = mat;
            Collider col = go.GetComponent<Collider>();
            if (col != null) Destroy(col);
            return go.transform;
        }

        private static Transform Tube(Transform parent, string name, Vector3 from, Vector3 to, float radius, Material mat)
        {
            Transform t = Primitive(PrimitiveType.Cylinder, parent, name, Vector3.zero, Vector3.one, mat);
            Vector3 dir = to - from;
            float len = dir.magnitude;
            t.localPosition = (from + to) * 0.5f;
            t.localScale = new Vector3(radius * 2f, len * 0.5f, radius * 2f);
            if (len > 0.0001f) t.localRotation = Quaternion.FromToRotation(Vector3.up, dir.normalized);
            return t;
        }

        private static TextMesh Text(Transform parent, string value, Vector3 pos, float size, Color color)
        {
            GameObject go = new GameObject("Text");
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            TextMesh tm = go.AddComponent<TextMesh>();
            tm.text = value;
            tm.characterSize = size;
            tm.color = color;
            Font font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            tm.font = font;
            tm.GetComponent<MeshRenderer>().material = font.material;
            return tm;
        }

        private static Material Mat(Color color, float smoothness)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Lit")
                         ?? Shader.Find("Standard")
                         ?? Shader.Find("Unlit/Color");
            Material mat = new Material(shader);
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", color);
            if (mat.HasProperty("_Smoothness")) mat.SetFloat("_Smoothness", smoothness);
            if (mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness", smoothness);
            mat.color = color;
            return mat;
        }
    }
}
