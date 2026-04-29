using System;
using System.Collections.Generic;
using UnityEngine;
using RcPilot.Core;
using RcPilot.Input;
using RcPilot.Network;
using RcPilot.UI;

namespace RcPilot.Race
{
    /// <summary>
    /// Race state machine and lap timing.
    ///
    /// v0.1 uses manual lap marks — the driver hits a button (wheel or L key)
    /// as they cross the start/finish line. This is dramatically simpler than
    /// vision/RFID lap detection and it's a perfectly fine MVP for a pilot at
    /// the Novi venue.
    ///
    /// Later: wire a track-side IR gate or RFID reader to a separate UDP
    /// broadcaster and have RaceManager subscribe; flip <see cref="autoLap"/>
    /// to true and remove the manual requirement.
    /// </summary>
    public class RaceManager : MonoBehaviour
    {
        public enum Phase { Idle, CountdownGo, OnLap, Finished }

        public Phase phase = Phase.Idle;
        public bool IsRacing => phase == Phase.OnLap;
        public int LapIndex { get; private set; }
        public float CurrentLapTime { get; private set; } = -1;
        public float LastLapTime { get; private set; } = -1;
        public float BestLapTime { get; private set; } = -1;
        public bool LastLapWasBest { get; private set; }

        public BestTimeStore bestTimes = new BestTimeStore();
        public GhostRecorder ghost = new GhostRecorder();

        private Config _cfg;
        private TelemetryReceiver _telem;
        private WheelInput _wheel;
        private float _lapStartTime = -1;
        private float _raceStartTime = -1;
        private float _countdownUntil = -1;

        public event Action<int, float, bool> OnLapCompleted; // idx, time, wasBest
        public event Action OnRaceStarted;
        public event Action OnRaceFinished;

        public void Configure(Config cfg, TelemetryReceiver telem, WheelInput wheel)
        {
            _cfg = cfg;
            _telem = telem;
            _wheel = wheel;

            _wheel.OnLapMark += HandleLapMark;
            bestTimes.Load();
            BestLapTime = bestTimes.BestFor(cfg.race.driverName);

            Log.Info($"RaceManager ready (driver={cfg.race.driverName}, track={cfg.race.trackName})");
        }

        public void StartCountdown()
        {
            phase = Phase.CountdownGo;
            _countdownUntil = Time.unscaledTime + 3.2f; // ~3 sec countdown
            _raceStartTime = _countdownUntil;
            LapIndex = 0;
            CurrentLapTime = -1;
            LastLapTime = -1;
            LastLapWasBest = false;
            var boot = Bootstrapper.Instance;
            boot?.hud?.toasts?.Show("3", UiTheme.Accent, 0.9f);
        }

        public void StopRace()
        {
            phase = Phase.Finished;
            OnRaceFinished?.Invoke();
            var boot = Bootstrapper.Instance;
            boot?.hud?.toasts?.Show("RACE ENDED", UiTheme.AccentDim);
        }

        private void HandleLapMark()
        {
            if (phase == Phase.Idle || phase == Phase.CountdownGo)
            {
                // Use the lap button to also start a time trial if not running.
                StartCountdown();
                return;
            }
            if (phase != Phase.OnLap) return;

            float now = Time.unscaledTime;
            float lap = now - _lapStartTime;

            if (lap < _cfg.race.minLapSeconds)
            {
                // Debounce: driver pressed it too soon after crossing last.
                Bootstrapper.Instance?.hud?.toasts?.Show(
                    $"LAP TOO SHORT ({lap:F1}s) — ignored",
                    UiTheme.Warn, 1.5f);
                return;
            }

            LastLapTime = lap;
            bool isBest = BestLapTime < 0 || lap < BestLapTime;
            LastLapWasBest = isBest;
            if (isBest)
            {
                BestLapTime = lap;
                bestTimes.Submit(_cfg.race.driverName, lap);
                Bootstrapper.Instance?.hud?.toasts?.Show(
                    $"BEST LAP  {LapTimer.Fmt(lap)}", UiTheme.Good, 3f);
            }
            else
            {
                Bootstrapper.Instance?.hud?.toasts?.Show(
                    $"LAP {LapIndex + 1}  {LapTimer.Fmt(lap)}", UiTheme.Accent, 2f);
            }

            LapIndex++;
            OnLapCompleted?.Invoke(LapIndex, lap, isBest);
            ghost.StartNewLap();

            if (LapIndex >= _cfg.race.targetLapCount)
            {
                phase = Phase.Finished;
                OnRaceFinished?.Invoke();
                Bootstrapper.Instance?.hud?.toasts?.Show(
                    $"RACE COMPLETE · BEST {LapTimer.Fmt(BestLapTime)}",
                    UiTheme.Good, 4f);
            }
            else
            {
                _lapStartTime = now;
            }
        }

        private void Update()
        {
            float t = Time.unscaledTime;

            switch (phase)
            {
                case Phase.CountdownGo:
                    float remaining = _countdownUntil - t;
                    if (remaining <= 0)
                    {
                        phase = Phase.OnLap;
                        _lapStartTime = t;
                        OnRaceStarted?.Invoke();
                        Bootstrapper.Instance?.hud?.toasts?.Show("GO", UiTheme.Good, 1.2f);
                    }
                    else
                    {
                        // Toast each integer mark once. Kept simple: we rely on
                        // toast stacking rather than a dedicated countdown widget.
                        int shown = Mathf.CeilToInt(remaining);
                        if (shown != _lastCountdownShown)
                        {
                            Bootstrapper.Instance?.hud?.toasts?.Show(
                                shown.ToString(), UiTheme.Accent, 0.9f);
                            _lastCountdownShown = shown;
                        }
                    }
                    break;

                case Phase.OnLap:
                    CurrentLapTime = t - _lapStartTime;
                    ghost.Sample(_wheel?.state ?? default, _telem);
                    break;
            }
        }

        private int _lastCountdownShown = -1;

        private void OnDestroy()
        {
            if (_wheel != null) _wheel.OnLapMark -= HandleLapMark;
        }
    }
}
