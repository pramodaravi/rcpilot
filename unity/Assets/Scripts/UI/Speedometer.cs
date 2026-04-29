using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Network;

namespace RcPilot.UI
{
    /// <summary>
    /// 200° arc speedometer drawn live with a RawImage + procedurally generated
    /// Texture2D. We don't have an authored dial sprite so we draw everything
    /// in code every frame the needle moves — cheap at 128×128 resolution.
    ///
    /// "Speed" is derived from the pwm_throttle telemetry field. PWM 1500 =
    /// neutral, 2000 = full forward, 1000 = full reverse. We map absolute
    /// distance from neutral to a 0..100% throttle gauge since we don't have
    /// a wheel encoder yet to compute true ground speed.
    /// </summary>
    public class Speedometer : MonoBehaviour
    {
        private RawImage _dial;
        private Text _pct;
        private Text _label;
        private Texture2D _tex;
        private float _needleNorm;
        private float _smoothedNorm;
        private const int TexRes = 256;

        public void Build(Config cfg)
        {
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            var dialGO = new GameObject("Dial", typeof(RectTransform), typeof(RawImage));
            dialGO.transform.SetParent(transform, false);
            _dial = dialGO.GetComponent<RawImage>();
            var drt = dialGO.GetComponent<RectTransform>();
            drt.anchorMin = new Vector2(0.5f, 0.5f);
            drt.anchorMax = new Vector2(0.5f, 0.5f);
            drt.pivot = new Vector2(0.5f, 0.5f);
            drt.anchoredPosition = new Vector2(0, 20);
            drt.sizeDelta = new Vector2(220, 220);

            _tex = new Texture2D(TexRes, TexRes, TextureFormat.RGBA32, false);
            _tex.wrapMode = TextureWrapMode.Clamp;
            _tex.filterMode = FilterMode.Bilinear;
            _dial.texture = _tex;

            _pct = UiTheme.AddText(gameObject, "Pct", "0%", 48,
                                   TextAnchor.UpperCenter, UiTheme.Text);
            _pct.rectTransform.anchoredPosition = new Vector2(0, -10);

            _label = UiTheme.AddText(gameObject, "Label", "THROTTLE",
                                     18, TextAnchor.LowerCenter, UiTheme.TextDim);
            _label.rectTransform.anchoredPosition = new Vector2(0, 18);

            DrawDial(0f);
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null || boot.telemetry == null) return;
            if (!boot.telemetry.HasPacket) return;

            var t = boot.telemetry.Latest;
            // pwmThrottle range: roughly 1000..2000 us. Neutral = 1500.
            int pwm = t.pwmThrottle;
            int delta = pwm - 1500;
            float pct = Mathf.Clamp01(Mathf.Abs(delta) / 500f);
            _needleNorm = pct;
            _smoothedNorm = Mathf.Lerp(_smoothedNorm, _needleNorm,
                                       Time.unscaledDeltaTime * 8f);
            DrawDial(_smoothedNorm);
            _pct.text = $"{Mathf.RoundToInt(_smoothedNorm * 100)}%";
            _pct.color = delta >= 0 ? UiTheme.Accent : UiTheme.Warn;
            _label.text = delta >= 0 ? "THROTTLE" : "REVERSE";
        }

        private void DrawDial(float norm)
        {
            // Arc from 170° (left) down through 0° (bottom) to -350° ≈ 10° (right).
            // We draw:
            //   - background ring
            //   - tick marks every 10%
            //   - filled arc up to `norm`
            //   - needle line
            var pixels = new Color32[TexRes * TexRes];
            Color32 clear = new Color32(0, 0, 0, 0);
            for (int i = 0; i < pixels.Length; i++) pixels[i] = clear;

            int cx = TexRes / 2;
            int cy = TexRes / 2;
            int rOuter = (TexRes / 2) - 4;
            int rInner = rOuter - 14;

            Color32 ringBg = new Color32(30, 40, 50, 255);
            Color32 tickCol = new Color32(160, 180, 200, 255);
            Color accent = UiTheme.Accent;
            Color warn = UiTheme.Warn;
            Color bad = UiTheme.Bad;
            Color active = norm < 0.75f ? accent : (norm < 0.9f ? warn : bad);

            const float startAng = 150f * Mathf.Deg2Rad;   // left-lower
            const float endAng   = 30f  * Mathf.Deg2Rad;   // right-lower
            const float sweep = -(240f * Mathf.Deg2Rad);   // negative → CCW in screen coords? We'll just sweep continuously.

            // Draw ring background
            for (int y = 0; y < TexRes; y++)
            {
                for (int x = 0; x < TexRes; x++)
                {
                    int dx = x - cx, dy = y - cy;
                    int d2 = dx * dx + dy * dy;
                    if (d2 <= rOuter * rOuter && d2 >= rInner * rInner)
                    {
                        float ang = Mathf.Atan2(dy, dx);
                        if (IsInSweep(ang, startAng, sweep))
                        {
                            pixels[y * TexRes + x] = ringBg;
                        }
                    }
                }
            }

            // Filled arc up to `norm`
            float filledSweep = sweep * norm;
            for (int y = 0; y < TexRes; y++)
            {
                for (int x = 0; x < TexRes; x++)
                {
                    int dx = x - cx, dy = y - cy;
                    int d2 = dx * dx + dy * dy;
                    if (d2 <= rOuter * rOuter && d2 >= rInner * rInner)
                    {
                        float ang = Mathf.Atan2(dy, dx);
                        if (IsInSweep(ang, startAng, filledSweep))
                        {
                            pixels[y * TexRes + x] = (Color32)active;
                        }
                    }
                }
            }

            // Tick marks every 10%
            for (int i = 0; i <= 10; i++)
            {
                float t = i / 10f;
                float a = startAng + sweep * t;
                DrawLine(pixels, cx, cy, a, rInner - 6, rInner - 1, tickCol);
            }

            // Needle line
            float na = startAng + sweep * norm;
            DrawLine(pixels, cx, cy, na, 0, rInner - 10,
                     new Color32(255, 255, 255, 255));

            _tex.SetPixels32(pixels);
            _tex.Apply(false);
        }

        private static bool IsInSweep(float ang, float start, float sweep)
        {
            // Normalize sweep check to [0..1] along the sweep.
            float rel = Mathf.DeltaAngle(start * Mathf.Rad2Deg, ang * Mathf.Rad2Deg);
            float sweepDeg = sweep * Mathf.Rad2Deg;
            if (sweepDeg > 0) return rel >= 0 && rel <= sweepDeg;
            return rel <= 0 && rel >= sweepDeg;
        }

        private static void DrawLine(Color32[] pixels, int cx, int cy, float ang,
                                      int rStart, int rEnd, Color32 color)
        {
            int steps = Mathf.Abs(rEnd - rStart) * 2 + 2;
            for (int i = 0; i <= steps; i++)
            {
                float t = (float)i / steps;
                float r = Mathf.Lerp(rStart, rEnd, t);
                int x = cx + Mathf.RoundToInt(Mathf.Cos(ang) * r);
                int y = cy + Mathf.RoundToInt(Mathf.Sin(ang) * r);
                if ((uint)x >= TexRes || (uint)y >= TexRes) continue;
                pixels[y * TexRes + x] = color;
                // Thicken slightly
                if (x + 1 < TexRes) pixels[y * TexRes + x + 1] = color;
                if (y + 1 < TexRes) pixels[(y + 1) * TexRes + x] = color;
            }
        }
    }
}
