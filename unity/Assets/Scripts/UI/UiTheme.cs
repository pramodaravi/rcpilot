using UnityEngine;
using UnityEngine.UI;

namespace RcPilot.UI
{
    /// <summary>
    /// Shared color palette and simple widget helpers so every piece of HUD
    /// gets the same blue-accent sim-racer look without needing Unity's
    /// theming system.
    /// </summary>
    public static class UiTheme
    {
        public static readonly Color Bg       = new Color(0f, 0f, 0f, 0.62f);
        public static readonly Color BgSolid  = new Color(0.03f, 0.04f, 0.05f, 0.9f);
        public static readonly Color Accent   = new Color(0.0f, 0.78f, 1.0f, 1f);
        public static readonly Color AccentDim= new Color(0.0f, 0.5f, 0.8f, 1f);
        public static readonly Color Good     = new Color(0.2f, 0.95f, 0.4f, 1f);
        public static readonly Color Warn     = new Color(1.0f, 0.75f, 0.0f, 1f);
        public static readonly Color Bad      = new Color(1.0f, 0.2f, 0.2f, 1f);
        public static readonly Color TextDim  = new Color(0.75f, 0.82f, 0.88f, 1f);
        public static readonly Color Text     = new Color(1f, 1f, 1f, 1f);

        public static Font DefaultFont()
        {
            // Unity ships "LegacyRuntime.ttf" (Arial replacement) with the engine
            // so we don't need TMP asset authoring.
            Font f = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (f == null) f = Font.CreateDynamicFontFromOSFont("Arial", 16);
            return f;
        }

        public static Image AddPanel(GameObject parent, Color color)
        {
            var go = new GameObject("Panel", typeof(RectTransform), typeof(Image));
            go.transform.SetParent(parent.transform, false);
            var img = go.GetComponent<Image>();
            img.color = color;
            img.sprite = GetWhiteSprite();
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            return img;
        }

        private static Sprite _whiteSprite;
        /// <summary>
        /// Returns a tiny solid-white Sprite, created once. Plain
        /// <c>GameObject.AddComponent&lt;Image&gt;()</c> doesn't render anything
        /// without a Sprite — this is the cheapest way to get a colored rect.
        /// </summary>
        public static Sprite GetWhiteSprite()
        {
            if (_whiteSprite != null) return _whiteSprite;
            var tex = new Texture2D(4, 4, TextureFormat.RGBA32, false);
            tex.hideFlags = HideFlags.HideAndDontSave;
            var px = new Color32[16];
            for (int i = 0; i < 16; i++) px[i] = new Color32(255, 255, 255, 255);
            tex.SetPixels32(px);
            tex.Apply();
            _whiteSprite = Sprite.Create(tex, new Rect(0, 0, 4, 4), Vector2.one * 0.5f);
            _whiteSprite.hideFlags = HideFlags.HideAndDontSave;
            return _whiteSprite;
        }

        public static Text AddText(GameObject parent, string name, string value,
                                   int fontSize, TextAnchor anchor, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            go.transform.SetParent(parent.transform, false);
            var t = go.GetComponent<Text>();
            t.font = DefaultFont();
            t.text = value;
            t.fontSize = fontSize;
            t.alignment = anchor;
            t.color = color;
            t.horizontalOverflow = HorizontalWrapMode.Overflow;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            return t;
        }

        public static RawImage AddRawImage(GameObject parent, string name, Texture tex)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(RawImage));
            go.transform.SetParent(parent.transform, false);
            var img = go.GetComponent<RawImage>();
            img.texture = tex;
            return img;
        }

        public static Image AddFilledBar(GameObject parent, string name, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image));
            go.transform.SetParent(parent.transform, false);
            var img = go.GetComponent<Image>();
            img.sprite = GetWhiteSprite();
            img.type = Image.Type.Filled;
            img.fillMethod = Image.FillMethod.Horizontal;
            img.fillOrigin = (int)Image.OriginHorizontal.Left;
            img.fillAmount = 0f;
            img.color = color;
            return img;
        }
    }
}
