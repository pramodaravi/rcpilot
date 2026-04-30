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
        private int _joystickNumber = 1;

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
                    _joystickNumber = i + 1;
                    Log.Info($"WheelInput: joystick[{_joystickNumber}] = '{padName}'");
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
            // We use Unity's wildcard joystick axis names: InputManager.asset
            // defines "joystick axis 0..9" with joyNum=0 (any joystick), so a
            // single named axis reads from whichever physical joystick has
            // input on that axis. Beats matching the slot number to a per-
            // joystick axis definition (fragile on Windows when phantom
            // slots shift the gamepad to slot 2+).
            string axisName = $"joystick axis {axisIndex}";
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
            KeyCode kc = KeyCode.Joystick1Button0 + ((_joystickNumber - 1) * 20) + btnIndex;
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

            // Cockpit-local button bits, held for EdgeHoldSec after a rising
            // edge so subscribers polling at lower rates still see the event.
            // Wire format v0.2 doesn't carry buttons — these are local-only.
            byte bits = 0;
            if (now - _armEdge    < EdgeHoldSec) bits |= WheelState.BTN_ARM;
            if (now - _disarmEdge < EdgeHoldSec) bits |= WheelState.BTN_DISARM;
            if (now - _estopEdge  < EdgeHoldSec) bits |= WheelState.BTN_ESTOP;
            if (now - _resetEdge  < EdgeHoldSec) bits |= WheelState.BTN_RESET;
            if (now - _camEdge    < EdgeHoldSec) bits |= WheelState.BTN_CAM;
            if (now - _lapEdge    < EdgeHoldSec) bits |= WheelState.BTN_LAP;

            // V0.2 wire format: float32 axes, no button channel. We still compute
            // `bits` above and stash them in WheelState.buttonsBits so local
            // subscribers (UI, RaceManager, sim cam toggle) get edge events —
            // the bits just don't ride the network. The Jetson learns about
            // arming/estop via the watchdog instead of explicit button bits.
            state.steering = steer;
            state.throttle = thr;
            state.brake = brk;
            state.buttonsBits = bits;

            if (_sender != null)
            {
                _sender.steering = steer;
                _sender.throttle = thr;
                _sender.brake    = brk;
                _sender.clutch   = 0f;  // G920 + Logitech pedals = no clutch input
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

        // Cockpit-local button bit codes. These used to ride the v0.1 wire and
        // were sourced from <c>RcPilot.Network.Protocol.BTN_*</c>. The v0.2 wire
        // format dropped the button channel (the watchdog handles arming /
        // estop), but local subscribers — RaceManager (lap-mark), CockpitBuilder
        // (camera toggle), sim mode (reset) — still need these edge bits to fire
        // events. So they live here, in cockpit-local space.
        public const byte BTN_ARM    = 1 << 0;
        public const byte BTN_DISARM = 1 << 1;
        public const byte BTN_ESTOP  = 1 << 2;
        public const byte BTN_RESET  = 1 << 3;
        public const byte BTN_CAM    = 1 << 4;
        public const byte BTN_LAP    = 1 << 5;
    }
}
