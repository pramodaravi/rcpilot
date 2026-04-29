using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Race;

namespace RcPilot.UI
{
    /// <summary>
    /// Top-right lap timer. Shows:
    ///   big current lap time (live, 0.01 s precision)
    ///   previous lap time (colored vs best)
    ///   best lap time
    ///   lap count / target
    /// </summary>
    public class LapTimer : MonoBehaviour
    {
        private Text _bigTime;
        private Text _prevLap;
        private Text _bestLap;
        private Text _lapCount;

        public void Build(Config cfg)
        {
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            // Big current lap time
            var bigGO = new GameObject("Big", typeof(RectTransform));
            bigGO.transform.SetParent(transform, false);
            var brt = bigGO.GetComponent<RectTransform>();
            brt.anchorMin = new Vector2(0, 0.55f);
            brt.anchorMax = new Vector2(1, 1);
            brt.offsetMin = Vector2.zero; brt.offsetMax = Vector2.zero;
            _bigTime = UiTheme.AddText(bigGO, "t", "--:--.--", 56,
                                       TextAnchor.MiddleCenter, UiTheme.Accent);

            // Bottom strip: prev | best | count
            var prevGO = MakeLowerCell("Prev", 0f, 0.33f);
            _prevLap = UiTheme.AddText(prevGO, "t", "PREV\n--:--.--", 16,
                                       TextAnchor.MiddleCenter, UiTheme.TextDim);

            var bestGO = MakeLowerCell("Best", 0.33f, 0.66f);
            _bestLap = UiTheme.AddText(bestGO, "t", "BEST\n--:--.--", 16,
                                       TextAnchor.MiddleCenter, UiTheme.Good);

            var countGO = MakeLowerCell("Count", 0.66f, 1f);
            _lapCount = UiTheme.AddText(countGO, "t", "LAP\n0/5", 16,
                                        TextAnchor.MiddleCenter, UiTheme.TextDim);
        }

        private GameObject MakeLowerCell(string name, float xMin, float xMax)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(transform, false);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(xMin, 0);
            rt.anchorMax = new Vector2(xMax, 0.5f);
            rt.offsetMin = Vector2.zero; rt.offsetMax = Vector2.zero;
            return go;
        }

        private void Update()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null || boot.race == null) return;
            var race = boot.race;

            _bigTime.text = Fmt(race.CurrentLapTime);
            _bigTime.color = race.IsRacing ? UiTheme.Accent : UiTheme.TextDim;

            _prevLap.text = $"PREV\n{(race.LastLapTime > 0 ? Fmt(race.LastLapTime) : "--:--.--")}";
            _prevLap.color = race.LastLapWasBest ? UiTheme.Good : UiTheme.TextDim;

            _bestLap.text = $"BEST\n{(race.BestLapTime > 0 ? Fmt(race.BestLapTime) : "--:--.--")}";

            _lapCount.text = $"LAP\n{race.LapIndex}/{boot.config.race.targetLapCount}";
        }

        public static string Fmt(float t)
        {
            if (t < 0) return "--:--.--";
            int mins = (int)(t / 60f);
            float rem = t - mins * 60f;
            return $"{mins:00}:{rem:00.00}";
        }
    }
}
