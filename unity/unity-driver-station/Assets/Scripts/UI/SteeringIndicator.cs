using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;

namespace RcPilot.UI
{
    /// <summary>
    /// Thin horizontal bar with a sliding tick representing steering position.
    /// Center = 0, left = -1, right = +1. Uses wheel state for driver-latency
    /// feedback (PWM feedback is in the status panel / speedo via telemetry).
    /// </summary>
    public class SteeringIndicator : MonoBehaviour
    {
        private RectTransform _tickRT;

        public void Build(Config cfg)
        {
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            // Center line
            var centerGO = new GameObject("Center", typeof(RectTransform), typeof(Image));
            centerGO.transform.SetParent(transform, false);
            centerGO.GetComponent<Image>().color = new Color(0.3f, 0.35f, 0.4f, 1f);
            var crt = centerGO.GetComponent<RectTransform>();
            crt.anchorMin = new Vector2(0.5f, 0); crt.anchorMax = new Vector2(0.5f, 1);
            crt.pivot = new Vector2(0.5f, 0.5f);
            crt.sizeDelta = new Vector2(2, 0);

            // Tick
            var tickGO = new GameObject("Tick", typeof(RectTransform), typeof(Image));
            tickGO.transform.SetParent(transform, false);
            tickGO.GetComponent<Image>().color = UiTheme.Accent;
            _tickRT = tickGO.GetComponent<RectTransform>();
            _tickRT.anchorMin = new Vector2(0.5f, 0);
            _tickRT.anchorMax = new Vector2(0.5f, 1);
            _tickRT.pivot = new Vector2(0.5f, 0.5f);
            _tickRT.sizeDelta = new Vector2(12, 4);
            _tickRT.anchoredPosition = Vector2.zero;
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null || boot.wheelInput == null) return;
            var parentRT = (RectTransform)transform;
            float halfW = parentRT.rect.width * 0.5f - 8;
            _tickRT.anchoredPosition = new Vector2(
                boot.wheelInput.state.steering * halfW, 0);
        }
    }
}
