using System.Collections.Generic;
using UnityEngine;
using RcPilot.Core;
using RcPilot.Sim;

namespace RcPilot.Assists
{
    /// <summary>
    /// Builds a racing line and a per-point speed profile from a SimTrack.
    ///
    /// v0.1 algorithm — "biased apex":
    ///   1. Start from the track centerline.
    ///   2. For each point, compute local curvature (1/R) from three-point
    ///      circle fit through neighbors.
    ///   3. Shift each point toward the inside of the turn by an amount
    ///      proportional to curvature, clamped to (trackWidth/2 - carHalfWidth)
    ///      so the line stays on track.
    ///   4. Smooth the shifted line with a box filter (window=5) so the apex
    ///      bias doesn't create kinks.
    ///   5. Compute max safe speed at each point from the smoothed curvature:
    ///      v_max = sqrt(mu * g / k) where mu is the grip coefficient and k
    ///      is the curvature. mu here is derived from SimConfig.gripN divided
    ///      by car mass + small margin.
    ///   6. Forward/backward pass (like a Braden-Mayer) propagates braking
    ///      zones backward so the car arrives at each corner already at the
    ///      right speed.
    ///
    /// When real-car positioning exists (UWB / SLAM) this same computer runs
    /// once off-line from a surveyed track, producing the same Point array
    /// that AssistController reads. Keeping the data shape identical means
    /// sim and real paths use the exact same assist code.
    /// </summary>
    public class RacingLineComputer
    {
        public struct Point
        {
            public Vector3 pos;     // world position
            public float speedMps;  // max safe speed through here
            public float curvature; // 1/m (sign = left positive, right negative)
        }

        public List<Point> points { get; private set; } = new List<Point>();

        private const float CarHalfWidthM = 0.13f;   // 1/8 RC
        private const float BrakeMps2 = 12f;          // conservative; real brakes are stronger

        public void Build(SimTrack track, float maxSpeedMps, float frictionCoef)
        {
            if (track == null || track.centerline == null || track.centerline.Count < 4)
            {
                Log.Warn("RacingLineComputer: track not ready");
                return;
            }

            // 1. Local-space centerline & widths.
            int n = track.centerline.Count;
            var localLine = new Vector3[n];
            var widths = new float[n];
            for (int i = 0; i < n; i++)
            {
                localLine[i] = track.centerline[i].pos;
                widths[i] = track.centerline[i].widthM;
            }

            // 2. Curvature per point from signed triangle fit.
            var k = new float[n];
            for (int i = 0; i < n; i++)
            {
                Vector3 p0 = localLine[(i - 1 + n) % n];
                Vector3 p1 = localLine[i];
                Vector3 p2 = localLine[(i + 1) % n];
                k[i] = SignedCurvature(p0, p1, p2);
            }

            // 3. Shift toward inside of turn.
            var shifted = new Vector3[n];
            for (int i = 0; i < n; i++)
            {
                Vector3 tangent = (localLine[(i + 1) % n] - localLine[(i - 1 + n) % n]).normalized;
                Vector3 lateral = new Vector3(-tangent.z, 0, tangent.x); // +lateral = track-left
                float maxShift = widths[i] * 0.5f - CarHalfWidthM - 0.05f;
                // k[i] > 0 means turn-left → apex is on the LEFT (+lateral).
                // Scale by curvature magnitude normalized to ~max-curvature-on-track.
                float kNorm = Mathf.Clamp(k[i] * 5f, -1f, 1f);
                shifted[i] = localLine[i] + lateral * (kNorm * maxShift);
            }

            // 4. Box smooth.
            var smoothed = new Vector3[n];
            const int W = 5;
            for (int i = 0; i < n; i++)
            {
                Vector3 sum = Vector3.zero;
                for (int j = -W / 2; j <= W / 2; j++) sum += shifted[(i + j + n) % n];
                smoothed[i] = sum / W;
            }

            // 5. Re-compute curvature on smoothed line, then speed cap per point.
            var kSmooth = new float[n];
            var vCap = new float[n];
            for (int i = 0; i < n; i++)
            {
                Vector3 p0 = smoothed[(i - 1 + n) % n];
                Vector3 p1 = smoothed[i];
                Vector3 p2 = smoothed[(i + 1) % n];
                kSmooth[i] = SignedCurvature(p0, p1, p2);
                float absK = Mathf.Abs(kSmooth[i]);
                float vMax = absK < 1e-4f
                    ? maxSpeedMps
                    : Mathf.Sqrt(frictionCoef * 9.81f / absK);
                vCap[i] = Mathf.Min(maxSpeedMps, vMax);
            }

            // 6. Backward pass: the speed at point i cannot exceed what you
            //    could brake down to in reaching point i+1 from here.
            //    v[i]^2 <= v[i+1]^2 + 2 * brake * distance.
            for (int pass = 0; pass < 3; pass++)
            {
                for (int i = n - 1; i >= 0; i--)
                {
                    int iNext = (i + 1) % n;
                    float dist = Vector3.Distance(smoothed[i], smoothed[iNext]);
                    float vMax = Mathf.Sqrt(vCap[iNext] * vCap[iNext] + 2f * BrakeMps2 * dist);
                    vCap[i] = Mathf.Min(vCap[i], vMax);
                }
            }

            // 7. Publish as world-space points (transformed through track root).
            var root = track.Root;
            points.Clear();
            for (int i = 0; i < n; i++)
            {
                Vector3 world = root != null ? root.TransformPoint(smoothed[i]) : smoothed[i];
                points.Add(new Point
                {
                    pos = world,
                    speedMps = vCap[i],
                    curvature = kSmooth[i],
                });
            }

            Log.Info($"RacingLineComputer: built {points.Count} points " +
                     $"(speed range {Min(vCap):F1}..{Max(vCap):F1} m/s)");
        }

        public int NearestIndex(Vector3 worldPoint)
        {
            float best = float.MaxValue;
            int bestIdx = 0;
            for (int i = 0; i < points.Count; i++)
            {
                float d = (points[i].pos - worldPoint).sqrMagnitude;
                if (d < best) { best = d; bestIdx = i; }
            }
            return bestIdx;
        }

        /// <summary>Estimate the speed target <paramref name="lookAheadM"/> metres
        /// ahead of the nearest point to <paramref name="worldPoint"/>. Used by
        /// the speed governor — scrubbing throttle based on where you'll BE,
        /// not where you are, means you actually arrive at apex speed.</summary>
        public float SpeedTargetAhead(Vector3 worldPoint, float lookAheadM)
        {
            if (points.Count < 2) return float.MaxValue;
            int start = NearestIndex(worldPoint);
            float walked = 0f;
            int cur = start;
            while (walked < lookAheadM)
            {
                int nxt = (cur + 1) % points.Count;
                walked += Vector3.Distance(points[cur].pos, points[nxt].pos);
                cur = nxt;
                if (cur == start) break;
            }
            return points[cur].speedMps;
        }

        private static float SignedCurvature(Vector3 a, Vector3 b, Vector3 c)
        {
            // Project to XZ plane (y is irrelevant for racing-line computation).
            Vector2 A = new Vector2(a.x, a.z);
            Vector2 B = new Vector2(b.x, b.z);
            Vector2 C = new Vector2(c.x, c.z);
            Vector2 ab = B - A, bc = C - B;
            float cross = ab.x * bc.y - ab.y * bc.x;
            float triArea = cross * 0.5f;
            float lA = ab.magnitude, lB = bc.magnitude, lC = (C - A).magnitude;
            float denom = lA * lB * lC;
            if (denom < 1e-6f) return 0f;
            return 4f * triArea / denom; // signed; +ve = left turn
        }

        private static float Min(float[] arr) { float m = float.MaxValue; foreach (var v in arr) if (v < m) m = v; return m; }
        private static float Max(float[] arr) { float m = float.MinValue; foreach (var v in arr) if (v > m) m = v; return m; }
    }
}
