using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using RcPilot.Core;

namespace RcPilot.UI
{
    /// <summary>
    /// Transient notifications stack at top-center: "ARMED", "BEST LAP 00:42.11",
    /// "LINK LOST — FAILSAFE ENGAGED" etc. Entries auto-fade after 2 s.
    ///
    /// Use: <c>Bootstrapper.Instance.hud.toasts.Show("BEST LAP", UiTheme.Good);</c>
    /// </summary>
    public class Toast : MonoBehaviour
    {
        private struct Entry
        {
            public GameObject go;
            public Text text;
            public Image bg;
            public float tAdded;
            public float lifetime;
        }

        private readonly List<Entry> _entries = new List<Entry>();

        public void Build(Config cfg)
        {
            // Container only — children added as toasts come in.
        }

        public void Show(string msg, Color? color = null, float lifetime = 2.5f)
        {
            var go = new GameObject("Toast", typeof(RectTransform), typeof(Image));
            go.transform.SetParent(transform, false);
            var bg = go.GetComponent<Image>();
            bg.color = color ?? UiTheme.AccentDim;
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(0.5f, 1);
            rt.anchorMax = new Vector2(0.5f, 1);
            rt.pivot = new Vector2(0.5f, 1);
            rt.sizeDelta = new Vector2(600, 52);

            var txt = UiTheme.AddText(go, "t", msg, 28,
                                      TextAnchor.MiddleCenter, UiTheme.Text);
            _entries.Add(new Entry
            {
                go = go, text = txt, bg = bg,
                tAdded = Time.unscaledTime, lifetime = lifetime,
            });
        }

        private void Update()
        {
            float t = Time.unscaledTime;
            // Stack from top of container downward.
            float y = 0;
            for (int i = 0; i < _entries.Count; i++)
            {
                var e = _entries[i];
                float age = t - e.tAdded;
                float alpha = age < e.lifetime - 0.5f ? 1f
                            : Mathf.Clamp01((e.lifetime - age) / 0.5f);
                var bgCol = e.bg.color; bgCol.a = 0.85f * alpha; e.bg.color = bgCol;
                var txCol = e.text.color; txCol.a = alpha; e.text.color = txCol;
                var rt = (RectTransform)e.go.transform;
                rt.anchoredPosition = new Vector2(0, -y);
                y += rt.sizeDelta.y + 8;

                if (age >= e.lifetime)
                {
                    Destroy(e.go);
                    _entries.RemoveAt(i);
                    i--;
                }
            }
        }
    }
}
