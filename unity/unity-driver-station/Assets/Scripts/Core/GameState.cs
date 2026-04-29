using UnityEngine;

namespace RcPilot.Core
{
    /// <summary>
    /// Single source of truth for high-level app state (not the RC car's state
    /// machine — that's the Jetson's business, and we only mirror it via
    /// telemetry). This tracks whether the player is in a menu, a race, or
    /// looking at a results screen.
    /// </summary>
    public class GameState : MonoBehaviour
    {
        public enum Screen { MainMenu, Cockpit, Paused, Results }

        public Screen current = Screen.Cockpit; // skip menu on v0.1 startup

        public Config config { get; private set; }

        public System.Action<Screen, Screen> OnScreenChanged;

        public void Init(Config cfg)
        {
            config = cfg;
        }

        public void Goto(Screen next)
        {
            if (next == current) return;
            var prev = current;
            current = next;
            Log.Info($"Screen {prev} -> {next}");
            OnScreenChanged?.Invoke(prev, next);
        }
    }
}
