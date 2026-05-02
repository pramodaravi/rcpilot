using System.Collections;
using UnityEngine;
using RcPilot.Assists;
using RcPilot.Audio;
using RcPilot.Input;
using RcPilot.Network;
using RcPilot.Race;
using RcPilot.Sim;
using RcPilot.UI;
using RcPilot.Video;

namespace RcPilot.Core
{
    /// <summary>
    /// Entry point. Attach to a single empty GameObject in a fresh scene, press Play,
    /// and this builds the cockpit, HUD, audio, input, and network stack procedurally.
    /// Nothing in this project depends on pre-authored prefabs or scenes beyond a
    /// Camera + AudioListener + this Bootstrapper.
    ///
    /// Why procedural: lets us ship C# only (no binary .asset/.prefab files to rot),
    /// and lets the whole app live in source control as plain text. Downside is that
    /// art-direction requires code edits, but this is a driver station, not a game.
    /// </summary>
    [DefaultExecutionOrder(-10000)]
    public class Bootstrapper : MonoBehaviour
    {
        public static Bootstrapper Instance { get; private set; }

        [Header("Runtime references (populated at Start)")]
        public Config config;
        public ControlSender controlSender;
        public EchoReceiver echo;             // V0.2 RTT histogram + link health
        public TelemetryReceiver telemetry;   // V0.1-shape, dormant in hardware mode; sim mode injects
        public VideoBridgeClient cam0;
        public VideoBridgeClient cam1;        // V1: not configured (single-camera)
        public WheelInput wheelInput;
        public HUDController hud;
        public MainMenu menu;
        public ResultsScreen results;
        public CockpitBuilder cockpit;
        public EngineSynth engine;
        public RaceManager race;
        public GameState state;

        // Sim-mode references (null when sim.enabled=false).
        public SimWorld simWorld;
        public SimTrack simTrack;
        public SimCar simCar;
        public SimCameraRig simCameraRig;
        public SimTelemetryBridge simTelemetry;

        // Driver-assist stack (lives in sim mode today; real-car mode once
        // indoor positioning exists).
        public AssistController assists;

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);

            Application.runInBackground = true;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 120;
#if !UNITY_EDITOR
            // Only touch resolution in built player — in Editor, we want the Game
            // view to honor whatever the developer set.
            Screen.SetResolution(1920, 1080, FullScreenMode.FullScreenWindow);
#endif
        }

        private IEnumerator Start()
        {
            Log.Info("Bootstrapper starting");

            config = Config.LoadOrDefault();
            state = gameObject.AddComponent<GameState>();
            state.Init(config);

            // Cockpit + HUD first — we need RenderTextures/RawImages before the
            // video bridge clients start writing into them.
            cockpit = gameObject.AddComponent<CockpitBuilder>();
            cockpit.Build(config);

            hud = gameObject.AddComponent<HUDController>();
            hud.Build(config);

            // Menu (starts hidden; toggle with Esc)
            var menuHost = new GameObject("MenuHost");
            menuHost.transform.SetParent(transform, false);
            menu = menuHost.AddComponent<MainMenu>();
            menu.Build(config);

            // Results screen (shown automatically after a race)
            var resultsHost = new GameObject("ResultsHost");
            resultsHost.transform.SetParent(transform, false);
            results = resultsHost.AddComponent<ResultsScreen>();
            results.Build(config);

            // Audio
            engine = gameObject.AddComponent<EngineSynth>();
            engine.Init(config);
            gameObject.AddComponent<UIAudio>();

            // Network — control sender, echo receiver, and (dormant) telemetry
            // receiver are always created so the rest of the app can reference
            // them without null checks. In sim mode the sender stays
            // un-Configured (ControlSender.Ready=false) and SimTelemetryBridge
            // injects synthetic TelemetryPackets straight into the receiver.
            //
            // V0.2 doesn't have a separate telemetry UDP stream — the car only
            // sends 16-byte echoes back on the control socket — so
            // TelemetryReceiver no longer binds to a port. It exists solely as
            // the sim-mode sink. EchoReceiver is the V1 link-health source.
            controlSender = gameObject.AddComponent<ControlSender>();
            echo = gameObject.AddComponent<EchoReceiver>();
            telemetry = gameObject.AddComponent<TelemetryReceiver>();
            if (!config.sim.enabled)
            {
                controlSender.Configure(config.network.jetsonIp, config.network.controlPort,
                                        config.network.sendHz, config.network.localControlPort);
                echo.Configure(controlSender);

                // Auto-spawn the local Python video-bridge sidecar so the user
                // doesn't have to open a PowerShell window every session.
                // Lifetime is tied to this GameObject; OnDestroy / OnQuit on
                // the launcher kills the bridge cleanly. Disable via
                // BridgeProcessLauncher.autoStart=false in the Inspector if
                // you want to launch the bridge manually for debugging.
                // The Jetson stitches both IMX219 cameras into one 2560x720
                // RTP stream via nvcompositor (start_video_stitched.sh), so
                // the cockpit only needs ONE bridge.py decoding the merged
                // stream from UDP 5004. Set cam1Port > 0 only if you want to
                // go back to the dual-stream architecture (see git history
                // before the nvcompositor switch).
                var bridgeLauncher = gameObject.AddComponent<BridgeProcessLauncher>();
                bridgeLauncher.jetsonIp = config.network.jetsonIp;
                bridgeLauncher.outPort = config.video.cam0Port;
                if (bridgeLauncher.autoStart) bridgeLauncher.Launch();

                if (config.video.cam1Port > 0)
                {
                    var bridgeLauncher1 = gameObject.AddComponent<BridgeProcessLauncher>();
                    bridgeLauncher1.jetsonIp = config.network.jetsonIp;
                    bridgeLauncher1.inPort  = 5006;
                    bridgeLauncher1.outPort = config.video.cam1Port;
                    if (bridgeLauncher1.autoStart) bridgeLauncher1.Launch();
                }
            }
            else
            {
                Log.Info("SIM MODE: skipping ControlSender/EchoReceiver socket setup");
            }

            // Input. We allow the app to come up even if no wheel is attached, so
            // the HUD still paints and the video still flows — useful for shakedown.
            wheelInput = gameObject.AddComponent<WheelInput>();
            wheelInput.Configure(config);

            if (!config.sim.enabled)
            {
                // Dual-camera bench (cam1Port > 0): cam0 on UDP 5004 -> TCP 9000,
                // cam1 on UDP 5006 -> TCP 9001. Each bridge.py is a separate
                // sidecar process. Cockpit displays them side-by-side as a
                // widescreen windshield — see CockpitBuilder.
                cam0 = gameObject.AddComponent<VideoBridgeClient>();
                cam0.Configure("cam0", config.video.bridgeHost, config.video.cam0Port,
                               cockpit.GetCameraTexture(0));
                if (config.video.cam1Port > 0)
                {
                    cam1 = gameObject.AddComponent<VideoBridgeClient>();
                    cam1.Configure("cam1", config.video.bridgeHost, config.video.cam1Port,
                                   cockpit.GetCameraTexture(1));
                }
            }
            else
            {
                BuildSimWorld();
            }

            // Race logic
            race = gameObject.AddComponent<RaceManager>();
            race.Configure(config, telemetry, wheelInput);
            race.OnRaceFinished += () => results.Show(race);

            // Virtual cockpit (steering wheel + kart body + dashboard gauges)
            // is built after race so the lap-counter has a source. Speed comes
            // from SimCar in sim mode; real mode will plug in a positioning-
            // derived speed estimate once UWB is installed.
            System.Func<float> speedProvider = () =>
                (simCar != null) ? simCar.SpeedMps : 0f;
            System.Func<int> lapProvider = () => race != null ? race.LapIndex : 0;
            cockpit.AttachVirtualCockpit(config, wheelInput, telemetry,
                                         speedProvider, lapProvider);

            // Wheel button → cockpit camera swap.
            wheelInput.OnCamToggle += () => cockpit.ToggleMain();

            // In sim mode also wire Reset (Y / R key) to put the car back at the
            // start line if the driver gets stuck in a barrier.
            if (config.sim.enabled && simCar != null && simTrack != null)
            {
                wheelInput.OnReset += () =>
                {
                    simTrack.GetStartPose(out var p, out var r);
                    simCar.ResetTo(p, r);
                    hud?.toasts?.Show("CAR RESET", UiTheme.Accent, 1.2f);
                };
            }

            // Cycle assist levels with the Tab key — cheap runtime toggle that
            // doesn't need the menu UI. Wrapped 0→1→2→3→0. Sim-only today.
            if (config.sim.enabled && assists != null)
            {
                gameObject.AddComponent<AssistLevelCycler>().Bind(assists, hud);
            }

            // Let one frame pass so all Start/OnEnable cycles settle before we say OK.
            yield return null;
            Log.Info("Bootstrapper ready — hand over to drive");
        }

        private void OnApplicationQuit()
        {
            Log.Info("Bootstrapper shutting down");
            // V0.2: no explicit disarm packet (no button channel on the wire).
            // Simply ceasing to send control packets triggers the Jetson's
            // watchdog within ~200 ms, which coasts the motor — same end state
            // as the v0.1 disarm-and-quit dance.
        }

        /// <summary>
        /// Builds the sim-mode scene: world root + track + car + FPV/chase
        /// cameras + telemetry synthesizer + lap-crossing trigger. Must be
        /// called after CockpitBuilder is up — the camera rig hands the cockpit
        /// its RenderTextures via SetCameraSource.
        /// </summary>
        private void BuildSimWorld()
        {
            Log.Info("SIM MODE: building sim world");

            // 1. World root (offset far above cockpit so the cockpit camera never sees it).
            var worldGO = new GameObject("SimWorldHost");
            worldGO.transform.SetParent(transform, false);
            simWorld = worldGO.AddComponent<SimWorld>();
            simWorld.Build(config);

            // 2. Track geometry under sim root.
            var trackGO = new GameObject("SimTrackHost");
            trackGO.transform.SetParent(simWorld.root.transform, false);
            simTrack = trackGO.AddComponent<SimTrack>();
            simTrack.Build(config, simWorld.trackParent);

            // 3. Car.
            simTrack.GetStartPose(out var startPos, out var startRot);
            var carGO = new GameObject("SimCar");
            carGO.transform.SetParent(simWorld.root.transform, false);
            carGO.transform.SetPositionAndRotation(startPos, startRot);
            // Ignore Raycast layer (index 2) so the proximity sensor's rays
            // don't hit the car's own collider. Layer collision matrix still
            // lets the car collide with barriers on Default.
            carGO.layer = 2;
            // Body mesh — a small flat cube so you can see the car in chase cam.
            var body = GameObject.CreatePrimitive(PrimitiveType.Cube);
            body.name = "CarBody";
            body.transform.SetParent(carGO.transform, false);
            body.transform.localScale = new Vector3(0.25f, 0.12f, 0.45f); // 1/8 RC-ish
            Destroy(body.GetComponent<BoxCollider>());
            var bodyMr = body.GetComponent<MeshRenderer>();
            var bodyMat = new Material(Shader.Find("Universal Render Pipeline/Lit")
                                       ?? Shader.Find("Standard")
                                       ?? Shader.Find("Unlit/Color"));
            if (bodyMat.HasProperty("_BaseColor")) bodyMat.SetColor("_BaseColor", new Color(0.9f, 0.3f, 0.15f));
            if (bodyMat.HasProperty("_Color"))     bodyMat.SetColor("_Color", new Color(0.9f, 0.3f, 0.15f));
            bodyMr.material = bodyMat;
            // Collider for the car — box sized roughly like the body.
            var carCol = carGO.AddComponent<BoxCollider>();
            carCol.size = new Vector3(0.25f, 0.12f, 0.45f);
            carCol.center = new Vector3(0, 0.06f, 0);

            var rb = carGO.AddComponent<Rigidbody>();
            rb.constraints = RigidbodyConstraints.FreezeRotationX
                           | RigidbodyConstraints.FreezeRotationZ;
            simCar = carGO.AddComponent<SimCar>();
            simCar.Init(config, wheelInput);

            // 4. Cameras (FPV on car → cockpit slot 0; chase → cockpit slot 1).
            simCameraRig = gameObject.AddComponent<SimCameraRig>();
            simCameraRig.Init(config, carGO.transform, cockpit);

            // 5. Lap-crossing trigger (start/finish was built by SimTrack).
            if (simTrack.startFinishTransform != null)
            {
                var lap = simTrack.startFinishTransform.gameObject.AddComponent<SimLapTrigger>();
                lap.Init(wheelInput, carGO.transform);
            }

            // 6. Telemetry bridge: sim → TelemetryReceiver (main thread inject).
            simTelemetry = gameObject.AddComponent<SimTelemetryBridge>();
            simTelemetry.Init(simCar, telemetry);

            // 7. Driver-assist stack. Lives on the Bootstrapper so its execution
            //    order (500) interleaves properly between WheelInput (0) and
            //    ControlSender (1000). Even when defaultLevel=Off, we instantiate
            //    it so the menu can switch assists on at runtime without a
            //    scene reload.
            if (!config.assists.disabled)
            {
                assists = gameObject.AddComponent<AssistController>();
                int lvl = Mathf.Clamp(config.assists.defaultLevel, 0, 3);
                assists.level = (AssistLevel)lvl;
                assists.Init(config, wheelInput, controlSender, simCar, simTrack, hud);
            }

            Log.Info("SIM MODE: world ready, drive away");
        }
    }
}
