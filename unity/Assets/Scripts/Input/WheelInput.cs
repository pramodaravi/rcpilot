using System;
using System.Text;
using UnityEngine;
using RcPilot.Core;
using RcPilot.Network;

namespace RcPilot.Input
{
    /// <summary>
    /// Driver-side input. For v0.1 this reads a **generic gamepad** (Xbox, PS, Switch
    /// Pro, MFi via Bluetooth — anything that Unity's legacy input sees as joystick 1).
    /// Steering = left-stick X, throttle = right trigger, brake = left trigger. The
    /// G920 wheel code that used to live here is gone — no native Logitech driver on
    /// macOS means we'd have been bolting on kernel extensions just to get consistent
    /// axis ranges, and Benno plans to move to a custom direct-drive wheel anyway.
    ///
    /// Why legacy input and not the new Input System package? Legacy input is already
    /// in 2022.3 LTS with no package import — one less thing to go wrong. If/when we
    /// go multi-player at the arcade, we'll reconsider.
    ///
    /// Axis indices for gamepads on macOS differ by vendor and connection type
    /// (Bluetooth vs USB, Xbox vs PS, etc.). The defaults in <see cref="WheelConfig"/>
    /// target a wired/Bluetooth Xbox-layout controller on macOS Sonoma+. If something
    /// doesn't move, set <c>input.logDiscovery = true</c> in config.json to dump axis
    /// and button activity to the Unity console on startup.
    ///
    /// The class is still called WheelInput because every other script in the project
    /// refers to it — renaming would churn a dozen files for no functional win.
    /// </summary>
    public class WheelInput : MonoBehaviour
    {
        public WheelState state;
        public bool padPresent;
        public string padName = "(none detected)";

        private Config _cfg;
        private ControlSender _sender;

        // Press-edge tracking. Holding a protocol bit for EdgeHoldSec after a rising
        // edge lets the Jetson see a clean event even at 200 Hz send rate.
        private bool _prevArm, _prevDisarm, _prevEstop, _prevReset, _prevCam, _prevLap;
        private double _armEdge, _disarmEdge, _estopEdge, _resetEdge, _camEdge, _lapEdge;
        private const float EdgeHoldSec = 0.08f;

        // One-shot discovery logging. Toggled by config OR the backtick key.
        private float _discoveryLogTimer;

        public event Action OnArm, OnDisarm, OnEstop, OnReset, OnCamToggle, OnLapMark;

        /// <summary>
        /// External trigger hooks used by sim mode (lap gate on track crossing,
        /// cam swap from a UI button, etc.). Kept tiny so RaceManager and the
        /// cockpit don't need sim-specific code paths.
        /// </summary>
        public void FireLapMark()    { OnLapMark?.Invoke(); }
        public void FireCamToggle()  { OnCamToggle?.Invoke(); }
        public void FireReset()      { OnReset?.Invoke(); }

        public void Configure(Config cfg)
        {
            _cfg = cfg;
            _sender = GetComponent<ControlSender>();
            DetectPad();
        }

        private void DetectPad()
        {
            var names = UnityEngine.Input.GetJoystickNames();
            for (int i = 0; i < names.Length; i++)
            {
                if (!string.IsNullOrEmpty(names[i]))
                {
                    padPresent = true;
                    padName = names[i];
                    Log.Info($"WheelInput: joystick[{i+1}] = '{padName}'");
                    break;
                }
            }
            if (!padPresent)
            {
                Log.Warn("WheelInput: no gamepad detected — keyboard fallback active (WASD + Space/R/C/L + 1/2)");
            }
        }

        private float RawJoyAxis(int axisIndex)
        {
            if (!padPresent) return 0f;
            // Unity surfaces raw axes as "joystick N axis M". Joystick 1 is the first
            // controller seen; we don't currently multi-plex players.
            string axisName = $"joystick 1 axis {axisIndex}";
            try { return UnityEngine.Input.GetAxisRaw(axisName); } catch { return 0f; }
        }

        /// <summary>
        /// Triggers on Xbox-style controllers idle at -1 and travel to +1 when fully
        /// pressed. PS controllers (and some others) idle at 0 and travel to +1. The
        /// <c>triggersAreBiPolar</c> flag chooses which to do; default is Xbox layout.
        /// </summary>
        private float ReadTriggerNormalized(int axisIndex)
        {
            float raw = RawJoyAxis(axisIndex);
            if (_cfg.wheel.triggersAreBiPolar)
            {
                // Map [-1..+1] → [0..1].
                return Mathf.Clamp01((raw + 1f) * 0.5f);
            }
            return Mathf.Clamp01(raw);
        }

        private float ReadSteeringNormalized()
        {
            float raw = RawJoyAxis(_cfg.wheel.steeringAxis);
            if (_cfg.wheel.invertSteering) raw = -raw;
            if (Mathf.Abs(raw) < _cfg.wheel.steeringDeadzone) return 0f;
            return Mathf.Clamp(raw, -1f, 1f);
        }

        private bool ReadButton(int btnIndex)
        {
            if (!padPresent || btnIndex < 0 || btnIndex > 19) return false;
            KeyCode kc = KeyCode.Joystick1Button0 + btnIndex;
            return UnityEngine.Input.GetKey(kc);
        }

        private void Update()
        {
            if (_cfg == null) return;

            // -------- Steering --------
            float steer = ReadSteeringNormalized();

            // -------- Throttle / Brake --------
            float thr, brk;
            if (padPresent)
            {
                thr = ReadTriggerNormalized(_cfg.wheel.throttleAxis);
                brk = ReadTriggerNormalized(_cfg.wheel.brakeAxis);
            }
            else
            {
                // Keyboard fallback.
                thr = UnityEngine.Input.GetKey(KeyCode.W) ? 0.6f : 0f;
                brk = UnityEngine.Input.GetKey(KeyCode.S) ? 0.8f : 0f;
            }

            // Keyboard steering fallback always layered in — useful for shakedown.
            if (!padPresent)
            {
                if (UnityEngine.Input.GetKey(KeyCode.A)) steer -= 1f;
                if (UnityEngine.Input.GetKey(KeyCode.D)) steer += 1f;
                steer = Mathf.Clamp(steer, -1f, 1f);
            }

            // -------- Buttons --------
            bool armNow    = ReadButton(_cfg.wheel.btnArm)       || UnityEngine.Input.GetKeyDown(KeyCode.Alpha1);
            bool disarmNow = ReadButton(_cfg.wheel.btnDisarm)    || UnityEngine.Input.GetKeyDown(KeyCode.Alpha2);
            bool estopNow  = ReadButton(_cfg.wheel.btnEstop)     || UnityEngine.Input.GetKey(KeyCode.Space);
            bool resetNow  = ReadButton(_cfg.wheel.btnReset)     || UnityEngine.Input.GetKeyDown(KeyCode.R);
            bool camNow    = ReadButton(_cfg.wheel.btnToggleCam) || UnityEngine.Input.GetKeyDown(KeyCode.C);
            bool lapNow    = ReadButton(_cfg.wheel.btnLapMark)   || UnityEngine.Input.GetKeyDown(KeyCode.L);

            double now = Time.realtimeSinceStartupAsDouble;
            if (armNow    && !_prevArm)    { _armEdge = now;    OnArm?.Invoke(); }
            if (disarmNow && !_prevDisarm) { _disarmEdge = now; OnDisarm?.Invoke(); }
            if (estopNow  && !_prevEstop)  { _estopEdge = now;  OnEstop?.Invoke(); }
            if (resetNow  && !_prevReset)  { _resetEdge = now;  OnReset?.Invoke(); }
            if (camNow    && !_prevCam)    { _camEdge = now;    OnCamToggle?.Invoke(); }
            if (lapNow    && !_prevLap)    { _lapEdge = now;    OnLapMark?.Invoke(); }

            _prevArm = armNow;
            _prevDisarm = disarmNow;
            _prevEstop = estopNow;
            _prevReset = resetNow;
            _prevCam = camNow;
            _prevLap = lapNow;

            // Protocol bits held for EdgeHoldSec after the edge, so the Jetson sees
            // the event even at its slower recv cadence.
            byte bits = 0;
            if (now - _armEdge    < EdgeHoldSec) bits |= Protocol.BTN_ARM;
            if (now - _disarmEdge < EdgeHoldSec) bits |= Protocol.BTN_DISARM;
            if (now - _estopEdge  < EdgeHoldSec) bits |= Protocol.BTN_ESTOP;
            if (now - _resetEdge  < EdgeHoldSec) bits |= Protocol.BTN_RESET;
            if (now - _camEdge    < EdgeHoldSec) bits |= Protocol.BTN_CAM;
            if (now - _lapEdge    < EdgeHoldSec) bits |= Protocol.BTN_LAP;

            // Convert to protocol units (-10000..+10000 for steering; 0..+10000 for pedals).
            short steerUnits = (short)Mathf.RoundToInt(steer * 10000f);
            short thrUnits   = (short)Mathf.RoundToInt(thr   * 10000f);
            short brkUnits   = (short)Mathf.RoundToInt(brk   * 10000f);

            state.steering = steer;
            state.throttle = thr;
            state.brake = brk;
            state.buttonsBits = bits;

            if (_sender != null)
            {
                _sender.steering = steerUnits;
                _sender.throttle = thrUnits;
                _sender.brake = brkUnits;
                _sender.reverse = 0;
                _sender.buttons = bits;
                _sender.assist = 0;
            }

            // -------- Discovery mode --------
            // Holds backtick (`) or config flag: log whichever axes are non-zero and
            // whichever buttons are pressed. Prints at ~2 Hz so the console stays
            // readable while you wiggle a stick or mash buttons to identify them.
            bool wantDiscovery = _cfg.wheel.logDiscovery
                                 || UnityEngine.Input.GetKey(KeyCode.BackQuote);
            if (wantDiscovery)
            {
                _discoveryLogTimer -= Time.unscaledDeltaTime;
                if (_discoveryLogTimer <= 0f)
                {
                    _discoveryLogTimer = 0.5f;
                    LogPadActivity();
                }
            }
        }

        private void LogPadActivity()
        {
            var sb = new StringBuilder("pad: ");
            bool any = false;
            for (int i = 0; i < 10; i++)
            {
                float v = RawJoyAxis(i);
                if (Mathf.Abs(v) > 0.1f)
                {
                    if (any) sb.Append(", ");
                    sb.Append($"axis{i}={v:F2}");
                    any = true;
                }
            }
            for (int i = 0; i < 20; i++)
            {
                if (UnityEngine.Input.GetKey(KeyCode.Joystick1Button0 + i))
                {
                    if (any) sb.Append(", ");
                    sb.Append($"btn{i}");
                    any = true;
                }
            }
            if (any) Log.Info(sb.ToString());
        }
    }

    [Serializable]
    public struct WheelState
    {
        public float steering;   // -1..+1
        public float throttle;   //  0..+1
        public float brake;      //  0..+1
        public byte  buttonsBits;
    }
}
