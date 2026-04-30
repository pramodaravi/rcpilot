"""Config loading for rcpilot.

Configuration is YAML, in ``config/default.yaml`` (committed) or
``config/local.yaml`` (per-host overrides, gitignored). The lookup order
when no explicit path is passed:

    1. ``RCPILOT_CONFIG`` environment variable (absolute or relative path)
    2. ``./config/local.yaml`` if it exists
    3. ``./config/default.yaml`` if it exists
    4. Built-in defaults (the dataclass defaults below)

Any keys missing from the YAML file fall back to the dataclass defaults, so a
``local.yaml`` only needs to contain the values it wants to override.

Example minimal ``local.yaml``::

    network:
      jetson_ip: 10.0.0.42
    control:
      joystick_index: 1
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

# ---- Config dataclasses ---------------------------------------------------


@dataclass
class NetworkConfig:
    """Where things live on the network."""

    jetson_ip: str = "192.168.1.53"
    """Jetson's address. Default is the bench Wi-Fi IP on the Starlink subnet.
    Set to 192.168.55.1 (USB-C virtual ethernet) for cabled bench bring-up."""

    cockpit_ip: str = "192.168.1.247"
    """Cockpit's address as seen by the Jetson; used as the video udpsink target.
    Set to 192.168.55.100 (USB-C virtual ethernet) for cabled bench bring-up."""

    control_port: int = 5005
    """UDP port for control packets (cockpit → car) and echoes (car → cockpit)."""

    video_port: int = 5004
    """UDP port for the H.264 RTP video stream (car → cockpit)."""


@dataclass
class JoystickAxisMap:
    """Which joystick axis carries which control input.

    Defaults are correct for an Xbox One controller under SDL2/pygame on
    Windows. The Asetek Forte Tony Kanaan wheelbase under SimSports software
    presents differently — typically axis 0 is steering, but pedal axes
    depend on which pedal set is connected. Run
    ``rcpilot-identify-joystick`` to see live axis values for your device.
    """

    steering: int = 0
    throttle: int = 5
    brake: int = 4
    clutch: int = 1

    # Some pedal sets report -1.0 fully-released and +1.0 fully-pressed; others
    # invert that. Set to ``True`` if a pedal reads the wrong way.
    throttle_inverted: bool = False
    brake_inverted: bool = False
    clutch_inverted: bool = False


@dataclass
class ControlConfig:
    """Cockpit control sender behaviour."""

    rate_hz: int = 250
    """Target send rate. 250 Hz matches what arcade-grade DD wheels poll at."""

    watchdog_ms: int = 200
    """Car-side failsafe: brake / coast if no packet arrives within this many ms."""

    joystick_index: int = 0
    """Which joystick to use if multiple are connected."""

    axes: JoystickAxisMap = field(default_factory=JoystickAxisMap)


@dataclass
class VideoConfig:
    """Video pipeline parameters. These are read by the start_video.sh script
    via env-var export, not directly by the Python code today — but keeping
    them here means everything lives in one config file."""

    width: int = 1280
    height: int = 720
    framerate: int = 60
    bitrate_kbps: int = 8000
    sensor_mode: int = 4
    """IMX219 sensor mode. 4 = 1280x720@60. See start_video.sh for the full list."""

    encoder: str = "x264enc"
    """Software encoder while we wait for the Orin NX module. Switch to
    ``nvv4l2h264enc`` after the NX swap to get hardware NVENC."""


@dataclass
class Config:
    """Top-level rcpilot config."""

    network: NetworkConfig = field(default_factory=NetworkConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    video: VideoConfig = field(default_factory=VideoConfig)


# ---- Loading --------------------------------------------------------------


_DEFAULT_LOOKUP_PATHS = (
    Path("config/local.yaml"),
    Path("config/default.yaml"),
)


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config, applying YAML overrides over the dataclass defaults."""
    resolved = _resolve_path(path)
    if resolved is None:
        return Config()

    with open(resolved, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cfg = Config()
    _apply_overrides(cfg, data)
    return cfg


def _resolve_path(path: str | os.PathLike[str] | None) -> Path | None:
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p

    env_path = os.environ.get("RCPILOT_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.is_file():
            raise FileNotFoundError(f"RCPILOT_CONFIG points to missing file: {p}")
        return p

    for candidate in _DEFAULT_LOOKUP_PATHS:
        if candidate.is_file():
            return candidate

    return None


def _apply_overrides(target: Any, overrides: dict[str, Any]) -> None:
    """Recursively apply a dict of overrides onto a dataclass instance.

    Unknown keys are silently ignored — that matches YAML's "easy to extend"
    feel and prevents an old config file from breaking when we add a new
    section. Raise on type mismatches if you'd prefer the opposite.
    """
    if not is_dataclass(target):
        return
    for f in fields(target):
        if f.name not in overrides:
            continue
        new_value = overrides[f.name]
        current = getattr(target, f.name)
        if is_dataclass(current) and isinstance(new_value, dict):
            _apply_overrides(current, new_value)
        else:
            setattr(target, f.name, new_value)


__all__ = [
    "Config",
    "ControlConfig",
    "JoystickAxisMap",
    "NetworkConfig",
    "VideoConfig",
    "load",
]
