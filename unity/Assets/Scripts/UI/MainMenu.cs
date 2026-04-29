using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;

namespace RcPilot.UI
{
    /// <summary>
    /// Pre-race menu overlay. Built procedurally the first time the player
    /// presses Escape or when the app starts in menu mode.
    ///
    /// Entries:
    ///   START TIME TRIAL  (arms countdown)
    ///   DRIVER: <name>    (inline edit via keyboard)
    ///   TOGGLE HUD
    ///   VIEW LEADERBOARD  (no-op; LB is always visible)
    ///   QUIT
    /// </summary>
    public class MainMenu : MonoBehaviour
    {
        public Canvas canvas;
        public InputField driverField;
        public Text footerHint;

        public void Build(Config cfg)
        {
            var canvasGO = new GameObject("MenuCanvas");
            canvasGO.transform.SetParent(transform, false);
            canvas = canvasGO.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 500;
            var scaler = canvasGO.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080);
            canvasGO.AddComponent<GraphicRaycaster>();

            // Dim backdrop
            var dim = UiTheme.AddPanel(canvasGO, new Color(0, 0, 0, 0.65f));

            // Menu box
            var boxGO = new GameObject("Box", typeof(RectTransform), typeof(Image));
            boxGO.transform.SetParent(canvasGO.transform, false);
            boxGO.GetComponent<Image>().color = UiTheme.BgSolid;
            var rt = boxGO.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(0.5f, 0.5f);
            rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(680, 620);

            // Title
            var titleGO = new GameObject("Title", typeof(RectTransform));
            titleGO.transform.SetParent(boxGO.transform, false);
            var trt = titleGO.GetComponent<RectTransform>();
            trt.anchorMin = new Vector2(0, 1); trt.anchorMax = new Vector2(1, 1);
            trt.pivot = new Vector2(0.5f, 1);
            trt.anchoredPosition = new Vector2(0, -30);
            trt.sizeDelta = new Vector2(-40, 80);
            UiTheme.AddText(titleGO, "t", "RC PILOT — DRIVER STATION",
                            34, TextAnchor.MiddleCenter, UiTheme.Accent);

            var subGO = new GameObject("Sub", typeof(RectTransform));
            subGO.transform.SetParent(boxGO.transform, false);
            var srt = subGO.GetComponent<RectTransform>();
            srt.anchorMin = new Vector2(0, 1); srt.anchorMax = new Vector2(1, 1);
            srt.pivot = new Vector2(0.5f, 1);
            srt.anchoredPosition = new Vector2(0, -110);
            srt.sizeDelta = new Vector2(-40, 40);
            UiTheme.AddText(subGO, "t", cfg.race.trackName,
                            20, TextAnchor.MiddleCenter, UiTheme.TextDim);

            // Driver field
            driverField = MakeDriverField(boxGO, cfg, -200);

            // Buttons
            MakeButton(boxGO, "START TIME TRIAL", -290,
                       () => {
                           Bootstrapper.Instance?.race?.StartCountdown();
                           Hide();
                       });
            MakeButton(boxGO, "TOGGLE HUD", -360,
                       () => {
                           var hc = Bootstrapper.Instance?.hud;
                           if (hc != null && hc.canvas != null)
                               hc.canvas.enabled = !hc.canvas.enabled;
                       });
            MakeButton(boxGO, "SWAP MAIN CAMERA", -430,
                       () => Bootstrapper.Instance?.cockpit?.ToggleMain());
            MakeButton(boxGO, "RESUME", -500,
                       () => Hide());
            MakeButton(boxGO, "QUIT", -560,
                       () => Application.Quit());

            // Footer hint
            var footGO = new GameObject("Foot", typeof(RectTransform));
            footGO.transform.SetParent(boxGO.transform, false);
            var frt = footGO.GetComponent<RectTransform>();
            frt.anchorMin = new Vector2(0, 0); frt.anchorMax = new Vector2(1, 0);
            frt.pivot = new Vector2(0.5f, 0);
            frt.anchoredPosition = new Vector2(0, 16);
            frt.sizeDelta = new Vector2(-40, 30);
            footerHint = UiTheme.AddText(footGO, "t",
                "ESC open/close · L lap mark · C swap camera · SPACE estop",
                16, TextAnchor.MiddleCenter, UiTheme.TextDim);

            Hide();
        }

        private InputField MakeDriverField(GameObject parent, Config cfg, float yOffset)
        {
            var rowGO = new GameObject("DriverRow", typeof(RectTransform));
            rowGO.transform.SetParent(parent.transform, false);
            var rrt = rowGO.GetComponent<RectTransform>();
            rrt.anchorMin = new Vector2(0.5f, 1);
            rrt.anchorMax = new Vector2(0.5f, 1);
            rrt.pivot = new Vector2(0.5f, 1);
            rrt.anchoredPosition = new Vector2(0, yOffset);
            rrt.sizeDelta = new Vector2(540, 56);

            UiTheme.AddText(rowGO, "lbl", "DRIVER NAME", 18,
                            TextAnchor.UpperLeft, UiTheme.TextDim)
                   .rectTransform.anchoredPosition = new Vector2(6, 0);

            var fieldGO = new GameObject("Field", typeof(RectTransform),
                                         typeof(Image), typeof(InputField));
            fieldGO.transform.SetParent(rowGO.transform, false);
            fieldGO.GetComponent<Image>().color = new Color(0.08f, 0.1f, 0.13f, 1f);
            var frt = fieldGO.GetComponent<RectTransform>();
            frt.anchorMin = Vector2.zero; frt.anchorMax = Vector2.one;
            frt.offsetMin = new Vector2(0, 0); frt.offsetMax = new Vector2(0, -22);

            var textGO = new GameObject("Text", typeof(RectTransform), typeof(Text));
            textGO.transform.SetParent(fieldGO.transform, false);
            var t = textGO.GetComponent<Text>();
            t.font = UiTheme.DefaultFont();
            t.fontSize = 22;
            t.color = UiTheme.Text;
            t.alignment = TextAnchor.MiddleLeft;
            var textRT = textGO.GetComponent<RectTransform>();
            textRT.anchorMin = Vector2.zero; textRT.anchorMax = Vector2.one;
            textRT.offsetMin = new Vector2(10, 0); textRT.offsetMax = new Vector2(-10, 0);

            var phGO = new GameObject("Placeholder", typeof(RectTransform), typeof(Text));
            phGO.transform.SetParent(fieldGO.transform, false);
            var ph = phGO.GetComponent<Text>();
            ph.font = UiTheme.DefaultFont();
            ph.fontSize = 22;
            ph.color = UiTheme.TextDim;
            ph.alignment = TextAnchor.MiddleLeft;
            ph.text = "Driver 1";
            var phRT = phGO.GetComponent<RectTransform>();
            phRT.anchorMin = Vector2.zero; phRT.anchorMax = Vector2.one;
            phRT.offsetMin = new Vector2(10, 0); phRT.offsetMax = new Vector2(-10, 0);

            var f = fieldGO.GetComponent<InputField>();
            f.textComponent = t;
            f.placeholder = ph;
            f.text = cfg.race.driverName;
            f.onEndEdit.AddListener(v =>
            {
                if (!string.IsNullOrWhiteSpace(v))
                {
                    cfg.race.driverName = v.Trim();
                    Log.Info($"Driver renamed → {cfg.race.driverName}");
                }
            });
            return f;
        }

        private void MakeButton(GameObject parent, string label, float yOffset,
                                System.Action onClick)
        {
            var btnGO = new GameObject(label, typeof(RectTransform),
                                       typeof(Image), typeof(Button));
            btnGO.transform.SetParent(parent.transform, false);
            var img = btnGO.GetComponent<Image>();
            img.color = UiTheme.AccentDim;
            var rt = btnGO.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(0.5f, 1);
            rt.anchorMax = new Vector2(0.5f, 1);
            rt.pivot = new Vector2(0.5f, 1);
            rt.anchoredPosition = new Vector2(0, yOffset);
            rt.sizeDelta = new Vector2(540, 52);

            UiTheme.AddText(btnGO, "lbl", label, 22,
                            TextAnchor.MiddleCenter, UiTheme.Text);

            var b = btnGO.GetComponent<Button>();
            b.targetGraphic = img;
            var cb = b.colors;
            cb.normalColor = UiTheme.AccentDim;
            cb.highlightedColor = UiTheme.Accent;
            cb.pressedColor = UiTheme.AccentDim * 0.8f;
            cb.selectedColor = UiTheme.Accent;
            b.colors = cb;
            b.onClick.AddListener(() => onClick?.Invoke());
        }

        public void Show()
        {
            canvas.enabled = true;
            Time.timeScale = 1f; // we pause via a game-state flag, not timeScale —
                                 // because network + telemetry still need to run.
        }

        public void Hide()
        {
            canvas.enabled = false;
        }

        public void Toggle()
        {
            if (canvas.enabled) Hide(); else Show();
        }

        private void Update()
        {
            if (UnityEngine.Input.GetKeyDown(KeyCode.Escape))
            {
                Toggle();
            }
        }
    }
}
