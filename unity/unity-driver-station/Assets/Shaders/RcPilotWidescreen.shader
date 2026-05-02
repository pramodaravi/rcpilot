// rcpilot widescreen — composite two camera textures into one seamless Quad.
//
// The left half of the Quad samples _LeftTex; the right half samples _RightTex.
// Each camera's full image is mapped onto its half (so a parallel-mounted pair
// shows duplicate content in the middle until the cameras are toed out — that's
// expected). A linear alpha blend across `_BlendWidth` UV units around the
// seam (centered at u=0.5) hides the hard discontinuity.
//
// Properties:
//   _LeftTex     — Texture2D blitted by VideoBridgeClient cam0
//   _RightTex    — Texture2D blitted by VideoBridgeClient cam1
//   _BlendWidth  — half-width of the blend region in UV space (0.05 = 10% of
//                  screen width is the gradient zone). 0 = hard seam.
//   _Brightness  — overall multiplier, useful for matching exposure between
//                  cameras if one runs hot.
//   _SeamShift   — shifts the seam left (negative) or right (positive) in UV
//                  space; useful if the cameras don't have equal FOV.

Shader "RcPilot/Widescreen2Cam"
{
    Properties
    {
        _LeftTex     ("Left Camera (cam0)",  2D) = "black" {}
        _RightTex    ("Right Camera (cam1)", 2D) = "black" {}
        _BlendWidth  ("Blend Half-Width",    Range(0.0, 0.25)) = 0.05
        _Brightness  ("Brightness",          Range(0.0, 2.0))  = 1.0
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

                // Map each half of the Quad onto the full UV range of its
                // camera. Left half (u < seam) -> cam0 stretched 2x; right
                // half (u > seam) -> cam1 stretched 2x.
                float uL = saturate(i.uv.x / seam);
                float uR = saturate((i.uv.x - seam) / (1.0 - seam));

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
