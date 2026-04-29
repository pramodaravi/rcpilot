@echo off
rem V1 single-camera bridge launcher for the Windows cockpit machine.
rem RTP UDP 5004 -> JPEG TCP 9000. Matches rcpilot/config/default.yaml on
rem the Jetson side and Assets/StreamingAssets/config.json in Unity.
rem
rem Prereqs: python 3.10+ on PATH, pip install -r requirements.txt.
rem Run from the unity-driver-station folder, OR double-click this file.

setlocal
set SCRIPT_DIR=%~dp0
set PY=python
echo [bridge.bat] starting cam0 (RTP :5004 -> JPEG TCP :9000)
"%PY%" "%SCRIPT_DIR%bridge.py" --in-port 5004 --out-port 9000
endlocal
