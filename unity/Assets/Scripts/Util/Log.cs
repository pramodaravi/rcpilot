using UnityEngine;

namespace RcPilot.Core
{
    /// <summary>
    /// Lightweight wrapper around Debug.Log so log lines have a prefix that
    /// scrolls cleanly in Unity's console and in player.log.
    /// </summary>
    public static class Log
    {
        public static void Info(string msg)  => Debug.Log($"[rc-pilot] {msg}");
        public static void Warn(string msg)  => Debug.LogWarning($"[rc-pilot] {msg}");
        public static void Err(string msg)   => Debug.LogError($"[rc-pilot] {msg}");
    }
}
