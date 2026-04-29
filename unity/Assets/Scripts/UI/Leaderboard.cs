using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;
using RcPilot.Race;

namespace RcPilot.UI
{
    /// <summary>
    /// Bottom-right leaderboard. In v0.1 this is a local best-times list
    /// (one entry per driver name), loaded/saved via BestTimeStore. Once we
    /// wire the pilot lobby up to a backend, this becomes a venue-wide ladder.
    /// </summary>
    public class Leaderboard : MonoBehaviour
    {
        private Text _header;
        private readonly List<Text> _rows = new List<Text>();
        private float _nextRefresh;

        public void Build(Config cfg)
        {
            UiTheme.AddPanel(gameObject, UiTheme.Bg);

            var headGO = new GameObject("Head", typeof(RectTransform));
            headGO.transform.SetParent(transform, false);
            var hrt = headGO.GetComponent<RectTransform>();
            hrt.anchorMin = new Vector2(0, 1); hrt.anchorMax = new Vector2(1, 1);
            hrt.pivot = new Vector2(0.5f, 1);
            hrt.anchoredPosition = new Vector2(0, -10);
            hrt.sizeDelta = new Vector2(-20, 34);
            _header = UiTheme.AddText(headGO, "t", "LEADERBOARD", 20,
                                      TextAnchor.MiddleLeft, UiTheme.Accent);

            // 6 rows
            for (int i = 0; i < 6; i++)
            {
                var rowGO = new GameObject($"Row{i}", typeof(RectTransform));
                rowGO.transform.SetParent(transform, false);
                var rrt = rowGO.GetComponent<RectTransform>();
                rrt.anchorMin = new Vector2(0, 1); rrt.anchorMax = new Vector2(1, 1);
                rrt.pivot = new Vector2(0.5f, 1);
                rrt.anchoredPosition = new Vector2(0, -48 - i * 32);
                rrt.sizeDelta = new Vector2(-20, 30);
                var t = UiTheme.AddText(rowGO, "t", "", 18,
                                        TextAnchor.MiddleLeft, UiTheme.TextDim);
                _rows.Add(t);
            }
        }

        private void Update()
        {
            if (Time.unscaledTime < _nextRefresh) return;
            _nextRefresh = Time.unscaledTime + 1f;
            Repaint();
        }

        private void Repaint()
        {
            var boot = Bootstrapper.Instance;
            if (boot == null || boot.race == null) return;
            var entries = boot.race.bestTimes.AllSorted();
            for (int i = 0; i < _rows.Count; i++)
            {
                if (i >= entries.Count)
                {
                    _rows[i].text = "";
                    continue;
                }
                var e = entries[i];
                string driverHighlight = e.driver == boot.config.race.driverName ? ">" : " ";
                _rows[i].text = $"{driverHighlight} {i + 1}. {e.driver,-18} {LapTimer.Fmt(e.lapSeconds)}";
                _rows[i].color = i == 0 ? UiTheme.Accent
                                : e.driver == boot.config.race.driverName ? UiTheme.Good
                                : UiTheme.TextDim;
            }
        }
    }
}
