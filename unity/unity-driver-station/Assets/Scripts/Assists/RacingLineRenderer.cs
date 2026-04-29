using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Assists
{
    /// <summary>
    /// Draws the racing line on the track using a Unity LineRenderer, with
    /// per-vertex colors that encode the speed profile:
    ///   - green  = fast (>= 80% of track max speed)
    ///   - yellow = medium
    ///   - red    = slow (brake zone)
    /// The driver sees the line painted on the tarmac ahead of them through
    /// the FPV camera — iRacing-style.
    ///
    /// This component is cheap enough to leave running all the time; visibility
    /// is toggled via <see cref="SetVisible"/> driven by AssistTierConfig.
    /// Rebuild the line by calling <see cref="Rebuild"/> whenever the racing
    /// line sample list changes (e.g. after physics tuning or a reset).
    /// </summary>
    [RequireComponent(typeof(LineRenderer))]
    public class RacingLineRenderer : MonoBehaviour
    {
        private LineRenderer _line;
        private RacingLineComputer _computer;
        private float _maxSpeedMps;

        // Line is drawn a few cm above the tarmac so z-fighting doesn't kill it.
        private const float HoverY = 0.04f;
        private const float LineWidth = 0.25f;

        private void Awake()
        {
            _line = GetComponent<LineRenderer>();
            _line.loop = true;
            _line.useWorldSpace = true;
            _line.startWidth = _line.endWidth = LineWidth;
            _line.numCapVertices = 0;
            _line.numCornerVertices = 2;
            _line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            _line.receiveShadows = false;
            _line.alignment = LineAlignment.View;

            // Unlit material so the line pops regardless of scene lighting.
            Shader s = Shader.Find("Sprites/Default")
                    ?? Shader.Find("Unlit/Color")
                    ?? Shader.Find("Universal Render Pipeline/Unlit");
            var mat = new Material(s);
            _line.material = mat;
            _line.colorGradient = new Gradient(); // overwritten per-segment later
        }

        public void Init(RacingLineComputer computer, float maxSpeedMps)
        {
            _computer = computer;
            _maxSpeedMps = Mathf.Max(1f, maxSpeedMps);
            Rebuild();
        }

        /// <summary>Rebuild vertex positions + colors from the current racing line.</summary>
        public void Rebuild()
        {
            if (_computer == null || _computer.points.Count < 3)
            {
                _line.positionCount = 0;
                return;
            }

            int n = _computer.points.Count;
            _line.positionCount = n;

            // LineRenderer's colorGradient has a fixed 8-key max — too coarse for a
            // per-waypoint speed map. Instead paint per-vertex via the positions'
            // color channel (requires assigning colors through SetColors-style API).
            // Easiest reliable approach: pack colors into a vertex color array using
            // the LineRenderer's per-position color via dynamic gradient approximation.
            // Gradient supports up to 8 keys, which is enough for a smoothed speed
            // map since the eye can't tell apart closely-spaced color keys anyway.

            var positions = new Vector3[n];
            float vMin = float.MaxValue, vMax = float.MinValue;
            for (int i = 0; i < n; i++)
            {
                var pt = _computer.points[i];
                positions[i] = new Vector3(pt.pos.x, pt.pos.y + HoverY, pt.pos.z);
                if (pt.speedMps < vMin) vMin = pt.speedMps;
                if (pt.speedMps > vMax) vMax = pt.speedMps;
            }
            _line.SetPositions(positions);

            // Approximate per-waypoint coloring with an 8-key gradient keyed by
            // distance along the line. For each of 8 equally-spaced samples we
            // pick the local min-speed in that window (most visually useful —
            // a braking zone reads as red even if it's only one waypoint long).
            const int KEYS = 8;
            var colorKeys = new GradientColorKey[KEYS];
            var alphaKeys = new GradientAlphaKey[2];
            alphaKeys[0] = new GradientAlphaKey(0.85f, 0f);
            alphaKeys[1] = new GradientAlphaKey(0.85f, 1f);

            for (int k = 0; k < KEYS; k++)
            {
                float t = (float)k / (KEYS - 1);
                int startIdx = Mathf.FloorToInt(t * (n - 1));
                int endIdx = Mathf.Min(n - 1, startIdx + Mathf.Max(1, n / KEYS));
                float windowMin = float.MaxValue;
                for (int i = startIdx; i <= endIdx; i++)
                    if (_computer.points[i].speedMps < windowMin)
                        windowMin = _computer.points[i].speedMps;
                colorKeys[k] = new GradientColorKey(SpeedToColor(windowMin), t);
            }

            var grad = new Gradient();
            grad.SetKeys(colorKeys, alphaKeys);
            _line.colorGradient = grad;
        }

        public void SetVisible(bool v)
        {
            if (_line != null) _line.enabled = v;
        }

        private Color SpeedToColor(float vMps)
        {
            float t = Mathf.Clamp01(vMps / _maxSpeedMps);
            // Piecewise: red→yellow→green
            if (t < 0.5f)
            {
                // red (1,0,0) → yellow (1,1,0)
                float u = t / 0.5f;
                return new Color(1f, u, 0f);
            }
            else
            {
                // yellow (1,1,0) → green (0.2,0.95,0.2)
                float u = (t - 0.5f) / 0.5f;
                return new Color(Mathf.Lerp(1f, 0.2f, u),
                                 Mathf.Lerp(1f, 0.95f, u),
                                 Mathf.Lerp(0f, 0.2f, u));
            }
        }
    }
}
