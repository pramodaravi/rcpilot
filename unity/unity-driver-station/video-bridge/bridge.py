"""RTP H.264 → raw RGB (default) or JPEG TCP bridge for the Unity driver station.

V1 is single-camera: the Jetson sends RTP H.264 to UDP 5004 (matches
rcpilot/config/default.yaml on the Jetson). Unity's built-in H.264 decoding
is flaky for live low-latency RTP, so this sidecar:

    1. receives the RTP stream via PyAV (FFmpeg under the hood)
    2. drops old frames to keep latency bounded (~1 frame buffer)
    3. serves raw RGB frames over TCP to Unity by default, or JPEG when asked,
       with small binary frame headers:

         JPEG: 4 bytes "JFRM" + 4 bytes little-endian uint + JPEG bytes
         Raw : 4 bytes "RFRM" + 4 bytes little-endian uint + u16 width
               + u16 height + RGB24 bytes

Run:

    python bridge.py                                  # defaults: 5004 in, 9000 out
    python bridge.py --in-port 5004 --out-port 9000   # explicit

Or use bridge.sh / bridge.bat which set the same defaults.

A second camera (chase view, etc.) is a future feature — to enable it later,
launch a second copy on a distinct in-port + out-port pair, and set
config.video.cam1Port in the Unity config.json to the new TCP port.

## Why PyAV (was: OpenCV+GStreamer)

The previous Windows build relied on cv2.VideoCapture with OpenCV's GStreamer
backend, which meant installing a ~300 MB GStreamer runtime + plugins + the
opencv-contrib-python wheel. On macOS (especially Apple Silicon) that stack is
brittle: Homebrew's gstreamer doesn't auto-wire up to Python's opencv wheel,
and the contrib wheel isn't always built with GStreamer anyway. PyAV wraps
FFmpeg directly with prebuilt wheels for every Mac arch — one `pip install av`
and you're done. The JPEG wire format from the OpenCV days is preserved as a
fallback (--frame-format jpeg); raw RGB (RFRM) is the new default for lower
localhost latency.

## Latency budget

camera shutter → cockpit pixel:
    15 ms CSI+ISP + 3 ms NVENC + 5 ms wifi
  + 4 ms depay/decode here + 5 ms re-encode
  + 3 ms TCP + 8 ms Unity upload + 8 ms vsync
  ≈ 50-60 ms at 60 fps. Fine for a 1,100 ft indoor kart loop.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import socket
import struct
import sys
import tempfile
import threading
import time

import av
from PIL import Image  # PIL reads numpy arrays directly; numpy comes via PyAV


MAGIC_JPEG = b"JFRM"
MAGIC_RAW = b"RFRM"

# Minimal SDP describing the Jetson's H.264 RTP stream. Matches the caps that
# gst-launch's rtph264pay pt=96 produces on the sender side — same params the
# old GStreamer pipeline string asserted via udpsrc caps.
SDP_TEMPLATE = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=rc-pilot\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "t=0 0\r\n"
    "m=video {port} RTP/AVP 96\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 packetization-mode=1\r\n"
)


def write_sdp(in_port: int) -> str:
    """Write a minimal SDP for our H.264 RTP stream to a temp file.

    PyAV/FFmpeg wants the stream description as an SDP file on disk. We also
    flip on the protocol whitelist so FFmpeg lets the sdp file pull UDP.
    """
    sdp = SDP_TEMPLATE.format(port=in_port)
    fd, path = tempfile.mkstemp(prefix=f"rc-pilot-{in_port}-", suffix=".sdp")
    with os.fdopen(fd, "wb") as f:
        f.write(sdp.encode("ascii"))
    return path


def resolve_rtp_bind(requested_bind: str, jetson_ip: str, in_port: int,
                     log: logging.Logger) -> str:
    """Choose the local interface FFmpeg should bind for incoming RTP."""
    requested = (requested_bind or "auto").strip()
    if requested.lower() != "auto":
        return requested

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((jetson_ip, in_port))
            local_ip = probe.getsockname()[0]
    except OSError as e:
        log.warning(
            f"could not auto-detect RTP bind address for Jetson {jetson_ip}: "
            f"{e}; falling back to 0.0.0.0"
        )
        return "0.0.0.0"

    if not local_ip or local_ip.startswith("127."):
        log.warning(
            f"auto-detected RTP bind address was {local_ip or '<none>'}; "
            "falling back to 0.0.0.0"
        )
        return "0.0.0.0"

    return local_ip


def frame_reader(in_port: int, stop_evt: threading.Event, frame_box: dict,
                 scale: float, rtp_bind: str, reorder_queue: int,
                 udp_buffer: int) -> None:
    """Background thread: pull frames from the RTP stream via PyAV.

    Keeps only the latest decoded frame in frame_box. Scales during decode
    reformat (swscale) rather than in numpy — much cheaper per frame.
    """
    import traceback
    log = logging.getLogger(f"rx:{in_port}")
    sdp_path = write_sdp(in_port)
    last_log = time.monotonic()
    last_count = 0
    decode_errors = 0

    # Reopen on failure — the Jetson may not be streaming yet.
    while not stop_evt.is_set():
        open_options = {
            # Let FFmpeg open udp:// URLs referenced by the sdp.
            "protocol_whitelist": "file,udp,rtp",
            # Small reorder queue soaks up a stray out-of-order RTP
            # packet without adding meaningful latency.
            "reorder_queue_size": str(max(0, reorder_queue)),
            # 0.5 s socket timeout so we notice a stream stall quickly.
            "stimeout": "500000",
            # Keep the UDP and demux buffers small. If the app falls behind,
            # dropping old packets is better than faithfully playing stale video.
            "buffer_size": str(max(8192, udp_buffer)),
            "rtbufsize": str(max(8192, udp_buffer)),
            "overrun_nonfatal": "1",
            "probesize": "32",
            "analyzeduration": "0",
            # Low-latency flags: no buffering past what's needed to decode.
            "fflags": "nobuffer",
            "flags": "low_delay",
            "max_delay": "0",
        }
        if rtp_bind and rtp_bind not in ("0.0.0.0", "::"):
            open_options["localaddr"] = rtp_bind

        try:
            container = av.open(
                sdp_path,
                format="sdp",
                options=open_options,
            )
        except av.FFmpegError as e:
            log.warning(f"av.open failed ({e}), retrying in 2 s")
            time.sleep(2)
            continue

        log.info(f"RTP stream open on {rtp_bind}:{in_port}, decoding frames")
        try:
            stream = container.streams.video[0]
            # Frame threading can add decode latency. Slice threading keeps
            # parallelism where the codec supports it without queueing whole
            # frames behind the scenes.
            try:
                stream.thread_type = "SLICE"
            except ValueError:
                stream.thread_type = "NONE"

            # Precompute target size once we see the first frame (or from stream metadata).
            target_w = target_h = None

            for packet in container.demux(stream):
                if stop_evt.is_set():
                    break
                try:
                    for frame in packet.decode():
                        if target_w is None:
                            w0 = frame.width or stream.codec_context.width or 1280
                            h0 = frame.height or stream.codec_context.height or 720
                            if scale != 1.0:
                                target_w = max(2, int(w0 * scale) & ~1)  # even
                                target_h = max(2, int(h0 * scale) & ~1)
                            else:
                                target_w, target_h = w0, h0
                            log.info(f"decoding {w0}x{h0} → {target_w}x{target_h}")

                        # Reformat: scale + convert to RGB24 for PIL.
                        rgb = frame.reformat(
                            width=target_w, height=target_h, format="rgb24"
                        ).to_ndarray()

                        frame_box["latest"] = rgb
                        frame_box["count"] = frame_box.get("count", 0) + 1
                        frame_box["updated_at"] = time.monotonic()
                except Exception as e:
                    # Single packet failed to decode — log and continue.
                    decode_errors += 1
                    log.debug(f"decode error: {type(e).__name__}: {e}")
                    log.debug(traceback.format_exc())
                    now = time.monotonic()
                    if now - last_log >= 1.0:
                        delta = frame_box.get("count", 0) - last_count
                        last_count = frame_box.get("count", 0)
                        last_log = now
                        log.info(
                            f"decode: {delta} fps  total={last_count} "
                            f"errors={decode_errors}"
                        )
                        decode_errors = 0
                    continue

                # Per-second decode-rate heartbeat so we can SEE frames flowing.
                now = time.monotonic()
                if now - last_log >= 1.0:
                    delta = frame_box.get("count", 0) - last_count
                    last_count = frame_box.get("count", 0)
                    last_log = now
                    suffix = f" errors={decode_errors}" if decode_errors else ""
                    log.info(
                        f"decode: {delta} fps  total={last_count}{suffix}"
                    )
                    decode_errors = 0
        except Exception as e:
            log.warning(
                f"demux error: {type(e).__name__}: {e} — reconnecting"
            )
            log.debug(traceback.format_exc())
        finally:
            try:
                container.close()
            except Exception:
                pass
        time.sleep(0.5)


def serve_one_client(conn: socket.socket, frame_box: dict, quality: int,
                     target_hz: float, frame_format: str,
                     log: logging.Logger) -> None:
    """Per-client thread: JPEG-encode and send frames at target_hz.

    Encoding is done with Pillow — C-implemented libjpeg, ~1-2 ms per frame
    at half-res/quality 70 on Apple Silicon. Plenty of headroom at 60 Hz.
    """
    period = 1.0 / max(1.0, target_hz)
    next_send = time.monotonic()
    last_count = -1
    sent_in_window = 0
    last_log = time.monotonic()

    conn.settimeout(0.08)
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
    try:
        while True:
            now = time.monotonic()
            sleep_for = next_send - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            next_send += period

            # Per-second send-rate heartbeat (independent of whether we send).
            if now - last_log >= 1.0:
                count_now = frame_box.get("count", 0)
                log.info(
                    f"send: {sent_in_window} fps  framebox_count={count_now}"
                )
                sent_in_window = 0
                last_log = now

            frame = frame_box.get("latest")
            count = frame_box.get("count", 0)
            if frame is None or count == last_count:
                # No new frame yet — skip to avoid bandwidth on dupes. Unity
                # keeps painting the last frame on its side.
                continue
            last_count = count

            try:
                if frame_format == "raw":
                    h, w = frame.shape[:2]
                    size = int(frame.nbytes)
                    header = MAGIC_RAW + struct.pack("<IHH", size, w, h)
                    conn.sendall(header)
                    conn.sendall(memoryview(frame).cast("B"))
                else:
                    # frame is HxWx3 RGB uint8 (from reformat above).
                    img = Image.fromarray(frame, mode="RGB")
                    buf = io.BytesIO()
                    img.save(
                        buf,
                        format="JPEG",
                        quality=quality,
                        optimize=False,
                        subsampling=2,
                    )
                    data = buf.getvalue()
                    header = MAGIC_JPEG + struct.pack("<I", len(data))
                    conn.sendall(header + data)
                sent_in_window += 1
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.timeout) as e:
                log.info(f"client disconnected: {e}")
                return
            except Exception as e:
                log.warning(
                    f"send error: {type(e).__name__}: {e}"
                )
                return
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--preset", choices=("lowlatency", "fast", "balanced", "quality"),
                   default="lowlatency",
                   help="quality preset: lowlatency/fast=640x360, "
                        "balanced=960x540, quality=1280x720 for a 720p input")
    p.add_argument("--in-port", type=int, default=5004,
                   help="RTP UDP port to receive on (default 5004 matches "
                        "rcpilot/config/default.yaml)")
    p.add_argument("--out-port", type=int, default=9000,
                   help="TCP port Unity will connect to")
    p.add_argument("--bind", default="127.0.0.1",
                   help="TCP bind address (127.0.0.1 = local Unity only)")
    p.add_argument("--rtp-bind", default="auto",
                   help="local address for incoming RTP UDP. auto binds to "
                        "the interface used to reach --jetson-ip; 0.0.0.0 "
                        "listens on all interfaces")
    p.add_argument("--jetson-ip", default=os.getenv("RCPILOT_JETSON_IP", "192.168.55.1"),
                   help="Jetson address used only for --rtp-bind auto routing")
    p.add_argument("--frame-format", choices=("raw", "jpeg"), default="raw",
                   help="Unity bridge payload format. raw is lowest latency on localhost; "
                        "jpeg is a compatibility fallback")
    p.add_argument("--reorder-queue", type=int, default=0,
                   help="FFmpeg RTP reorder queue size. 0 favors latency over packet repair")
    p.add_argument("--udp-buffer", type=int, default=65536,
                   help="RTP UDP receive buffer in bytes")
    p.add_argument("--quality", type=int, default=None,
                   help="JPEG quality 1..100 (overrides --preset)")
    p.add_argument("--hz", type=float, default=None,
                   help="max JPEG publish rate")
    p.add_argument("--scale", type=float, default=None,
                   help="resize factor before JPEG encode (overrides --preset)")
    args = p.parse_args()

    presets = {
        "lowlatency": {"quality": 65, "scale": 0.50, "hz": 60.0},
        "fast": {"quality": 70, "scale": 0.50, "hz": 60.0},
        "balanced": {"quality": 76, "scale": 0.75, "hz": 45.0},
        "quality": {"quality": 88, "scale": 1.00, "hz": 60.0},
    }
    preset = presets[args.preset]
    if args.quality is None:
        args.quality = preset["quality"]
    if args.scale is None:
        args.scale = preset["scale"]
    if args.hz is None:
        args.hz = preset["hz"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-12s %(levelname)s: %(message)s",
    )
    log = logging.getLogger("bridge")
    rtp_bind = resolve_rtp_bind(args.rtp_bind, args.jetson_ip, args.in_port, log)
    log.info(f"RTP in {rtp_bind}:{args.in_port}  {args.frame_format} TCP out {args.bind}:{args.out_port} "
             f"preset={args.preset} quality={args.quality} "
             f"scale={args.scale} hz={args.hz} format={args.frame_format}")

    stop_evt = threading.Event()
    frame_box: dict = {}

    reader = threading.Thread(
        target=frame_reader,
        args=(args.in_port, stop_evt, frame_box, args.scale, rtp_bind,
              args.reorder_queue, args.udp_buffer),
        daemon=True,
    )
    reader.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.out_port))
    srv.listen(2)
    log.info("waiting for Unity to connect")

    try:
        while True:
            conn, addr = srv.accept()
            log.info(f"client connected from {addr}")
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(
                target=serve_one_client,
                args=(conn, frame_box, args.quality, args.hz, args.frame_format,
                      logging.getLogger(f"tx:{args.out_port}")),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        stop_evt.set()
        try:
            srv.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
