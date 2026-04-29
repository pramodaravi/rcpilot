using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;

namespace RcPilot.Sim
{
    /// <summary>
    /// Attach to the start/finish trigger collider. When the car crosses it in
    /// the forward direction, fire WheelInput.FireLapMark() — which is the
    /// same path the hardware gamepad button uses, so RaceManager doesn't
    /// need a sim-specific branch.
    ///
    /// Directionality: we track the car's last-known side of the line (sign of
    /// the dot product with the line's forward vector). A lap completes only
    /// when the sign flips from negative to positive — so backing over the
    /// start line doesn't count. Debounce is also applied (1.5 s).
    /// </summary>
    public class SimLapTrigger : MonoBehaviour
    {
        public WheelInput wheel;
        public Transform carTransform;

        private float _lastFireTime = -10f;
        private float _lastDot = float.NaN;
        private const float DebounceSec = 1.5f;

        public void Init(WheelInput w, Transform car)
        {
            wheel = w;
            carTransform = car;
        }

        private void OnTriggerStay(Collider other)
        {
            if (carTransform == null || wheel == null) return;

            // Only consider the car rigidbody.
            var rb = other.attachedRigidbody;
            if (rb == null || rb.transform != carTransform) return;

            // Signed distance along this trigger's forward vector from its origin.
            Vector3 rel = carTransform.position - transform.position;
            float dot = Vector3.Dot(rel, transform.forward);

            if (!float.IsNaN(_lastDot) && _lastDot < 0 && dot >= 0)
            {
                if (Time.unscaledTime - _lastFireTime > DebounceSec)
                {
                    _lastFireTime = Time.unscaledTime;
                    wheel.FireLapMark();
                    Log.Info("SimLapTrigger: lap mark fired");
                }
            }
            _lastDot = dot;
        }

        private void OnTriggerExit(Collider other)
        {
            var rb = other.attachedRigidbody;
            if (rb != null && rb.transform == carTransform) _lastDot = float.NaN;
        }
    }
}
