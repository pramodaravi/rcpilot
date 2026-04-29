using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;

namespace RcPilot.Sim
{
    /// <summary>
    /// Arcade-ish RC car physics on a Rigidbody.
    ///
    /// Not WheelColliders — WheelColliders are fragile, require a convex wheel
    /// mesh setup, and overshoot in an "RC on a kart track" feel. Instead we
    /// model:
    ///   - engine force along local +Z from throttle
    ///   - brake force opposing current velocity along +Z
    ///   - steering that rotates the velocity vector toward the heading (a
    ///     "bicycle model" yaw rate proportional to steer × speed / wheelbase)
    ///   - lateral grip as a side-velocity cancellation force (with a slip
    ///     threshold beyond which the car slides — gives arcade drift feel)
    ///   - air drag (simple linear)
    ///
    /// Reads the driver's steering/throttle/brake from <see cref="WheelInput.state"/>.
    /// Does NOT talk to ControlSender — sim mode bypasses the network path entirely.
    /// </summary>
    [RequireComponent(typeof(Rigidbody))]
    public class SimCar : MonoBehaviour
    {
        public Config cfg;
        public WheelInput wheel;

        // Public telemetry for SimTelemetryBridge and HUD.
        public float SpeedMps { get; private set; }
        public float SpeedKmh => SpeedMps * 3.6f;
        public float LateralSlip { get; private set; } // m/s sideways
        public float YawRateDps { get; private set; }  // degrees per second

        private Rigidbody _rb;
        private float _steer;    // last applied steer input [-1..+1]
        private float _thr;      // throttle [0..1]
        private float _brk;      // brake [0..1]

        public void Init(Config c, WheelInput w)
        {
            cfg = c;
            wheel = w;

            _rb = GetComponent<Rigidbody>();
            _rb.mass = 3.5f;                  // ~1/8 RC ready-to-run is ~3-4 kg
            _rb.drag = 0f;                    // we apply custom drag
            _rb.angularDrag = 2.0f;
            _rb.centerOfMass = new Vector3(0, -0.05f, 0);
            _rb.interpolation = RigidbodyInterpolation.Interpolate;
            _rb.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;

            // Slightly heavier-than-real mass keeps arcade physics stable at
            // high steering rates; real 1/8 RC cars are ~3 kg.
        }

        public void ResetTo(Vector3 pos, Quaternion rot)
        {
            _rb.velocity = Vector3.zero;
            _rb.angularVelocity = Vector3.zero;
            transform.SetPositionAndRotation(pos, rot);
        }

        private void FixedUpdate()
        {
            if (cfg == null || wheel == null) return;

            float dt = Time.fixedDeltaTime;
            var state = wheel.state;
            _steer = state.steering;  // -1..+1
            _thr = state.throttle;    //  0..+1
            _brk = state.brake;       //  0..+1

            // Local-frame velocity.
            Vector3 vWorld = _rb.velocity;
            Vector3 fwd = transform.forward;
            Vector3 right = transform.right;
            float vLong = Vector3.Dot(vWorld, fwd);   // + forward, - reverse
            float vLat = Vector3.Dot(vWorld, right);  // + sliding right
            LateralSlip = vLat;
            SpeedMps = Mathf.Abs(vLong);

            // -------- Drive force --------
            float engineA = _thr * cfg.sim.accelMps2;
            float brakeA = _brk * cfg.sim.brakeMps2;
            float longA = engineA;
            // Braking opposes current longitudinal velocity; clamp to stop, not reverse.
            if (vLong > 0.01f)       longA -= brakeA;
            else if (vLong < -0.01f) longA += brakeA;

            // Air drag (linear is fine at RC speeds).
            longA -= vLong * cfg.sim.drag;

            // Speed limiter — RC ESCs cut power at their rated top speed.
            float vMax = cfg.sim.straightSpeedMps;
            if (vLong > vMax) longA = Mathf.Min(longA, 0f);

            // -------- Steering & yaw (bicycle model) --------
            // Effective steer angle in radians.
            float steerDeg = _steer * cfg.sim.maxSteerDeg;
            float steerRad = steerDeg * Mathf.Deg2Rad;
            // Yaw rate = v * tan(steer) / wheelbase. Use signed vLong so reverse steers the right way.
            float yawRate = vLong * Mathf.Tan(steerRad) / Mathf.Max(0.05f, cfg.sim.wheelbaseM);
            YawRateDps = yawRate * Mathf.Rad2Deg;
            Quaternion yaw = Quaternion.AngleAxis(yawRate * dt * Mathf.Rad2Deg, Vector3.up);
            _rb.MoveRotation(yaw * _rb.rotation);

            // -------- Lateral grip --------
            // Cancel side-velocity up to a grip limit; beyond that, the car slides.
            // Grip budget scales weakly with throttle (weight transfer simplified).
            float gripMax = cfg.sim.gripN / _rb.mass;
            float desiredLatA = -vLat / Mathf.Max(0.05f, dt);
            float latA = Mathf.Clamp(desiredLatA, -gripMax, gripMax);

            // Apply the longitudinal + lateral accelerations back in world space.
            Vector3 accelWorld = fwd * longA + right * latA;
            _rb.AddForce(accelWorld * _rb.mass, ForceMode.Force);

            // Keep car pinned to the ground plane (no jumps) — track is flat at y=0.
            var p = _rb.position;
            if (p.y < 0.05f)
            {
                _rb.position = new Vector3(p.x, 0.05f, p.z);
                var v = _rb.velocity;
                if (v.y < 0) { v.y = 0; _rb.velocity = v; }
            }
        }
    }
}
