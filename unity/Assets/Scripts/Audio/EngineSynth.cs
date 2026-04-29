using UnityEngine;
using RcPilot.Core;
using RcPilot.Network;

namespace RcPilot.Audio
{
    /// <summary>
    /// Synthesizes a convincing RC brushless whine in real time, modulated by
    /// the car's actual throttle PWM telemetry. Two sawtooth oscillators at
    /// slightly detuned frequencies + a low-pass-ish averaging filter + a
    /// subtle tire rumble beneath them, mixed to a fill-me-in engine.
    ///
    /// Why synth? Sampling engine audio from a brushless RC is hard — pitch
    /// varies with throttle and load continuously, so any pre-baked loop
    /// sounds wrong. A procedural engine is maybe 100 lines and responds
    /// instantly to PWM changes, which is exactly the racing-sim feel we
    /// want.
    ///
    /// Uses OnAudioFilterRead which is called on Unity's audio thread at
    /// whatever the system's DSP block size is (512 samples typical, @ 48kHz).
    /// All state updates from the main thread are atomic reads of a few
    /// floats — acceptable without a lock at this rate.
    /// </summary>
    public class EngineSynth : MonoBehaviour
    {
        private AudioSource _src;
        private Config _cfg;

        // State shared between main thread and audio thread — plain assignments
        // of single floats/doubles are atomic on all current runtime targets;
        // we don't need volatile for these audio-rate reads.
        private float _targetHz = 120f;
        private float _currentHz = 120f;
        private float _volume = 0.7f;
        private byte _state = Protocol.STATE_IDLE;

        private double _phase1;
        private double _phase2;
        private float _rumblePhase;
        private System.Random _noiseRng = new System.Random(0xBEEF);

        public void Init(Config cfg)
        {
            _cfg = cfg;
            if (!cfg.audio.engineSoundEnabled) return;
            _volume = cfg.audio.masterVolume;
            _src = gameObject.AddComponent<AudioSource>();
            _src.clip = AudioClip.Create("engine", 1, 1, 48000, false); // dummy, shape via OnAudioFilterRead
            _src.loop = true;
            _src.volume = 1f;
            _src.playOnAwake = true;
            _src.spatialBlend = 0f;
            _src.bypassEffects = true;
            _src.bypassListenerEffects = true;
            _src.bypassReverbZones = true;
            _src.Play();

            // Subscribe to telemetry for throttle-based pitch.
            var telem = Bootstrapper.Instance != null ? Bootstrapper.Instance.telemetry : null;
            if (telem != null) telem.OnPacket += OnTelemetry;
        }

        private void OnTelemetry(TelemetryPacket t)
        {
            _state = t.state;
            int pwm = t.pwmThrottle;
            int delta = pwm - 1500;
            float norm = Mathf.Clamp01(Mathf.Abs(delta) / 500f);
            _targetHz = Mathf.Lerp(_cfg.audio.engineMinHz, _cfg.audio.engineMaxHz, norm);
        }

        private void Update()
        {
            // Slew current frequency toward target so we don't click on step changes.
            _currentHz = Mathf.Lerp(_currentHz, _targetHz, Time.unscaledDeltaTime * 6f);

            // Hush when idle or estop — but don't go silent so the driver knows
            // the app is alive; a low idle hum is the tell.
            float gate = 1f;
            if (_state == Protocol.STATE_IDLE)  gate = 0.25f;
            if (_state == Protocol.STATE_ESTOP) gate = 0.1f;
            if (_state == Protocol.STATE_FAULT) gate = 0.0f;
            if (_src != null) _src.volume = _volume * gate;
        }

        private void OnAudioFilterRead(float[] data, int channels)
        {
            if (_cfg == null || !_cfg.audio.engineSoundEnabled) return;
            double sampleRate = AudioSettings.outputSampleRate;
            double phase1Inc = _currentHz / sampleRate;
            double phase2Inc = (_currentHz * 1.01) / sampleRate;
            float rumbleHz = 30f + _currentHz * 0.1f;
            float rumbleInc = rumbleHz / (float)sampleRate;

            for (int i = 0; i < data.Length; i += channels)
            {
                _phase1 += phase1Inc; if (_phase1 > 1) _phase1 -= 1;
                _phase2 += phase2Inc; if (_phase2 > 1) _phase2 -= 1;
                _rumblePhase += rumbleInc; if (_rumblePhase > 1) _rumblePhase -= 1;

                // Sawtooth mix
                float saw1 = (float)(_phase1 * 2.0 - 1.0);
                float saw2 = (float)(_phase2 * 2.0 - 1.0);
                float tone = (saw1 * 0.55f + saw2 * 0.45f);

                // Soft clip so loud parts don't distort nastily.
                tone = tone / (1 + Mathf.Abs(tone) * 0.3f);

                // Rumble: low triangle
                float rumbleSaw = _rumblePhase * 2f - 1f;
                float rumble = Mathf.Abs(rumbleSaw) * 2f - 1f;

                // Noise (tire / wind)
                float noise = ((float)_noiseRng.NextDouble() * 2f - 1f) * 0.05f;

                float sample = tone * 0.28f + rumble * 0.08f + noise;

                for (int c = 0; c < channels; c++) data[i + c] = sample;
            }
        }
    }
}
