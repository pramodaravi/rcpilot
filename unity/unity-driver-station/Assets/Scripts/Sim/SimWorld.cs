using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Sim
{
    /// <summary>
    /// Sim-mode scene dressing: a big ground plane, a sun light, and a skybox-ish
    /// clear color. Lives well above the cockpit (y=SimRootY) so the cockpit's
    /// camera (farClipPlane=100, at y≈1.15) can't see into the sim and vice versa.
    ///
    /// Why spatial separation instead of layer masks: layers require editing
    /// ProjectSettings/TagManager.asset, which is binary-ish YAML that is easy to
    /// break. A y-offset costs nothing at runtime and makes scene inspection
    /// trivially obvious in the Editor.
    /// </summary>
    public class SimWorld : MonoBehaviour
    {
        /// <summary>Vertical offset of the sim world from the cockpit scene.
        /// Anything at or above this y value belongs to the sim.</summary>
        public const float SimRootY = 1000f;

        public GameObject root;
        public Transform trackParent;
        public Light sun;

        public void Build(Config cfg)
        {
            root = new GameObject("SimWorld");
            root.transform.position = new Vector3(0, SimRootY, 0);

            // Ground — dark asphalt outside the racing surface.
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.name = "Ground";
            ground.transform.SetParent(root.transform, false);
            ground.transform.localPosition = Vector3.zero;
            // Default plane is 10x10 m; scale to cover Novi-sized area comfortably.
            ground.transform.localScale = new Vector3(80, 1, 80);
            ApplyFlatColor(ground.GetComponent<MeshRenderer>(), new Color(0.08f, 0.08f, 0.09f));

            // Sun
            var lightGO = new GameObject("SimSun");
            lightGO.transform.SetParent(root.transform, false);
            sun = lightGO.AddComponent<Light>();
            sun.type = LightType.Directional;
            sun.color = new Color(1f, 0.98f, 0.92f);
            sun.intensity = 1.1f;
            sun.shadows = LightShadows.Soft;
            lightGO.transform.rotation = Quaternion.Euler(55f, -35f, 0f);

            // Ambient bumped slightly so shadowed sides aren't pitch black.
            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.28f, 0.30f, 0.34f);

            trackParent = new GameObject("Track").transform;
            trackParent.SetParent(root.transform, false);
        }

        private static void ApplyFlatColor(MeshRenderer mr, Color c)
        {
            // Use the app's shared unlit/standard shader path — whichever is present.
            Shader s = Shader.Find("Universal Render Pipeline/Lit")
                    ?? Shader.Find("Standard")
                    ?? Shader.Find("Unlit/Color")
                    ?? Shader.Find("Sprites/Default");
            var mat = new Material(s);
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c);
            if (mat.HasProperty("_Color"))     mat.SetColor("_Color", c);
            mat.color = c;
            mr.material = mat;
        }
    }
}
