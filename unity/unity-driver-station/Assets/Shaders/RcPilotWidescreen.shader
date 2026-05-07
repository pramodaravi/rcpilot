// rcpilot widescreen — composite two camera textures into one seamless Quad.
//
// IMPORTANT vs v1: the screen shows only the OUTER portion of each camera so
// parallel-mounted cameras don't produce a duplicated middle. Specifically:
//
//   _CamCoverage = 0.5 (default): left screen half shows cam0's LEFT half,
//                                   right screen half shows cam1's RIGHT half.
//                                   The middle of each camera is cropped away.
//                                   Use this when cameras point in the same
//                                   direction — gives a "panoramic" stitch
//                                   without obvious doubling.
//
//   _CamCoverage = 1.0          : each half of screen shows the FULL camera.
//                                   Use only if the cameras are toed out and
//                                   their views barely overlap.
//
// A linear alpha blend across _BlendWidth around the seam hides any residual
// discontinuity.
//
// Properties:
//   _LeftTex      — Texture2D blitted by VideoBridgeClient cam0
//   _RightTex     — Texture2D blitted by VideoBridgeClient cam1
//   _CamCoverage  — fraction of each camera shown on its half of the screen
//                   (0.3 .. 1.0). Lower = more aggressive crop = less duplication.
//   _BlendWidth   — half-width of the seam blend in UV (0.05 = 10% of screen).
//   _Brightness   — overall multiplier for exposure-matching.
//   _SeamShift    — moves the seam left/right in UV space.

Shader "RcPilot/Widescreen2Cam"
{
    Properties
    {
        _LeftTex     ("Left Camera (cam0)",  2D) = "black" {}
        _RightTex    ("Right Camera (cam1)", 2D) = "black" {}
        _CamCoverage ("Per-Cam Coverage",    Range(0.3, 1.0))   = 0.5
        _BlendWidth  ("Blend Half-Width",    Range(0.0, 0.25))  = 0.05
        _Brightness  ("Brightness",          Range(0.0, 2.0))   = 1.0
        _SeamShift   ("Seam Shift",          Range(-0.25, 0.25)) = 0.0
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" "Queue" = "Geometry" }
        LOD 100
        Cull Off
        ZWrite On

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv     : TEXCOORD0;
            };

            struct v2f
            {
                float2 uv     : TEXCOORD0;
                float4 vertex : SV_POSITION;
            };

            sampler2D _LeftTex;
            sampler2D _RightTex;
            float _CamCoverage;
            float _BlendWidth;
            float _Brightness;
            float _SeamShift;

            v2f vert(appdata v)
            {
                v2f o;
                o.vertex = UnityObjectToClipPos(v.vertex);
                o.uv     = v.uv;
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                float seam = 0.5 + _SeamShift;
                // Each side of the screen samples ONLY a slice of the
                // corresponding camera (controlled by _CamCoverage). Default
                // 0.5 means left screen half = LEFT half of cam0, right
                // screen half = RIGHT half of cam1. Together: a stitched view
                // with the inner portion of each camera dropped, killing the
                // duplicate-middle problem when cameras point in the same
                // direction.
                float coverage = _CamCoverage;
                // Left:  map screen u in [0..seam] -> cam0 u in [0..coverage]
                float uL = saturate(i.uv.x / seam) * coverage;
                // Right: map screen u in [seam..1] -> cam1 u in [1-coverage..1]
                float uR = (1.0 - coverage)
                         + saturate((i.uv.x - seam) / (1.0 - seam)) * coverage;

                fixed4 left  = tex2D(_LeftTex,  float2(uL, i.uv.y));
                fixed4 right = tex2D(_RightTex, float2(uR, i.uv.y));

                // Linear blend across [seam - _BlendWidth, seam + _BlendWidth].
                // Outside that range, fully one camera or the other.
                float t = saturate((i.uv.x - (seam - _BlendWidth)) /
                                   (2.0 * _BlendWidth + 1e-6));
                fixed4 col = lerp(left, right, t);
                col.rgb *= _Brightness;
                col.a    = 1.0;
                return col;
            }
            ENDCG
        }
    }
    FallBack "Unlit/Texture"
}
