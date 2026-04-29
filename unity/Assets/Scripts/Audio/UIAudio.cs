using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;
using RcPilot.Network;

namespace RcPilot.Audio
{
    /// <summary>
    /// Tiny one-shot beep library for UI + race events. Builds each beep as a
    /// short synthesized AudioClip once, then PlayOneShot's it on demand.
    /// </summary>
    public class UIAudio : MonoBehaviour
    {
        private AudioSource _src;
        private AudioClip _beepArm;
        private AudioClip _beepDisarm;
        private AudioClip _beepEstop;
        private AudioClip _beepLap;
        private AudioClip _beepLink;

        private void Start()
        {
            _src = gameObject.AddComponent<AudioSource>();
            _src.playOnAwake = false;
            _src.spatialBlend = 0f;
            _src.volume = 0.5f;

            _beepArm    = MakeBeep(880f, 0.08f, 0.7f);
            _beepDisarm = MakeBeep(440f, 0.10f, 0.6f);
            _beepEstop  = MakeSweep(1200f, 300f, 0.30f, 0.9f);
            _beepLap    = MakeBeep(1318.5f /* E6 */, 0.12f, 0.6f);
            _beepLink   = MakeSweep(500f, 150f, 0.40f, 0.8f);

            var boot = Bootstrapper.Instance;
            if (boot == null) return;

            if (boot.wheelInput != null)
            {
                boot.wheelInput.OnArm    += () => Play(_beepArm);
                boot.wheelInput.OnDisarm += () => Play(_beepDisarm);
                boot.wheelInput.OnEstop  += () => Play(_beepEstop);
                boot.wheelInput.OnLapMark+= () => Play(_beepLap);
            }

            // Watch telemetry for link drops.
            if (boot.telemetry != null)
            {
                StartCoroutine(WatchLink());
            }
        }

        private System.Collections.IEnumerator WatchLink()
        {
            bool wasAlive = false;
            while (true)
            {
                yield return new WaitForSeconds(0.25f);
                var t = Bootstrapper.Instance?.telemetry;
                if (t == null) continue;
                bool alive = t.AgeMs < 250f && t.HasPacket;
                if (wasAlive && !alive) Play(_beepLink);
                wasAlive = alive;
            }
        }

        private void Play(AudioClip clip)
        {
            if (_src == null || clip == null) return;
            _src.PlayOneShot(clip);
        }

        private static AudioClip MakeBeep(float hz, float seconds, float volume)
        {
            int sr = 48000;
            int samples = Mathf.RoundToInt(sr * seconds);
            var data = new float[samples];
            for (int i = 0; i < samples; i++)
            {
                float t = (float)i / sr;
                float env = Mathf.Clamp01(1f - t / seconds);
                data[i] = Mathf.Sin(2 * Mathf.PI * hz * t) * volume * env;
            }
            var clip = AudioClip.Create($"beep_{hz}", samples, 1, sr, false);
            clip.SetData(data, 0);
            return clip;
        }

        private static AudioClip MakeSweep(float hzStart, float hzEnd,
                                           float seconds, float volume)
        {
            int sr = 48000;
            int samples = Mathf.RoundToInt(sr * seconds);
            var data = new float[samples];
            float phase = 0;
            for (int i = 0; i < samples; i++)
            {
                float t = (float)i / samples;
                float hz = Mathf.Lerp(hzStart, hzEnd, t);
                phase += 2 * Mathf.PI * hz / sr;
                float env = Mathf.Clamp01(1f - t);
                data[i] = Mathf.Sin(phase) * volume * env;
            }
            var clip = AudioClip.Create($"sweep_{hzStart}_{hzEnd}", samples, 1, sr, false);
            clip.SetData(data, 0);
            return clip;
        }
    }
}
