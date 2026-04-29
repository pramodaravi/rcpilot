using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Race;

namespace RcPilot.UI
{
    /// <summary>
    /// Post-race summary screen. Auto-shows when RaceManager.OnRaceFinished
    /// fires, auto-hides after 8 s or on a button press.
    /// </summary>
    public class ResultsScreen : MonoBehaviour
    {
        public Canvas canvas;
        private Text _bestText;
        private Text _splitsText;
        private float _hideAt;

        public void Build(Config cfg)
        {
            var canvasGO = new GameObject("ResultsCanvas");
            canvasGO.transform.SetParent(transform, false);
            canvas = canvasGO.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 400;
            var scaler = canvasGO.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080);
            canvasGO.AddComponent<GraphicRaycaster>();

            UiTheme.AddPanel(canvasGO, new Color(0, 0, 0, 0.6f));

            var box = new GameObject("Box", typeof(RectTransform), typeof(Image));
            box.transform.SetParent(canvasGO.transform, false);
            box.GetComponent<Image>().color = UiTheme.BgSolid;
            var rt = box.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(0.5f, 0.5f);
            rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(760, 520);

            // Title
            var titleGO = new GameObject("t", typeof(RectTransform));
            titleGO.transform.SetParent(box.transform, false);
            var trt = titleGO.GetComponent<RectTransform>();
            trt.anchorMin = new Vector2(0, 1); trt.anchorMax = new Vector2(1, 1);
            trt.pivot = new Vector2(0.5f, 1);
            trt.anchoredPosition = new Vector2(0, -30);
            trt.sizeDelta = new Vector2(-40, 80);
            UiTheme.AddText(titleGO, "t", "RACE COMPLETE", 36,
                            TextAnchor.MiddleCenter, UiTheme.Accent);

            var bestGO = new GameObject("best", typeof(RectTransform));
            bestGO.transform.SetParent(box.transform, false);
            var brt = bestGO.GetComponent<RectTransform>();
            brt.anchorMin = new Vector2(0, 1); brt.anchorMax = new Vector2(1, 1);
            brt.pivot = new Vector2(0.5f, 1);
            brt.anchoredPosition = new Vector2(0, -130);
            brt.sizeDelta = new Vector2(-40, 80);
            _bestText = UiTheme.AddText(bestGO, "t", "BEST LAP —",
                                        54, TextAnchor.MiddleCenter, UiTheme.Good);

            var splitsGO = new GameObject("splits", typeof(RectTransform));
            splitsGO.transform.SetParent(box.transform, false);
            var srt = splitsGO.GetComponent<RectTransform>();
            srt.anchorMin = new Vector2(0, 0); srt.anchorMax = new Vector2(1, 1);
            srt.offsetMin = new Vector2(40, 40); srt.offsetMax = new Vector2(-40, -230);
            _splitsText = UiTheme.AddText(splitsGO, "t", "",
                                          22, TextAnchor.UpperCenter, UiTheme.TextDim);

            canvas.enabled = false;
        }

        public void Show(RaceManager race)
        {
            canvas.enabled = true;
            _hideAt = Time.unscaledTime + 10f;

            _bestText.text = $"BEST LAP  {LapTimer.Fmt(race.BestLapTime)}";

            // Build splits string from the ghost's finalized best lap if
            // we captured it, else just reiterate the best.
            var sb = new System.Text.StringBuilder();
            sb.AppendLine($"Driver: {Bootstrapper.Instance.config.race.driverName}");
            sb.AppendLine($"Track:  {Bootstrapper.Instance.config.race.trackName}");
            sb.AppendLine();
            sb.AppendLine($"Laps completed: {race.LapIndex}");
            sb.AppendLine($"Last lap:       {LapTimer.Fmt(race.LastLapTime)}");
            sb.AppendLine($"Best lap:       {LapTimer.Fmt(race.BestLapTime)}");
            sb.AppendLine();
            sb.AppendLine("(any button / ESC to dismiss)");
            _splitsText.text = sb.ToString();
        }

        public void Hide() { canvas.enabled = false; }

        private void Update()
        {
            if (!canvas.enabled) return;
            if (Time.unscaledTime >= _hideAt) { Hide(); return; }
            if (UnityEngine.Input.anyKeyDown) Hide();
        }
    }
}
