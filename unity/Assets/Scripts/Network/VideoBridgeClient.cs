using System;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// TCP client to the video-bridge sidecar. The sidecar decodes RTP H.264
    /// from the Jetson and re-encodes each frame as JPEG, prefixed with:
    ///
    ///   magic  : 4 bytes  "JFRM"
    ///   size   : 4 bytes  little-endian uint (JPEG byte length)
    ///   frame  : size bytes
    ///
    /// Super-simple framing, battle-tested format, lets us auto-recover if we
    /// ever fall out of sync.
    ///
    /// Why not embed a real H.264 decoder in Unity? Because Windows x64 Unity
    /// doesn't ship with one and the reliable options (native GStreamer plugin,
    /// VideoPlayer-with-sourceUrl RTP) all fall over on 60fps RTP without a
    /// jitter buffer. JPEG-over-TCP gives us ~15–25 ms of added latency in
    /// exchange for a ~100-line receiver.
    /// </summary>
    public class VideoBridgeClient : MonoBehaviour
    {
        private static readonly byte[] Magic = { (byte)'J', (byte)'F', (byte)'R', (byte)'M' };

        public string label = "cam";
        public string host = "127.0.0.1";
        public int port = 9000;
        public Texture2D destTexture;
        public bool connected;
        public int framesReceived;
        public int framesDropped;
        public double lastFrameTime = -1;

        private Thread _thread;
        private volatile bool _running;
        private readonly object _frameLock = new object();
        private byte[] _pendingJpeg;
        private bool _hasPending;

        public void Configure(string lbl, string h, int p, Texture2D destTex)
        {
            label = lbl;
            host = h;
            port = p;
            destTexture = destTex;
            _running = true;
            _thread = new Thread(RxLoop) { IsBackground = true, Name = $"Video-{lbl}" };
            _thread.Start();
            Log.Info($"VideoBridgeClient[{label}] target {host}:{port}");
        }

        private void RxLoop()
        {
            byte[] header = new byte[8];
            while (_running)
            {
                TcpClient tcp = null;
                NetworkStream ns = null;
                try
                {
                    tcp = new TcpClient { NoDelay = true };
                    var ar = tcp.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(1500) || !tcp.Connected)
                    {
                        tcp.Close();
                        Thread.Sleep(500);
                        continue;
                    }
                    tcp.EndConnect(ar);
                    ns = tcp.GetStream();
                    connected = true;
                    Log.Info($"VideoBridgeClient[{label}] connected");

                    while (_running && tcp.Connected)
                    {
                        if (!ReadFully(ns, header, 0, 8))
                        {
                            break;
                        }
                        if (header[0] != Magic[0] || header[1] != Magic[1] ||
                            header[2] != Magic[2] || header[3] != Magic[3])
                        {
                            // resync: slide to next candidate
                            Log.Warn($"VideoBridgeClient[{label}] resyncing framing");
                            continue;
                        }
                        uint size = ByteOps.ReadU32LE(header, 4);
                        if (size == 0 || size > 8 * 1024 * 1024)
                        {
                            Log.Warn($"VideoBridgeClient[{label}] bad frame size {size}");
                            break;
                        }
                        byte[] jpeg = new byte[size];
                        if (!ReadFully(ns, jpeg, 0, (int)size)) break;
                        lock (_frameLock)
                        {
                            if (_hasPending) framesDropped++;
                            _pendingJpeg = jpeg;
                            _hasPending = true;
                        }
                    }
                }
                catch (Exception e)
                {
                    if (_running) Log.Warn($"VideoBridgeClient[{label}] error: {e.Message}");
                }
                finally
                {
                    connected = false;
                    try { ns?.Close(); } catch { }
                    try { tcp?.Close(); } catch { }
                }
                if (_running) Thread.Sleep(500); // backoff
            }
        }

        private static bool ReadFully(Stream s, byte[] buf, int off, int len)
        {
            int got = 0;
            while (got < len)
            {
                int n;
                try
                {
                    n = s.Read(buf, off + got, len - got);
                }
                catch
                {
                    return false;
                }
                if (n <= 0) return false;
                got += n;
            }
            return true;
        }

        private void Update()
        {
            // Apply the most recent JPEG, if any. ImageConversion.LoadImage handles
            // the resize of destTexture to match the JPEG's actual dimensions.
            byte[] jpeg = null;
            lock (_frameLock)
            {
                if (_hasPending)
                {
                    jpeg = _pendingJpeg;
                    _pendingJpeg = null;
                    _hasPending = false;
                }
            }
            if (jpeg == null) return;
            if (destTexture == null) return;
            try
            {
                if (!ImageConversion.LoadImage(destTexture, jpeg, markNonReadable: false))
                {
                    Log.Warn($"VideoBridgeClient[{label}] LoadImage failed");
                    return;
                }
                framesReceived++;
                lastFrameTime = Time.realtimeSinceStartupAsDouble;
            }
            catch (Exception e)
            {
                Log.Warn($"VideoBridgeClient[{label}] decode: {e.Message}");
            }
        }

        public float AgeMs =>
            lastFrameTime < 0 ? 9999f :
            (float)((Time.realtimeSinceStartupAsDouble - lastFrameTime) * 1000.0);

        private void OnDestroy()
        {
            _running = false;
            try { _thread?.Join(250); } catch { }
        }
    }
}
