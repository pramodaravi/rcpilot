"""rcpilot — control + video + telemetry stack for the Full Throttle Karting RC arcade.

Top-level package. Submodules:
    rcpilot.protocol   — wire format (control packets, echo packets, CRC).
    rcpilot.config     — YAML config loading + dataclasses.
    rcpilot.jetson     — code that runs on the car (Jetson SoM).
    rcpilot.cockpit    — code that runs at the sim cockpit (Windows / Mac / Linux).
"""

__version__ = "0.2.0"
