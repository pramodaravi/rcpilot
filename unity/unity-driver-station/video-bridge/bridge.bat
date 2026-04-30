@echo off
rem V1 single-camera bridge launcher for the Windows cockpit machine.
rem RTP UDP 5004 -> raw RGB TCP 9000 (lowlatency preset). Matches
rem rcpilot/config/default.yaml on the Jetson side and
rem Assets/StreamingAssets/config.json in Unity. Pass --frame-format jpeg
rem to fall back to JPEG framing.
rem
rem Prereqs: Python 3.11 via the py launcher, then:
rem   py -3.11 -m pip install --only-binary=:all: -r requirements.txt
rem Run from the unity-driver-station folder, OR double-click this file.

setlocal
set SCRIPT_DIR=%~dp0
set PY=py -3.11
echo [bridge.bat] starting cam0 (RTP auto:5004 -> raw RGB TCP 127.0.0.1:9000, lowlatency preset)
%PY% "%SCRIPT_DIR%bridge.py" --in-port 5004 --out-port 9000 --bind 127.0.0.1 --rtp-bind auto --preset lowlatency --frame-format raw
endlocal
