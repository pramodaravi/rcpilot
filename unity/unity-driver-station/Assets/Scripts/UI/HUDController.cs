using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Input;
using RcPilot.Network;
using RcPilot.Race;
using RcPilot.Video;

namespace RcPilot.UI
{
    /// <summary>
    /// Top-level HUD. Owns the overlay canvas and the individual widgets. All
    /// widgets subscribe to telemetry / wheel-input events and paint themselves
    /// every frame.
    ///
    /// Layout (1080p reference, scales with CanvasScaler):
    ///   top-left      state badge, RSSI, telemetry age, loss
    ///   top-right     lap timer, best time, lap count
    ///   center-bottom speedometer, throttle/brake bars, steering indicator
    ///   bottom-left   driver name + track name
    ///   bottom-right  leaderboard mini
    ///   top-center    toast stack
    /// </summary>
    public class HUDController : MonoBehaviour
    {
        public Canvas canvas;
        public Speedometer speedo;
        public ThrottleBrakeBar pedalBars;
        public SteeringIndicator steerInd;
        public LapTimer lapTimer;
        public StatusPanel statusPanel;
        public Leaderboard leaderboard;
        public Toast toasts;

        public void Build(Config cfg)
        {
            // Canvas + scaler
            var canvasGO = new GameObject("HUDCanvas");
            canvasGO.transform.SetParent(transform, false);
            canvas = canvasGO.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 100;
            var scaler = canvasGO.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080);
            // Match by HEIGHT (1.0) so the HUD always fits vertically regardless
            // of aspect ratio. Corner-anchored elements stay anchored on wider
            // screens; on narrower (uncommon) the UI scales down. The earlier
            // 0.5 blend caused vertical overflow on shorter-than-1080 viewports
            // (e.g. Game view in Free Aspect at 1.5x scale).
            scaler.matchWidthOrHeight = 1.0f;
            canvasGO.AddComponent<GraphicRaycaster>();

            // Status panel — top-left.
            // Height 200 (was 180): the StatusPanel's bottom Video row sits at
            // topOffset 156 + 28 = 184 internally, so 180 was clipping its last
            // row by 4px. 200 gives 16px of buffer below the last row.
            var statusGO = new GameObject("StatusPanel", typeof(RectTransform));
            statusGO.transform.SetParent(canvas.transform, false);
            statusPanel = statusGO.AddComponent<StatusPanel>();
            statusPanel.Build(cfg);
            Anchor(statusGO.GetComponent<RectTransform>(), new Vector2(0, 1),
                   new Vector2(40, -40), new Vector2(480, 200));

            // Lap timer — top-right
            var lapGO = new GameObject("LapTimer", typeof(RectTransform));
            lapGO.transform.SetParent(canvas.transform, false);
            lapTimer = lapGO.AddComponent<LapTimer>();
            lapTimer.Build(cfg);
            Anchor(lapGO.GetComponent<RectTransform>(), new Vector2(1, 1),
                   new Vector2(-40, -40), new Vector2(520, 210), new Vector2(1, 1));

            // Speedometer — bottom center
            var speedoGO = new GameObject("Speedometer", typeof(RectTransform));
            speedoGO.transform.SetParent(canvas.transform, false);
            speedo = speedoGO.AddComponent<Speedometer>();
            speedo.Build(cfg);
            var speedoRT = speedoGO.GetComponent<RectTransform>();
            speedoRT.anchorMin = new Vector2(0.5f, 0);
            speedoRT.anchorMax = new Vector2(0.5f, 0);
            speedoRT.pivot = new Vector2(0.5f, 0);
            speedoRT.anchoredPosition = new Vector2(0, 60);
            speedoRT.sizeDelta = new Vector2(440, 260);

            // Throttle/brake bars — flanking the speedometer
            var barsGO = new GameObject("PedalBars", typeof(RectTransform));
            barsGO.transform.SetParent(canvas.transform, false);
            pedalBars = barsGO.AddComponent<ThrottleBrakeBar>();
            pedalBars.Build(cfg);
            var barsRT = barsGO.GetComponent<RectTransform>();
            barsRT.anchorMin = new Vector2(0.5f, 0);
            barsRT.anchorMax = new Vector2(0.5f, 0);
            barsRT.pivot = new Vector2(0.5f, 0);
            barsRT.anchoredPosition = new Vector2(0, 40);
            barsRT.sizeDelta = new Vector2(900, 60);

            // Steering indicator — just above the bars
            var steerGO = new GameObject("SteeringInd", typeof(RectTransform));
            steerGO.transform.SetParent(canvas.transform, false);
            steerInd = steerGO.AddComponent<SteeringIndicator>();
            steerInd.Build(cfg);
            var steerRT = steerGO.GetComponent<RectTransform>();
            steerRT.anchorMin = new Vector2(0.5f, 0);
            steerRT.anchorMax = new Vector2(0.5f, 0);
            steerRT.pivot = new Vector2(0.5f, 0);
            steerRT.anchoredPosition = new Vector2(0, 320);
            steerRT.sizeDelta = new Vector2(500, 18);

            // Leaderboard — bottom-right
            var lbGO = new GameObject("Leaderboard", typeof(RectTransform));
            lbGO.transform.SetParent(canvas.transform, false);
            leaderboard = lbGO.AddComponent<Leaderboard>();
            leaderboard.Build(cfg);
            Anchor(lbGO.GetComponent<RectTransform>(), new Vector2(1, 0),
                   new Vector2(-40, 40), new Vector2(460, 260),
                   new Vector2(1, 0));

            // Toasts — top center
            var toastGO = new GameObject("Toasts", typeof(RectTransform));
            toastGO.transform.SetParent(canvas.transform, false);
            toasts = toastGO.AddComponent<Toast>();
            toasts.Build(cfg);
            var toastRT = toastGO.GetComponent<RectTransform>();
            toastRT.anchorMin = new Vector2(0.5f, 1);
            toastRT.anchorMax = new Vector2(0.5f, 1);
            toastRT.pivot = new Vector2(0.5f, 1);
            toastRT.anchoredPosition = new Vector2(0, -120);
            toastRT.sizeDelta = new Vector2(700, 400);

            Log.Info("HUD built");
        }

        private static void Anchor(RectTransform rt, Vector2 anchor,
                                   Vector2 pos, Vector2 size)
        {
            Anchor(rt, anchor, pos, size, new Vector2(0, 1));
        }

        private static void Anchor(RectTransform rt, Vector2 anchor,
                                   Vector2 pos, Vector2 size, Vector2 pivot)
        {
            rt.anchorMin = anchor;
            rt.anchorMax = anchor;
            rt.pivot = pivot;
            rt.anchoredPosition = pos;
            rt.sizeDelta = size;
        }
    }
}
