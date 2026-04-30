"""Tests for rcpilot.config — defaults, YAML overrides, missing-file behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from rcpilot import config as config_module
from rcpilot.config import Config, NetworkConfig, VideoConfig


def test_defaults_match_dataclasses() -> None:
    cfg = config_module.load(path=None)  # Will fall through to defaults if no config dir.
    # The exact default values come from the dataclasses; we just check the
    # nested structure is wired up.
    assert isinstance(cfg.network, NetworkConfig)
    assert isinstance(cfg.video, VideoConfig)
    assert cfg.network.control_port == 5005
    assert cfg.video.encoder == "x264enc"


def test_load_from_explicit_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(
        """
network:
  jetson_ip: 10.0.0.42
  control_port: 9999
control:
  rate_hz: 125
video:
  encoder: nvv4l2h264enc
  bitrate_kbps: 15000
"""
    )
    cfg = config_module.load(yaml_path)
    assert cfg.network.jetson_ip == "10.0.0.42"
    assert cfg.network.control_port == 9999
    # Unspecified keys keep their defaults.
    assert cfg.network.cockpit_ip == "192.168.1.247"
    assert cfg.control.rate_hz == 125
    assert cfg.control.watchdog_ms == 200  # default
    assert cfg.video.encoder == "nvv4l2h264enc"
    assert cfg.video.bitrate_kbps == 15000
    assert cfg.video.framerate == 60  # default


def test_load_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_path = tmp_path / "envvar.yaml"
    yaml_path.write_text("network:\n  jetson_ip: 1.2.3.4\n")
    monkeypatch.setenv("RCPILOT_CONFIG", str(yaml_path))
    cfg = config_module.load()
    assert cfg.network.jetson_ip == "1.2.3.4"


def test_load_unknown_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-file.yaml"
    with pytest.raises(FileNotFoundError):
        config_module.load(missing)


def test_load_envvar_pointing_to_missing_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RCPILOT_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(FileNotFoundError):
        config_module.load()


def test_unknown_yaml_keys_are_ignored(tmp_path: Path) -> None:
    yaml_path = tmp_path / "extra.yaml"
    yaml_path.write_text(
        """
network:
  jetson_ip: 5.6.7.8
  this_key_does_not_exist: hello
totally_made_up_section:
  whatever: 42
"""
    )
    cfg = config_module.load(yaml_path)
    assert cfg.network.jetson_ip == "5.6.7.8"
    # Should not raise; unknown keys silently dropped.


def test_nested_partial_override_keeps_other_axes_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "axes.yaml"
    yaml_path.write_text(
        """
control:
  axes:
    throttle_inverted: true
"""
    )
    cfg = config_module.load(yaml_path)
    assert cfg.control.axes.throttle_inverted is True
    # Default axis indices retained.
    assert cfg.control.axes.steering == 0
    assert cfg.control.axes.throttle == 5
    assert cfg.control.axes.brake_inverted is False


def test_default_yaml_in_repo_loads() -> None:
    """The committed config/default.yaml must always parse cleanly."""
    repo_root = Path(__file__).resolve().parents[1]
    default_yaml = repo_root / "config" / "default.yaml"
    if not default_yaml.is_file():
        pytest.skip("config/default.yaml not present in this checkout")
    cfg = config_module.load(default_yaml)
    assert isinstance(cfg, Config)
    # The default YAML mirrors the dataclass defaults — sanity-check a few.
    assert cfg.network.jetson_ip == "192.168.1.53"
    assert cfg.video.sensor_mode == 4
