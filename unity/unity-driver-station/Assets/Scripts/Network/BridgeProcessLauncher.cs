using System;
using System.Diagnostics;
using System.IO;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// Spawns the local Python video-bridge sidecar (video-bridge/bridge.py)
    /// as a child process when Play starts, and kills it on Play stop /
    /// editor recompile / app quit. Removes the manual "open a PowerShell
    /// window and run py -3.11 video-bridge\bridge.py" step every session.
    ///
    /// Process lifecycle:
    ///   - Launch() called from Bootstrapper.Start()
    ///   - bridge.py runs as a hidden child process by default; stdout +
    ///     stderr stream into Unity's Console with a [bridge] prefix
    ///   - On OnDestroy / OnApplicationQuit / OnDisable the process is
    ///     killed and disposed
    ///
    /// Why a Unity-spawned child instead of a Windows scheduled task:
    ///   - Lifetime is tied to Play state - no stale bridge running after
    ///     you stop Unity, no orphaned ports, no port-bind conflicts on
    ///     re-Play
    ///   - bridge.py log appears in the SAME Console you're already watching
    ///   - No system-level changes needed; works for any developer who
    ///     clones the repo and hits Play
    ///
    /// Disable via Inspector or set autoStart=false to fall back to the
    /// manual "open PowerShell, run bridge.py" workflow (useful if you're
    /// debugging the bridge with extra flags).
    /// </summary>
    public class BridgeProcessLauncher : MonoBehaviour
    {
        [Header("Process control")]
        public bool autoStart = true;
        [Tooltip("video-bridge --preset value: lowlatency / fast / balanced / quality")]
        public string preset = "lowlatency";
        [Tooltip("Show the bridge in its own console window. Default false: " +
                 "the bridge's stdout streams into Unity's Console instead.")]
        public bool showWindow = false;

        [Header("Bridge network")]
        [Tooltip("RTP/H.264 UDP port from the Jetson camera sender.")]
        public int inPort = 5004;
        [Tooltip("JPEG-over-TCP port Unity connects to on localhost.")]
        public int outPort = 9000;
        [Tooltip("TCP bind address for Unity's bridge connection.")]
        public string tcpBind = "127.0.0.1";
        [Tooltip("RTP UDP bind address. 'auto' uses the local route to jetsonIp.")]
        public string rtpBind = "auto";
        [Tooltip("Jetson address used to auto-detect the local RTP bind address. " +
                 "Bootstrapper overrides this from config.network.jetsonIp at runtime.")]
        public string jetsonIp = "192.168.1.53";
        [Tooltip("Bridge payload format: raw is lowest latency on localhost; jpeg is fallback.")]
        public string frameFormat = "raw";

        [Header("Python launcher (Windows defaults)")]
        [Tooltip("Windows Python launcher executable. 'py' picks up the right " +
                 "version from the shebang or the -3.11 arg below.")]
        public string pythonExe = "py";
        [Tooltip("First argument to the launcher. '-3.11' pins Python 3.11 " +
                 "specifically since PyAV wheels we use require it.")]
        public string pythonVersionArg = "-3.11";

        private Process _process;

        public bool IsRunning => _process != null && !_process.HasExited;

        public void Launch()
        {
            if (IsRunning)
            {
                Log.Info("BridgeProcessLauncher: bridge already running, not relaunching.");
                return;
            }

            // Application.dataPath in the editor points at .../unity-driver-station/Assets
            // Project root is one level up. The bridge sits under video-bridge/.
            string projectAssets = Application.dataPath;
            string projectRoot = Path.GetDirectoryName(projectAssets);
            string scriptPath = Path.Combine(projectRoot ?? "", "video-bridge", "bridge.py");

            if (!File.Exists(scriptPath))
            {
                Log.Warn($"BridgeProcessLauncher: bridge.py not found at '{scriptPath}'. " +
                         "Falling back to manual launch.");
                return;
            }

            string args = $"{pythonVersionArg} \"{scriptPath}\" --preset {preset} " +
                          $"--in-port {inPort} --out-port {outPort} --bind {tcpBind} " +
                          $"--rtp-bind {rtpBind} --jetson-ip {jetsonIp} " +
                          $"--frame-format {frameFormat}";
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = args,
                    WorkingDirectory = projectRoot ?? Environment.CurrentDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = !showWindow,
                    RedirectStandardOutput = !showWindow,
                    RedirectStandardError = !showWindow,
                };
                _process = new Process { StartInfo = psi, EnableRaisingEvents = true };

                if (!showWindow)
                {
                    _process.OutputDataReceived += (s, e) =>
                    {
                        if (!string.IsNullOrEmpty(e.Data)) Log.Info($"[bridge] {e.Data}");
                    };
                    _process.ErrorDataReceived += (s, e) =>
                    {
                        if (!string.IsNullOrEmpty(e.Data)) Log.Warn($"[bridge] {e.Data}");
                    };
                }

                _process.Exited += (s, e) =>
                {
                    int code = _process?.ExitCode ?? -1;
                    Log.Info($"BridgeProcessLauncher: bridge process exited (code {code}).");
                };

                _process.Start();
                if (!showWindow)
                {
                    _process.BeginOutputReadLine();
                    _process.BeginErrorReadLine();
                }
                Log.Info($"BridgeProcessLauncher: started '{pythonExe} {args}' " +
                         $"(pid {_process.Id})");
            }
            catch (Exception e)
            {
                Log.Err($"BridgeProcessLauncher: failed to start bridge: {e.Message}. " +
                        $"Make sure '{pythonExe}' is on PATH and Python 3.11 is installed.");
                _process = null;
            }
        }

        public void Kill()
        {
            if (_process == null) return;
            try
            {
                if (!_process.HasExited)
                {
                    _process.Kill();
                    _process.WaitForExit(2000);
                    Log.Info("BridgeProcessLauncher: bridge process terminated.");
                }
            }
            catch (Exception e)
            {
                Log.Warn($"BridgeProcessLauncher: kill error: {e.Message}");
            }
            try { _process.Dispose(); } catch { }
            _process = null;
        }

        private void OnDestroy() => Kill();
        private void OnApplicationQuit() => Kill();
        private void OnDisable() => Kill();
    }
}
