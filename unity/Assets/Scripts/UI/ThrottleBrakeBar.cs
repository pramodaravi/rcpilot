using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;

namespace RcPilot.UI
{
    /// <summary>
    /// Throttle fills left-to-right green; brake fills right-to-left red.
    /// Input is taken from the live WheelState, not telemetry — gives the
    /// driver zero-latency feedback of their foot, separate from what the
    /// car's PWM actually did.
    /// </summary>
    public class ThrottleBrakeBar : MonoBehaviour
    {
        private Image _thrFill;
        private Image _brkFill;
        private Text _thrLabel;
        private Text _brkLabel;

        public void Build(Config cfg)
        {
            // Background panel
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            // Throttle bar (left half, fill right)
            var thrGO = new GameObject("Thr", typeof(RectTransform));
            thrGO.transform.SetParent(transform, false);
            var trt = thrGO.GetComponent<RectTransform>();
            trt.anchorMin = new Vector2(0, 0.5f);
            trt.anchorMax = new Vector2(0.5f, 0.5f);
            trt.pivot = new Vector2(0.5f, 0.5f);
            trt.offsetMin = new Vector2(16, -20);
            trt.offsetMax = new Vector2(-10, 20);

            var thrBg = new GameObject("Bg", typeof(RectTransform), typeof(Image));
            thrBg.transform.SetParent(thrGO.transform, false);
            thrBg.GetComponent<Image>().color = new Color(0.05f, 0.12f, 0.08f, 0.8f);
            var tbgRT = thrBg.GetComponent<RectTransform>();
            tbgRT.anchorMin = Vector2.zero; tbgRT.anchorMax = Vector2.one;
            tbgRT.offsetMin = Vector2.zero; tbgRT.offsetMax = Vector2.zero;

            _thrFill = UiTheme.AddFilledBar(thrGO, "Fill", UiTheme.Good);
            _thrFill.fillOrigin = (int)Image.OriginHorizontal.Right;
            var tfRT = _thrFill.rectTransform;
            tfRT.anchorMin = Vector2.zero; tfRT.anchorMax = Vector2.one;
            tfRT.offsetMin = new Vector2(2, 2); tfRT.offsetMax = new Vector2(-2, -2);

            _thrLabel = UiTheme.AddText(thrGO, "lbl", "THR 0%", 18,
                                        TextAnchor.MiddleLeft, UiTheme.Text);
            _thrLabel.rectTransform.offsetMin = new Vector2(10, 0);

            // Brake bar (right half, fill left)
            var brkGO = new GameObject("Brk", typeof(RectTransform));
            brkGO.transform.SetParent(transform, false);
            var brt = brkGO.GetComponent<RectTransform>();
            brt.anchorMin = new Vector2(0.5f, 0.5f);
            brt.anchorMax = new Vector2(1, 0.5f);
            brt.pivot = new Vector2(0.5f, 0.5f);
            brt.offsetMin = new Vector2(10, -20);
            brt.offsetMax = new Vector2(-16, 20);

            var brkBg = new GameObject("Bg", typeof(RectTransform), typeof(Image));
            brkBg.transform.SetParent(brkGO.transform, false);
            brkBg.GetComponent<Image>().color = new Color(0.15f, 0.05f, 0.05f, 0.8f);
            var bbgRT = brkBg.GetComponent<RectTransform>();
            bbgRT.anchorMin = Vector2.zero; bbgRT.anchorMax = Vector2.one;
            bbgRT.offsetMin = Vector2.zero; bbgRT.offsetMax = Vector2.zero;

            _brkFill = UiTheme.AddFilledBar(brkGO, "Fill", UiTheme.Bad);
            _brkFill.fillOrigin = (int)Image.OriginHorizontal.Left;
            var bfRT = _brkFill.rectTransform;
            bfRT.anchorMin = Vector2.zero; bfRT.anchorMax = Vector2.one;
            bfRT.offsetMin = new Vector2(2, 2); bfRT.offsetMax = new Vector2(-2, -2);

            _brkLabel = UiTheme.AddText(brkGO, "lbl", "BRK 0%", 18,
                                        TextAnchor.MiddleRight, UiTheme.Text);
            _brkLabel.rectTransform.offsetMax = new Vector2(-10, 0);
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null || boot.wheelInput == null) return;
            var s = boot.wheelInput.state;
            _thrFill.fillAmount = s.throttle;
            _brkFill.fillAmount = s.brake;
            _thrLabel.text = $"THR {Mathf.RoundToInt(s.throttle * 100)}%";
            _brkLabel.text = $"BRK {Mathf.RoundToInt(s.brake * 100)}%";
        }
    }
}
