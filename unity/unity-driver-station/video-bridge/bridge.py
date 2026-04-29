"""RTP H.264 → JPEG TCP bridge for the Unity driver station.

V1 is single-camera: the Jetson sends RTP H.264 to UDP 5004 (matches
rcpilot/config/default.yaml on the Jetson). Unity's built-in H.264 decoding
is flaky for live low-latency RTP, so this sidecar:

    1. receives the RTP stream via PyAV (FFmpeg under the hood)
    2. drops old frames to keep latency bounded (~1 frame buffer)
    3. re-encodes each frame as a small JPEG
    4. serves JPEG frames over TCP to Unity, with 8-byte framing:

         4 bytes "JFRM"
         4 bytes little-endian uint  (jpeg byte length)
         N bytes                      (jpeg data)

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
and you're done. The wire format to Unity is byte-for-byte unchanged.

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


MAGIC = b"JFRM"

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
    with os.fdopen(fd, "w") as f:
        f.write(sdp)
    return path


def frame_reader(in_port: int, stop_evt: threading.Event, frame_box: dict,
                 scale: float) -> None:
    """Background thread: pull frames from the RTP stream via PyAV.

    Keeps only the latest decoded frame in frame_box. Scales during decode
    reformat (swscale) rather than in numpy — much cheaper per frame.
    """
    log = logging.getLogger(f"rx:{in_port}")
    sdp_path = write_sdp(in_port)

    # Reopen on failure — the Jetson may not be streaming yet.
    while not stop_evt.is_set():
        try:
            container = av.open(
                sdp_path,
                format="sdp",
                options={
                    # Let FFmpeg open udp:// URLs referenced by the sdp.
                    "protocol_whitelist": "file,udp,rtp",
                    # Small reorder queue soaks up a stray out-of-order RTP
                    # packet without adding meaningful latency.
                    "reorder_queue_size": "50",
                    # 0.5 s socket timeout so we notice a stream stall quickly.
                    "stimeout": "500000",
                    # Low-latency flags: no buffering past what's needed to decode.
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "max_delay": "0",
                },
            )
        except av.AVError as e:
            log.warning(f"av.open failed ({e}), retrying in 2 s")
            time.sleep(2)
            continue

        log.info("RTP stream open, decoding frames")
        try:
            stream = container.streams.video[0]
            # Threaded decode: worth it on Apple Silicon even for 720p.
            stream.thread_type = "AUTO"

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
                except av.AVError as e:
                    # Single packet failed to decode — ignore, continue.
                    log.debug(f"decode error (skipped): {e}")
                    continue
        except av.AVError as e:
            log.warning(f"demux error: {e} — reconnecting")
        finally:
            try:
                container.close()
            except Exception:
                pass
        time.sleep(0.5)


def serve_one_client(conn: socket.socket, frame_box: dict, quality: int,
                     target_hz: float, log: logging.Logger) -> None:
    """Per-client thread: JPEG-encode and send frames at target_hz.

    Encoding is done with Pillow — C-implemented libjpeg, ~1-2 ms per frame
    at half-res/quality 70 on Apple Silicon. Plenty of headroom at 60 Hz.
    """
    period = 1.0 / max(1.0, target_hz)
    next_send = time.monotonic()
    last_count = -1

    conn.settimeout(0.5)
    try:
        while True:
            now = time.monotonic()
            sleep_for = next_send - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            next_send += period

            frame = frame_box.get("latest")
            count = frame_box.get("count", 0)
            if frame is None or count == last_count:
                # No new frame yet — skip to avoid bandwidth on dupes. Unity
                # keeps painting the last frame on its side.
                continue
            last_count = count

            # frame is HxWx3 RGB uint8 (from reformat above).
            img = Image.fromarray(frame, mode="RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=False)
            data = buf.getvalue()

            header = MAGIC + struct.pack("<I", len(data))
            try:
                conn.sendall(header + data)
            except (BrokenPipeError, ConnectionResetError, socket.timeout) as e:
                log.info(f"client disconnected: {e}")
                return
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-port", type=int, default=5004,
                   help="RTP UDP port to receive on (default 5004 matches "
                        "rcpilot/config/default.yaml)")
    p.add_argument("--out-port", type=int, default=9000,
                   help="TCP port Unity will connect to")
    p.add_argument("--bind", default="127.0.0.1",
                   help="TCP bind address (127.0.0.1 = local Unity only)")
    p.add_argument("--quality", type=int, default=70,
                   help="JPEG quality 1..100 (70 is a good sim/latency balance)")
    p.add_argument("--hz", type=float, default=60.0,
                   help="max JPEG publish rate")
    p.add_argument("--scale", type=float, default=0.5,
                   help="resize factor before JPEG encode (0.5 = half)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-12s %(levelname)s: %(message)s",
    )
    log = logging.getLogger("bridge")
    log.info(f"RTP in :{args.in_port}  JPEG TCP out :{args.out_port} "
             f"quality={args.quality} scale={args.scale} hz={args.hz}")

    stop_evt = threading.Event()
    frame_box: dict = {}

    reader = threading.Thread(
        target=frame_reader, args=(args.in_port, stop_evt, frame_box, args.scale),
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
                args=(conn, frame_box, args.quality, args.hz,
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
