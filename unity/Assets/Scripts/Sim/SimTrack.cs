using System.Collections.Generic;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Sim
{
    /// <summary>
    /// Procedural Novi-inspired kart track.
    ///
    /// Not a cycle-accurate reproduction — Full Throttle Novi's actual track
    /// layout isn't public. Instead this is a 1:1-scale "kart track shaped"
    /// loop of roughly 335 m (1100 ft) with the flavors we know Novi has:
    /// one big main straight, a hairpin, a chicane, a sweeper, a tighter
    /// infield section. Total length verified at build time — <see cref="TotalLengthM"/>.
    ///
    /// Geometry is generated from a centerline of <see cref="Waypoint"/>s. For
    /// each pair of consecutive waypoints we emit:
    ///   - a tarmac quad (racing surface)
    ///   - two barrier cubes (inner + outer) with box colliders
    /// The start/finish is a trigger collider straddling the first segment.
    ///
    /// The racing line (used both visually and by the driver-assist system
    /// later) is exposed as an ordered list of sample points via <see cref="RacingLine"/>.
    /// </summary>
    public class SimTrack : MonoBehaviour
    {
        public struct Waypoint
        {
            public Vector3 pos;     // centerline position (y=0 at track level)
            public float widthM;    // track width at this point
            public float speedHint; // optimal speed through this point, m/s (racing-line seed)

            public Waypoint(float x, float z, float w, float v)
            {
                pos = new Vector3(x, 0, z); widthM = w; speedHint = v;
            }
        }

        public List<Waypoint> centerline { get; private set; } = new List<Waypoint>();
        public List<Vector3> RacingLine { get; private set; } = new List<Vector3>();
        public float TotalLengthM { get; private set; }
        public Transform startFinishTransform;

        private Config _cfg;
        private Transform _root;

        public void Build(Config cfg, Transform root)
        {
            _cfg = cfg;
            _root = root;

            BuildCenterline();
            BuildMesh();
            BuildBarriers();
            BuildStartFinish();
            BuildRacingLine();
            MeasureLength();

            Log.Info($"SimTrack: built {centerline.Count} waypoints, " +
                     $"{TotalLengthM:F1}m total (target was ~335m)");
        }

        // -------------------------------------------------------------------
        // Centerline shape.
        //
        // I built this interactively in a sketch so the curvature looks
        // track-like. Points are (x, z, width, speed-hint). Speed hints are
        // m/s the racing line should try not to exceed at that point.
        //
        // Overall shape: long main straight running +Z; tight U at the top;
        // right-hand sweeper coming back south; chicane on the west side; a
        // short infield hairpin; back to start.
        // -------------------------------------------------------------------
        private void BuildCenterline()
        {
            float w = _cfg.sim.trackWidthM;
            float vStraight = _cfg.sim.straightSpeedMps;
            float vMedium = vStraight * 0.65f;
            float vSlow = vStraight * 0.35f;

            // Sparse anchors; we'll interpolate to a dense walk below.
            var anchors = new List<Waypoint>
            {
                // Main straight, running +Z.
                new Waypoint( 0,   0, w, vStraight),
                new Waypoint( 0,  45, w, vStraight),
                new Waypoint( 0,  90, w, vStraight * 0.95f),

                // Sweeping left up to the top.
                new Waypoint( -8,  115, w, vMedium),
                new Waypoint(-20,  128, w, vMedium * 0.9f),

                // Tight hairpin around (-32, 130).
                new Waypoint(-28,  135, w * 0.85f, vSlow),
                new Waypoint(-36,  130, w * 0.85f, vSlow),
                new Waypoint(-34,  120, w * 0.9f,  vSlow * 1.3f),

                // Right-hand sweeper running -Z.
                new Waypoint(-28, 100, w, vMedium),
                new Waypoint(-24,  70, w, vMedium),
                new Waypoint(-26,  45, w, vMedium),

                // Chicane west — quick left-right.
                new Waypoint(-34,  30, w * 0.95f, vSlow * 1.5f),
                new Waypoint(-30,  20, w * 0.95f, vSlow * 1.5f),
                new Waypoint(-36,   5, w * 0.95f, vSlow * 1.2f),

                // Infield hairpin around (-28, -10).
                new Waypoint(-32, -10, w * 0.85f, vSlow),
                new Waypoint(-22, -14, w * 0.85f, vSlow),

                // Run back toward start along the south edge.
                new Waypoint(-10, -10, w, vMedium),
                new Waypoint(  0, -5,  w, vMedium),
            };

            // Dense walk — Catmull-Rom through the anchors, ~0.8 m spacing.
            centerline.Clear();
            int n = anchors.Count;
            const float sampleStep = 0.8f;
            for (int i = 0; i < n; i++)
            {
                Waypoint p0 = anchors[(i - 1 + n) % n];
                Waypoint p1 = anchors[i];
                Waypoint p2 = anchors[(i + 1) % n];
                Waypoint p3 = anchors[(i + 2) % n];
                float segLen = Vector3.Distance(p1.pos, p2.pos);
                int steps = Mathf.Max(4, Mathf.RoundToInt(segLen / sampleStep));
                for (int s = 0; s < steps; s++)
                {
                    float t = (float)s / steps;
                    Vector3 pos = CatmullRom(p0.pos, p1.pos, p2.pos, p3.pos, t);
                    float width = Mathf.Lerp(p1.widthM, p2.widthM, t);
                    float speed = Mathf.Lerp(p1.speedHint, p2.speedHint, t);
                    centerline.Add(new Waypoint(pos.x, pos.z, width, speed));
                }
            }
        }

        private void BuildMesh()
        {
            // A triangle strip along the centerline, 2 verts per waypoint (left + right edge).
            int n = centerline.Count;
            var verts = new Vector3[n * 2];
            var uvs = new Vector2[n * 2];
            var tris = new int[n * 6];

            for (int i = 0; i < n; i++)
            {
                var c = centerline[i];
                var cNext = centerline[(i + 1) % n];
                Vector3 tangent = (cNext.pos - c.pos).normalized;
                Vector3 normal = new Vector3(-tangent.z, 0, tangent.x); // left of direction
                float half = c.widthM * 0.5f;
                verts[i * 2]     = c.pos + normal * half;   // left edge
                verts[i * 2 + 1] = c.pos - normal * half;   // right edge
                uvs[i * 2]     = new Vector2(0, (float)i / n);
                uvs[i * 2 + 1] = new Vector2(1, (float)i / n);
            }

            for (int i = 0; i < n; i++)
            {
                int a = i * 2;
                int b = i * 2 + 1;
                int c2 = ((i + 1) % n) * 2;
                int d = ((i + 1) % n) * 2 + 1;
                int t = i * 6;
                tris[t + 0] = a; tris[t + 1] = c2; tris[t + 2] = b;
                tris[t + 3] = b; tris[t + 4] = c2; tris[t + 5] = d;
            }

            var mesh = new Mesh { name = "TrackSurface" };
            mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt16;
            mesh.vertices = verts;
            mesh.uv = uvs;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();

            var go = new GameObject("Tarmac");
            go.transform.SetParent(_root, false);
            go.transform.localPosition = new Vector3(0, 0.01f, 0);
            var mf = go.AddComponent<MeshFilter>();
            mf.sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            mr.material = MakeMat(new Color(0.22f, 0.22f, 0.24f));

            // Collider for lateral push-out if a wheel strays off — we also use
            // barriers, but a tarmac-vs-grass collider lets the car physics
            // keep the same friction everywhere. Use a thin box under the mesh
            // instead of a MeshCollider (MeshCollider on a non-convex strip is
            // fiddly). A big plane is enough since the barriers do the real work.
        }

        private void BuildBarriers()
        {
            // For each centerline segment emit two boxes, one on each side at track
            // edge + a small gap, height 0.4 m, thickness 0.15 m.
            int n = centerline.Count;
            var barriersParent = new GameObject("Barriers").transform;
            barriersParent.SetParent(_root, false);

            for (int i = 0; i < n; i++)
            {
                var c = centerline[i];
                var cNext = centerline[(i + 1) % n];
                Vector3 midpoint = (c.pos + cNext.pos) * 0.5f;
                Vector3 tangent = (cNext.pos - c.pos).normalized;
                Vector3 normal = new Vector3(-tangent.z, 0, tangent.x);
                float segLen = Vector3.Distance(c.pos, cNext.pos);
                float avgWidth = (c.widthM + cNext.widthM) * 0.5f;
                float offset = avgWidth * 0.5f + 0.15f;

                SpawnBarrier(barriersParent, midpoint + normal * offset, tangent, segLen, "WallL");
                SpawnBarrier(barriersParent, midpoint - normal * offset, tangent, segLen, "WallR");
            }
        }

        private void SpawnBarrier(Transform parent, Vector3 midPos, Vector3 tangent,
                                  float length, string name)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = midPos + Vector3.up * 0.2f;
            go.transform.localRotation = Quaternion.LookRotation(tangent, Vector3.up);
            go.transform.localScale = new Vector3(0.15f, 0.4f, length);

            var mr = go.GetComponent<MeshRenderer>();
            mr.material = MakeMat(new Color(0.85f, 0.15f, 0.15f));
            mr.material.EnableKeyword("_EMISSION");
            if (mr.material.HasProperty("_EmissionColor"))
                mr.material.SetColor("_EmissionColor", new Color(0.3f, 0.02f, 0.02f));
        }

        private void BuildStartFinish()
        {
            // Trigger spanning the track width at segment 0 → segment 1.
            var a = centerline[0];
            var b = centerline[1];
            Vector3 mid = (a.pos + b.pos) * 0.5f;
            Vector3 tangent = (b.pos - a.pos).normalized;
            Vector3 normal = new Vector3(-tangent.z, 0, tangent.x);

            var go = new GameObject("StartFinish");
            go.transform.SetParent(_root, false);
            go.transform.localPosition = mid + Vector3.up * 0.5f;
            go.transform.localRotation = Quaternion.LookRotation(tangent, Vector3.up);

            var box = go.AddComponent<BoxCollider>();
            box.isTrigger = true;
            box.size = new Vector3(a.widthM + 0.4f, 1.0f, 0.4f);

            // Visible start/finish stripe — thin bright quad on tarmac.
            var stripe = GameObject.CreatePrimitive(PrimitiveType.Quad);
            stripe.name = "StartFinishStripe";
            Destroy(stripe.GetComponent<Collider>());
            stripe.transform.SetParent(_root, false);
            stripe.transform.localPosition = mid + Vector3.up * 0.02f;
            stripe.transform.localRotation = Quaternion.LookRotation(Vector3.up, tangent);
            stripe.transform.localScale = new Vector3(a.widthM, 0.5f, 1f);
            var mr = stripe.GetComponent<MeshRenderer>();
            mr.material = MakeMat(new Color(0.95f, 0.95f, 0.95f));

            startFinishTransform = go.transform;
        }

        private void BuildRacingLine()
        {
            // Cheap v0: racing line = centerline. The driver-assist system will
            // replace this with a proper apex-biased line; this is the seed
            // everything else reads from.
            RacingLine.Clear();
            foreach (var c in centerline) RacingLine.Add(c.pos);
        }

        private void MeasureLength()
        {
            float total = 0f;
            int n = centerline.Count;
            for (int i = 0; i < n; i++)
                total += Vector3.Distance(centerline[i].pos, centerline[(i + 1) % n].pos);
            TotalLengthM = total;
        }

        /// <summary>Spawn pose just PAST the start line, aimed down the straight.
        /// Returned in WORLD SPACE. Positioned past the line (not before it) so
        /// the driver's first crossing happens after a full lap, not after the
        /// 3m warm-up roll-forward — keeps RaceManager's minLapSeconds debounce
        /// happy.</summary>
        public void GetStartPose(out Vector3 pos, out Quaternion rot)
        {
            var a = centerline[0];
            var b = centerline[1];
            Vector3 forward = (b.pos - a.pos).normalized;
            Vector3 localPos = a.pos + forward * 1.5f + Vector3.up * 0.15f;
            pos = _root != null ? _root.TransformPoint(localPos) : localPos;
            Quaternion localRot = Quaternion.LookRotation(forward, Vector3.up);
            rot = _root != null ? _root.rotation * localRot : localRot;
        }

        /// <summary>World-space transform of the track root (parent of all track
        /// geometry). Useful for reset pose transforms when the sim is offset
        /// from the main scene origin.</summary>
        public Transform Root => _root;

        /// <summary>Index of the centerline sample nearest to a world point.
        /// Linear scan — fine for 400 waypoints, called a few times a second.</summary>
        public int NearestCenterlineIndex(Vector3 worldPoint)
        {
            float best = float.MaxValue;
            int bestIdx = 0;
            for (int i = 0; i < centerline.Count; i++)
            {
                float d = (centerline[i].pos - worldPoint).sqrMagnitude;
                if (d < best) { best = d; bestIdx = i; }
            }
            return bestIdx;
        }

        private static Vector3 CatmullRom(Vector3 p0, Vector3 p1, Vector3 p2, Vector3 p3, float t)
        {
            float t2 = t * t, t3 = t2 * t;
            return 0.5f * ((2f * p1) +
                           (-p0 + p2) * t +
                           (2f * p0 - 5f * p1 + 4f * p2 - p3) * t2 +
                           (-p0 + 3f * p1 - 3f * p2 + p3) * t3);
        }

        private static Material MakeMat(Color c)
        {
            Shader s = Shader.Find("Universal Render Pipeline/Lit")
                    ?? Shader.Find("Standard")
                    ?? Shader.Find("Unlit/Color")
                    ?? Shader.Find("Sprites/Default");
            var m = new Material(s);
            if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", c);
            if (m.HasProperty("_Color"))     m.SetColor("_Color", c);
            m.color = c;
            return m;
        }
    }
}
