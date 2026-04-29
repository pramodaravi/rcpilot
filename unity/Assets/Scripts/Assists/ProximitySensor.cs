using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Assists
{
    /// <summary>
    /// Raycasts outward from the car to the nearest barrier in several fan
    /// directions, returning the shortest hit distance. Used by the barrier
    /// warning and as an input to the steer counter-correction logic.
    ///
    /// Fan pattern (in the car's local frame, 0 = forward):
    ///   -90°, -60°, -30°, 0°, +30°, +60°, +90°
    /// That's seven rays — cheap, and enough to catch walls coming at any
    /// angle the car might approach.
    ///
    /// Works against everything on the physics layer mask passed in. Caller
    /// should construct the mask to include the sim barriers and exclude the
    /// car itself (default mask includes everything; we use Default-Nothing
    /// pattern with Ignore Raycast to exclude the car body in Init).
    /// </summary>
    public class ProximitySensor : MonoBehaviour
    {
        public struct Reading
        {
            public float minDistM;      // shortest ray hit distance (float.MaxValue if clear)
            public Vector3 minDirLocal; // local-space direction of the nearest hit
            public float leftDistM;     // nearest hit in left hemisphere
            public float rightDistM;    // nearest hit in right hemisphere
        }

        public Reading latest;

        private Transform _carXform;
        private int _layerMask = ~0;
        private static readonly float[] AnglesDeg = { -90f, -60f, -30f, 0f, 30f, 60f, 90f };
        private const float MaxRangeM = 3.0f;
        private const float RayHeight = 0.12f; // midway up the barrier cubes (which are 0.4m tall)

        public void Init(Transform carXform, LayerMask mask)
        {
            _carXform = carXform;
            _layerMask = mask;
        }

        private void FixedUpdate()
        {
            if (_carXform == null) { latest = default; return; }

            Vector3 origin = _carXform.position + Vector3.up * RayHeight;
            float bestDist = float.MaxValue;
            Vector3 bestDirLocal = Vector3.forward;
            float leftBest = float.MaxValue, rightBest = float.MaxValue;

            for (int i = 0; i < AnglesDeg.Length; i++)
            {
                float deg = AnglesDeg[i];
                Vector3 localDir = Quaternion.AngleAxis(deg, Vector3.up) * Vector3.forward;
                Vector3 worldDir = _carXform.TransformDirection(localDir);

                if (Physics.Raycast(origin, worldDir, out RaycastHit hit, MaxRangeM, _layerMask,
                                    QueryTriggerInteraction.Ignore))
                {
                    if (hit.distance < bestDist)
                    {
                        bestDist = hit.distance;
                        bestDirLocal = localDir;
                    }
                    if (deg < 0f && hit.distance < leftBest)  leftBest = hit.distance;
                    if (deg > 0f && hit.distance < rightBest) rightBest = hit.distance;
                }
            }

            latest = new Reading
            {
                minDistM = bestDist,
                minDirLocal = bestDirLocal,
                leftDistM = leftBest,
                rightDistM = rightBest,
            };
        }

        private void OnDrawGizmosSelected()
        {
            if (_carXform == null) return;
            Gizmos.color = Color.yellow;
            Vector3 origin = _carXform.position + Vector3.up * RayHeight;
            foreach (var deg in AnglesDeg)
            {
                Vector3 localDir = Quaternion.AngleAxis(deg, Vector3.up) * Vector3.forward;
                Vector3 worldDir = _carXform.TransformDirection(localDir);
                Gizmos.DrawLine(origin, origin + worldDir * MaxRangeM);
            }
        }
    }
}
