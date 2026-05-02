using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Video
{
    /// <summary>
    /// Procedurally constructs a dual-camera widescreen cockpit: two equal-sized
    /// Quads side-by-side forming a wide "windshield" (left half = cam0, right
    /// half = cam1), with the BuggyCockpit 3D model layered in front for the
    /// dash + steering wheel. Each Quad has slight inward yaw so the seam looks
    /// like a real curved windshield instead of a flat letterbox.
    ///
    /// Camera feeds are Texture2D objects owned by this component; the
    /// VideoBridgeClient blits into them each frame, and the cockpit's unlit
    /// materials sample them directly.
    ///
    /// Toggle-cam (C or wheel button) swaps which feed is on which side —
    /// useful for reversing direction or correcting a swapped CSI ribbon.
    /// </summary>
    public class CockpitBuilder : MonoBehaviour
    {
        public Camera mainCamera;
        public GameObject cockpitRoot;
        public Transform mainScreen;
        public Transform secondaryScreen;
        public MeshRenderer mainScreenRenderer;
        public MeshRenderer secondaryScreenRenderer;

        public Texture2D cam0Tex;
        public Texture2D cam1Tex;

        /// <summary>Virtual cockpit (steering wheel + kart body + dashboard
        /// gauges) built on top of the plain cockpit shell. Null until
        /// AttachVirtualCockpit is called; safe to leave null for a stripped
        /// "windshield only" view.</summary>
        public VirtualCockpit virtualCockpit;

        // Sim mode replaces the Texture2D sources with live RenderTextures from
        // the in-engine cameras via SetCameraSource(). These track whatever
        // texture is currently bound to each screen slot so ToggleMain() keeps
        // working regardless of whether we're in sim or real-feed mode.
        private Texture _cam0Source;
        private Texture _cam1Source;

        private bool _cam0IsMain = true;

        public void Build(Config cfg)
        {
            // Textures the video bridges will paint into.
            cam0Tex = new Texture2D(cfg.video.texWidth, cfg.video.texHeight,
                                    TextureFormat.RGBA32, false);
            cam0Tex.wrapMode = TextureWrapMode.Clamp;
            cam0Tex.filterMode = FilterMode.Bilinear;

            cam1Tex = new Texture2D(cfg.video.texWidth, cfg.video.texHeight,
                                    TextureFormat.RGBA32, false);
            cam1Tex.wrapMode = TextureWrapMode.Clamp;
            cam1Tex.filterMode = FilterMode.Bilinear;

            PaintNoSignalPattern(cam0Tex, Color.gray);
            PaintNoSignalPattern(cam1Tex, new Color(0.2f, 0.2f, 0.3f));

            _cam0Source = cam0Tex;
            _cam1Source = cam1Tex;

            // Main camera
            var camGO = new GameObject("CockpitCamera");
            mainCamera = camGO.AddComponent<Camera>();
            mainCamera.clearFlags = CameraClearFlags.SolidColor;
            mainCamera.backgroundColor = new Color(0.03f, 0.03f, 0.04f);
            mainCamera.fieldOfView = 55f;
            mainCamera.nearClipPlane = 0.05f;
            mainCamera.farClipPlane = 100f;
            mainCamera.transform.position = new Vector3(0, 1.15f, -0.4f);
            mainCamera.transform.rotation = Quaternion.Euler(2f, 0f, 0f);
            camGO.AddComponent<AudioListener>();

            // Light
            var lightGO = new GameObject("Sun");
            var l = lightGO.AddComponent<Light>();
            l.type = LightType.Directional;
            l.color = new Color(0.9f, 0.92f, 1f);
            l.intensity = 0.6f;
            lightGO.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

            cockpitRoot = new GameObject("Cockpit");

            // The Jetson stitches both IMX219 cameras into one 2560x720 stream
            // via nvcompositor BEFORE encoding (see scripts/jetson/start_video_stitched.sh).
            // The cockpit therefore sees ONE wide video and renders it on a
            // single ultra-wide Quad — no shader gymnastics, no duplicated
            // middle. cam1Tex is no longer used (cam1Port = 0); ToggleMain
            // becomes a no-op in this mode.
            //
            //   Quad size: 3.5 m wide x 1.0 m tall (~3.5:1 to match 2560x720)
            //   Position : (0, 1.25, 1.5)
            const float kWidth   = 3.50f;
            const float kHeight  = 1.00f;
            const float kCenterZ = 1.50f;
            const float kCenterY = 1.25f;

            mainScreen = BuildScreenQuad("Windshield_Stitched",
                cockpitRoot.transform,
                new Vector3(0f, kCenterY, kCenterZ),
                Vector3.zero,
                new Vector3(kWidth, kHeight, 1f), cam0Tex);
            mainScreenRenderer = mainScreen.GetComponent<MeshRenderer>();

            // No secondary screen in stitched mode — the stitch happens on
            // the Jetson, the cockpit sees one feed.
            secondaryScreen = null;
            secondaryScreenRenderer = null;

            var windshieldRef = mainScreen;

            // Floor — dark closure below the cockpit so the lower edge of the
            // view doesn't leak the bare scene background.
            BuildMatteSlab("Floor", cockpitRoot.transform,
                new Vector3(0f, 0.05f, 0.7f), new Vector3(90f, 0, 0),
                new Vector3(3.6f, 2.6f, 1f), new Color(0.018f, 0.018f, 0.022f));

            // ----- BuggyCockpit loads a real 3D dune-buggy model -----
            // The procedural-primitives approach (V2) couldn't deliver the
            // photoreal cockpit feel the project wants. Switching to a real
            // 3D model gives us proper depth perception (cage at one depth,
            // dash at another, windshield deeper still) for free, and decent
            // materials without art effort.
            //
            // V2 (VirtualCockpitV2) is kept on disk for reference but no
            // longer instantiated. To revert: change the line below back to
            //   cockpitRoot.AddComponent<VirtualCockpitV2>().Build(...)
            cockpitRoot.AddComponent<BuggyCockpit>().Build(cockpitRoot.transform, cfg, windshieldRef);
        }

        /// <summary>
        /// Build a tubular cage piece (cylinder primitive scaled and rotated to
        /// span <paramref name="from"/> → <paramref name="to"/>). Unity's default
        /// cylinder is 1m tall along its local Y axis with 0.5m radius, so we
        /// scale Y to len/2 and X/Z to radius*2, then rotate Y to the segment
        /// direction.
        /// </summary>
        private Transform BuildCageTube(string name, Transform parent,
                                        Vector3 from, Vector3 to,
                                        float radius, Color color)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            Vector3 dir = to - from;
            float len = dir.magnitude;
            go.transform.localPosition = (from + to) * 0.5f;
            go.transform.localScale = new Vector3(radius * 2f, len * 0.5f, radius * 2f);
            go.transform.localRotation = Quaternion.FromToRotation(Vector3.up, dir.normalized);

            var mat = new Material(FindLitShader());
            AssignColor(mat, color);
            go.GetComponent<MeshRenderer>().material = mat;
            return go.transform;
        }

        private static Shader FindLitShader()
        {
            // Cage tubes look better lit than unlit (hint of shading on the
            // curved surface). Standard works in Built-in pipeline; URP/Lit
            // is the fallback if the project ever switches back.
            Shader s = Shader.Find("Standard")
                    ?? Shader.Find("Universal Render Pipeline/Lit")
                    ?? Shader.Find("Unlit/Color");
            return s;
        }

        public Texture2D GetCameraTexture(int idx) => idx == 0 ? cam0Tex : cam1Tex;

        /// <summary>
        /// Build the virtual cockpit (steering wheel, kart body, dashboard
        /// gauges). Safe to call at most once. Speed and lap-count providers
        /// are passed in as delegates so this module stays decoupled from
        /// sim-vs-real-mode plumbing.
        /// </summary>
        public void AttachVirtualCockpit(Config cfg, RcPilot.Input.WheelInput wheel,
                                         RcPilot.Network.TelemetryReceiver telemetry,
                                         System.Func<float> speedMpsProvider,
                                         System.Func<int> lapProvider)
        {
            if (virtualCockpit != null) return;
            if (cockpitRoot == null)
            {
                Log.Warn("CockpitBuilder.AttachVirtualCockpit: Build() not called yet");
                return;
            }
            virtualCockpit = cockpitRoot.AddComponent<VirtualCockpit>();
            virtualCockpit.Init(cockpitRoot.transform, cfg, wheel, telemetry,
                                speedMpsProvider, lapProvider);
        }

        /// <summary>
        /// Swap the texture that the main/secondary screens sample from for
        /// camera slot <paramref name="idx"/>. Sim mode uses this to hand the
        /// cockpit its in-engine RenderTextures; real-feed mode never calls
        /// this and the screens keep reading the Texture2Ds the VideoBridgeClient
        /// writes into.
        /// </summary>
        public void SetCameraSource(int idx, Texture tex)
        {
            if (tex == null) return;
            if (idx == 0) _cam0Source = tex;
            else          _cam1Source = tex;
            RebindScreens();
        }

        public void ToggleMain()
        {
            _cam0IsMain = !_cam0IsMain;
            RebindScreens();
            Log.Info($"Main camera = {(_cam0IsMain ? "cam0" : "cam1")}");
        }

        private void RebindScreens()
        {
            // In single-Quad widescreen mode, mainScreenRenderer holds a
            // material with the RcPilot/Widescreen2Cam shader — both camera
            // textures get bound on it via _LeftTex / _RightTex.
            if (mainScreenRenderer != null)
            {
                var mat = mainScreenRenderer.material;
                if (mat.HasProperty("_LeftTex"))
                {
                    // Widescreen shader: left cam = cam0, right cam = cam1.
                    // ToggleMain swaps which feed lands on which side.
                    Texture leftTex  = _cam0IsMain ? _cam0Source : _cam1Source;
                    Texture rightTex = _cam0IsMain ? _cam1Source : _cam0Source;
                    mat.SetTexture("_LeftTex",  leftTex);
                    mat.SetTexture("_RightTex", rightTex);
                }
                else
                {
                    // Single-texture fallback (e.g. shader missing): show cam0.
                    AssignMainTexture(mat, _cam0IsMain ? _cam0Source : _cam1Source);
                }
            }
            if (secondaryScreenRenderer != null)
                AssignMainTexture(secondaryScreenRenderer.material,
                                  _cam0IsMain ? _cam1Source : _cam0Source);
        }

        private Transform BuildScreenQuad(string name, Transform parent,
                                          Vector3 pos, Vector3 eulers, Vector3 scale,
                                          Texture tex)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            go.transform.localEulerAngles = eulers;
            go.transform.localScale = scale;

            var mat = new Material(FindUnlitShader());
            AssignMainTexture(mat, tex);
            go.GetComponent<MeshRenderer>().material = mat;
            return go.transform;
        }

        /// <summary>
        /// Build the seamless widescreen Quad. Uses the custom
        /// RcPilot/Widescreen2Cam shader to sample cam0 on the left half and
        /// cam1 on the right half of the same Quad, with a feathered blend
        /// across the seam. Falls back to a plain Unlit shader showing only
        /// cam0 if the custom shader can't be found (e.g. shader file missing
        /// from the project).
        /// </summary>
        private Transform BuildWidescreenQuad(string name, Transform parent,
                                              Vector3 pos, Vector3 scale,
                                              Texture leftTex, Texture rightTex)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            go.transform.localEulerAngles = Vector3.zero;
            go.transform.localScale = scale;

            Shader wide = Shader.Find("RcPilot/Widescreen2Cam");
            Material mat;
            if (wide != null)
            {
                mat = new Material(wide);
                mat.SetTexture("_LeftTex",  leftTex);
                mat.SetTexture("_RightTex", rightTex);
            }
            else
            {
                Log.Warn("Widescreen shader 'RcPilot/Widescreen2Cam' not found; "
                       + "falling back to cam0-only Unlit. Make sure "
                       + "Assets/Shaders/RcPilotWidescreen.shader is in the project.");
                mat = new Material(FindUnlitShader());
                AssignMainTexture(mat, leftTex);
            }
            go.GetComponent<MeshRenderer>().material = mat;
            return go.transform;
        }

        private Transform BuildMatteSlab(string name, Transform parent,
                                         Vector3 pos, Vector3 eulers, Vector3 scale,
                                         Color color)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            go.transform.localPosition = pos;
            go.transform.localEulerAngles = eulers;
            go.transform.localScale = scale;
            // Use the lit shader (Standard) for slabs so colors actually
            // apply. The unlit fallback chain after URP removal lands on
            // Unlit/Texture, which has no _Color property — every slab would
            // render as a plain white quad regardless of the color we set.
            // Standard has _Color and gives subtle directional-light shading
            // which adds depth to the matte interior surfaces. Screen quads
            // (camera feed) keep using FindUnlitShader because they need
            // _MainTex and explicitly do NOT want lighting on the feed.
            var mat = new Material(FindLitShader());
            AssignColor(mat, color);
            go.GetComponent<MeshRenderer>().material = mat;
            return go.transform;
        }

        /// <summary>
        /// Sets main texture on the material in a way that works across the
        /// built-in pipeline (uses "_MainTex") and URP's Unlit shader (uses
        /// "_BaseMap"). Doing only one results in a blank plane on the other.
        /// </summary>
        private static void AssignMainTexture(Material mat, Texture tex)
        {
            if (mat.HasProperty("_BaseMap")) mat.SetTexture("_BaseMap", tex);
            if (mat.HasProperty("_MainTex")) mat.SetTexture("_MainTex", tex);
            // For older shaders the convenience prop is the safe fallback.
            mat.mainTexture = tex;
        }

        private static void AssignColor(Material mat, Color color)
        {
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color"))     mat.SetColor("_Color", color);
            mat.color = color;
        }

        private static Shader FindUnlitShader()
        {
            // URP and built-in use different shader names; pick whichever is present.
            Shader s = Shader.Find("Universal Render Pipeline/Unlit")
                    ?? Shader.Find("Unlit/Texture")
                    ?? Shader.Find("Sprites/Default");
            return s;
        }

        private void PaintNoSignalPattern(Texture2D tex, Color baseTint)
        {
            // Cheap 8x8 noise pattern so the screen isn't a bright single color
            // while we wait for the bridge to connect.
            int w = tex.width, h = tex.height;
            var pixels = new Color32[w * h];
            for (int y = 0; y < h; y++)
            {
                for (int x = 0; x < w; x++)
                {
                    float n = Mathf.PerlinNoise(x * 0.05f, y * 0.05f) * 0.5f + 0.25f;
                    Color c = baseTint * n;
                    c.a = 1f;
                    pixels[y * w + x] = (Color32)c;
                }
            }
            tex.SetPixels32(pixels);
            tex.Apply();
        }
    }
}
