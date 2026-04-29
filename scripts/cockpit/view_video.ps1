# rcpilot — cockpit video receiver (Windows PowerShell).
#
# Receives the RTP/H.264 UDP stream from the Jetson and displays it with no
# jitter buffer so glass-to-glass latency measurements are clean. Press 'q'
# in the video window or Ctrl+C in PowerShell to stop.
#
# Usage:
#   .\view_video.ps1                  # uses default port 5004
#   .\view_video.ps1 -Port 5004
#
# Requires GStreamer for Windows (Complete MSVC 64-bit installer). If
# `gst-launch-1.0` isn't on PATH, this script tries the standard install
# locations as a fallback.

[CmdletBinding()]
param(
    [int]$Port = 5004
)

$ErrorActionPreference = "Stop"

function Resolve-GstLaunch {
    $candidates = @(
        "gst-launch-1.0",
        "C:\Program Files\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe",
        "C:\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe",
        "C:\Program Files\gstreamer\1.0\mingw_x86_64\bin\gst-launch-1.0.exe"
    )
    foreach ($candidate in $candidates) {
        $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($resolved) { return $resolved.Source }
    }
    Write-Error @"
gst-launch-1.0 not found. Install GStreamer for Windows (Complete MSVC
64-bit installer) from https://gstreamer.freedesktop.org/download/ and
either select 'Add to PATH' during install or pass the path to the binary
explicitly.
"@
}

$gst = Resolve-GstLaunch

Write-Host "===================================================="
Write-Host "  rcpilot video receiver"
Write-Host "  Listening:  0.0.0.0:$Port"
Write-Host "  Decoder:    avdec_h264 (software)"
Write-Host "  Latency:    no jitter buffer"
Write-Host "  Press q in video window or Ctrl-C here to stop."
Write-Host "===================================================="

# udpsrc → rtph264depay → h264parse → avdec_h264 → videoconvert → autovideosink.
# autovideosink picks d3d11videosink on Windows, which is the lowest-latency
# native sink. sync=false stops it from frame-pacing against any clock.

& $gst -v `
    udpsrc port=$Port caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" `
    ! rtph264depay `
    ! h264parse `
    ! avdec_h264 `
    ! videoconvert `
    ! autovideosink sync=false
