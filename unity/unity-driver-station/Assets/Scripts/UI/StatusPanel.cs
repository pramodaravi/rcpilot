using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Network;

namespace RcPilot.UI
{
    /// <summary>
    /// Top-left "car status" HUD block. Displays:
    ///   - car-side state badge (idle / armed / running / ESTOP / fault)
    ///   - WiFi RSSI with color coding
    ///   - telemetry age (how long since last packet; color codes >50ms red)
    ///   - packet loss %
    ///   - video bridge connection state (cam0 / cam1 age)
    /// </summary>
    public class StatusPanel : MonoBehaviour
    {
        private Text _stateText;
        private Image _stateBadge;
        private Text _rssiText;
        private Text _ageText;
        private Text _lossText;
        private Text _videoText;

        public void Build(Config cfg)
        {
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            // Layout: state big, then 4 small rows.
            _stateBadge = MakeRow("StateBadge", 0, UiTheme.AccentDim, 62);
            _stateText  = UiTheme.AddText(_stateBadge.gameObject, "txt", "IDLE", 40,
                                          TextAnchor.MiddleCenter, UiTheme.Text);

            _rssiText  = MakeRowText("RSSI",  66, "WiFi: --  dBm");
            _ageText   = MakeRowText("Age",   96, "telem: -- ms");
            _lossText  = MakeRowText("Loss",  126, "loss: --%");
            _videoText = MakeRowText("Video", 156, "video: cam0 -- ms | cam1 -- ms");
        }

        private Image MakeRow(string name, float topOffset, Color color, float height)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image));
            go.transform.SetParent(transform, false);
            var img = go.GetComponent<Image>();
            img.color = color;
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(0, 1);
            rt.anchorMax = new Vector2(1, 1);
            rt.pivot = new Vector2(0.5f, 1);
            rt.anchoredPosition = new Vector2(0, -topOffset);
            rt.sizeDelta = new Vector2(-16, height);
            return img;
        }

        private Text MakeRowText(string name, float topOffset, string initial)
        {
            var row = MakeRow(name, topOffset, new Color(0, 0, 0, 0), 28);
            return UiTheme.AddText(row.gameObject, "t", initial, 20,
                                   TextAnchor.MiddleLeft, UiTheme.TextDim);
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null) return;
            var telem = boot.telemetry;
            var cam0 = boot.cam0;
            var cam1 = boot.cam1;

            // State
            if (telem != null && telem.HasPacket)
            {
                var s = telem.Latest.state;
                _stateText.text = Protocol.StateName(s).ToUpper();
                _stateBadge.color = StateColor(s);
            }
            else
            {
                _stateText.text = "NO LINK";
                _stateBadge.color = UiTheme.Bad;
            }

            // RSSI
            if (telem != null && telem.HasPacket)
            {
                int dbm = -telem.Latest.wifiRssiNeg;
                _rssiText.text = $"WiFi: {dbm} dBm";
                _rssiText.color = dbm > -55 ? UiTheme.Good
                                : dbm > -70 ? UiTheme.Warn
                                : UiTheme.Bad;
            }

            // Telemetry age
            if (telem != null)
            {
                float age = telem.AgeMs;
                _ageText.text = $"telem: {age,4:0} ms   ({telem.SmoothedHz:0.0} Hz)";
                _ageText.color = age < 30 ? UiTheme.Good
                               : age < 80 ? UiTheme.Warn
                               : UiTheme.Bad;
            }

            // Loss
            if (telem != null && telem.HasPacket)
            {
                int loss = telem.Latest.pktLossPct;
                _lossText.text = $"loss: {loss}%";
                _lossText.color = loss < 1 ? UiTheme.Good
                                : loss < 5 ? UiTheme.Warn
                                : UiTheme.Bad;
            }

            // Video
            float ageC0 = cam0 != null ? cam0.AgeMs : 9999f;
            float ageC1 = cam1 != null ? cam1.AgeMs : 9999f;
            _videoText.text = $"video: cam0 {Format(ageC0)} | cam1 {Format(ageC1)}";
            _videoText.color = ageC0 < 150 && ageC1 < 150 ? UiTheme.Good
                             : ageC0 < 500 && ageC1 < 500 ? UiTheme.Warn
                             : UiTheme.Bad;
        }

        private static string Format(float ageMs) =>
            ageMs > 2500 ? "—" : $"{ageMs:0} ms";

        private static Color StateColor(byte s)
        {
            switch (s)
            {
                case TelemetryStates.STATE_IDLE:    return UiTheme.AccentDim;
                case TelemetryStates.STATE_ARMED:   return UiTheme.Warn;
                case TelemetryStates.STATE_RUNNING: return UiTheme.Good;
                case TelemetryStates.STATE_ESTOP:   return UiTheme.Bad;
                case TelemetryStates.STATE_FAULT:   return UiTheme.Bad;
                default: return UiTheme.AccentDim;
            }
        }
    }
}
