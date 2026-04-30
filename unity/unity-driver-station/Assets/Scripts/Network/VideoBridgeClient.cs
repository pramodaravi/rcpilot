using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using RcPilot.Core;

namespace RcPilot.Network
{
    /// <summary>
    /// TCP client to the video-bridge sidecar. The sidecar decodes RTP H.264
    /// from the Jetson and forwards raw RGB frames by default. JPEG remains
    /// supported as a compatibility fallback.
    ///
    /// Why not embed a real H.264 decoder in Unity? Because Windows x64 Unity
    /// doesn't ship with one and the reliable options (native GStreamer plugin,
    /// VideoPlayer-with-sourceUrl RTP) all fall over on 60fps RTP without a
    /// jitter buffer. The bridge sidecar gets us a ~100-line receiver: raw
    /// RFRM frames bypass JPEG encode/decode for ~5 ms localhost latency,
    /// with JFRM JPEG kept as a compatibility fallback.
    /// </summary>
    public class VideoBridgeClient : MonoBehaviour
    {
        private static readonly byte[] MagicJpeg = { (byte)'J', (byte)'F', (byte)'R', (byte)'M' };
        private static readonly byte[] MagicRaw = { (byte)'R', (byte)'F', (byte)'R', (byte)'M' };

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
        private readonly object _poolLock = new object();
        private readonly List<byte[]> _rawPool = new List<byte[]>(3);
        private byte[] _pendingFrame;
        private bool _pendingRaw;
        private int _pendingWidth;
        private int _pendingHeight;
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
            byte[] rawMeta = new byte[4];
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
                        bool isJpeg = header[0] == MagicJpeg[0] && header[1] == MagicJpeg[1] &&
                                      header[2] == MagicJpeg[2] && header[3] == MagicJpeg[3];
                        bool isRaw = header[0] == MagicRaw[0] && header[1] == MagicRaw[1] &&
                                     header[2] == MagicRaw[2] && header[3] == MagicRaw[3];
                        if (!isJpeg && !isRaw)
                        {
                            // resync: slide to next candidate
                            Log.Warn($"VideoBridgeClient[{label}] resyncing framing");
                            continue;
                        }
                        uint size = ByteOps.ReadU32LE(header, 4);
                        int width = 0;
                        int height = 0;
                        if (isRaw)
                        {
                            if (!ReadFully(ns, rawMeta, 0, 4)) break;
                            width = ByteOps.ReadU16LE(rawMeta, 0);
                            height = ByteOps.ReadU16LE(rawMeta, 2);
                        }
                        if (size == 0 || size > 8 * 1024 * 1024)
                        {
                            Log.Warn($"VideoBridgeClient[{label}] bad frame size {size}");
                            break;
                        }
                        if (isRaw && (width <= 0 || height <= 0 || size != (uint)(width * height * 3)))
                        {
                            Log.Warn($"VideoBridgeClient[{label}] bad raw frame {width}x{height} size {size}");
                            break;
                        }
                        byte[] frame = isRaw ? RentRawFrame((int)size) : new byte[size];
                        if (!ReadFully(ns, frame, 0, (int)size))
                        {
                            if (isRaw) ReturnRawFrame(frame);
                            break;
                        }
                        lock (_frameLock)
                        {
                            if (_hasPending)
                            {
                                if (_pendingRaw) ReturnRawFrame(_pendingFrame);
                                framesDropped++;
                            }
                            _pendingFrame = frame;
                            _pendingRaw = isRaw;
                            _pendingWidth = width;
                            _pendingHeight = height;
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
            // Apply the newest decoded frame. The receiver thread keeps
            // draining TCP and overwrites pending frames so we avoid replaying
            // a stale backlog on the main thread.
            byte[] frame = null;
            bool isRaw = false;
            int width = 0;
            int height = 0;
            lock (_frameLock)
            {
                if (_hasPending)
                {
                    frame = _pendingFrame;
                    isRaw = _pendingRaw;
                    width = _pendingWidth;
                    height = _pendingHeight;
                    _pendingFrame = null;
                    _hasPending = false;
                }
            }
            if (frame == null) return;
            if (destTexture == null) return;
            try
            {
                if (isRaw)
                {
                    if (destTexture.width != width || destTexture.height != height ||
                        destTexture.format != TextureFormat.RGB24)
                    {
                        if (!destTexture.Reinitialize(width, height, TextureFormat.RGB24, false))
                        {
                            Log.Warn($"VideoBridgeClient[{label}] texture resize failed");
                            return;
                        }
                    }
                    destTexture.LoadRawTextureData(frame);
                    destTexture.Apply(updateMipmaps: false, makeNoLongerReadable: false);
                }
                else if (!ImageConversion.LoadImage(destTexture, frame, markNonReadable: false))
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
            finally
            {
                if (isRaw) ReturnRawFrame(frame);
            }
        }

        private byte[] RentRawFrame(int size)
        {
            lock (_poolLock)
            {
                for (int i = _rawPool.Count - 1; i >= 0; i--)
                {
                    byte[] candidate = _rawPool[i];
                    if (candidate.Length == size)
                    {
                        _rawPool.RemoveAt(i);
                        return candidate;
                    }
                }
            }
            return new byte[size];
        }

        private void ReturnRawFrame(byte[] frame)
        {
            if (frame == null) return;
            lock (_poolLock)
            {
                if (_rawPool.Count < 3) _rawPool.Add(frame);
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
