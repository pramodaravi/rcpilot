"""Joystick / steering-wheel abstraction for the cockpit.

Wraps pygame (or pygame-ce — they're API-compatible) so the rest of the
codebase doesn't have to know whether the input device is a hobby Xbox
controller or a real Asetek / Simucube DD wheel.

Run the standalone identify utility to figure out your axis layout::

    rcpilot-identify-joystick

It prints live axis values so you can wiggle each control and see which
axis number it lives on, then plug those numbers into ``config/local.yaml``.
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass

try:
    import pygame
except ImportError as exc:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@dataclass
class AxisReading:
    """A single sample of all four control inputs, normalized to canonical ranges."""

    steering: float  # -1.0 (left) .. +1.0 (right)
    throttle: float  # 0.0 .. 1.0
    brake: float     # 0.0 .. 1.0
    clutch: float    # 0.0 .. 1.0


class JoystickAdapter:
    """Reads a pygame-compatible joystick and converts raw axis values to
    the canonical control ranges defined in ``rcpilot.protocol.ControlPacket``.

    The axis indices and inversion flags come from a
    ``rcpilot.config.JoystickAxisMap`` instance — typically loaded from
    ``config/local.yaml``.
    """

    def __init__(
        self,
        joystick_index: int,
        axes: _AxisMapLike,
    ) -> None:
        if pygame is None:  # pragma: no cover
            raise RuntimeError(
                "pygame is not installed. Run `pip install pygame-ce` "
                f"on the cockpit machine. Original ImportError: {_IMPORT_ERROR}"
            )
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            raise RuntimeError(
                "No joystick detected. Plug in the wheel/controller before starting."
            )
        if joystick_index >= pygame.joystick.get_count():
            raise RuntimeError(
                f"joystick_index={joystick_index} but only "
                f"{pygame.joystick.get_count()} device(s) detected."
            )
        self._js = pygame.joystick.Joystick(joystick_index)
        # pygame-ce 2.5 deprecates Joystick.init() — recent versions auto-init,
        # but call it for backward compat with older pygame builds and silence
        # the warning where it's not actually fatal.
        with contextlib.suppress(Exception):
            self._js.init()
        self._axes = axes

    @property
    def device_name(self) -> str:
        return self._js.get_name()

    @property
    def num_axes(self) -> int:
        return self._js.get_numaxes()

    def read(self) -> AxisReading:
        """Pump pygame events and read all four axes once."""
        pygame.event.pump()
        steer = _clamp(self._raw_axis(self._axes.steering), -1.0, 1.0)
        thr = _pedal(self._raw_axis(self._axes.throttle), self._axes.throttle_inverted)
        brk = _pedal(self._raw_axis(self._axes.brake), self._axes.brake_inverted)
        clu = _pedal(self._raw_axis(self._axes.clutch), self._axes.clutch_inverted)
        return AxisReading(steering=steer, throttle=thr, brake=brk, clutch=clu)

    def close(self) -> None:
        try:
            self._js.quit()
        except Exception:  # noqa: BLE001
            pass
        pygame.joystick.quit()
        pygame.quit()

    def _raw_axis(self, idx: int) -> float:
        if idx < 0 or idx >= self._js.get_numaxes():
            return 0.0
        return self._js.get_axis(idx)


class _AxisMapLike:
    """Structural type for the axis map fields we care about. Avoids a hard
    import of rcpilot.config in this module so the joystick abstraction can
    be tested in isolation."""

    steering: int
    throttle: int
    brake: int
    clutch: int
    throttle_inverted: bool
    brake_inverted: bool
    clutch_inverted: bool


# ---- Helpers --------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _pedal(raw: float, inverted: bool) -> float:
    """Convert a -1.0..+1.0 trigger / pedal axis to a 0.0..1.0 pedal value."""
    normalized = (raw + 1.0) * 0.5
    if inverted:
        normalized = 1.0 - normalized
    return _clamp(normalized, 0.0, 1.0)


# ---- Standalone "identify" entry point -----------------------------------


def identify_main(argv: list[str] | None = None) -> int:
    """Print live axis values for every detected joystick.

    Wired up as the ``rcpilot-identify-joystick`` console script. Use it
    once on each cockpit machine before configuring the axis map.
    """
    import argparse  # local import: keep startup fast for the main path

    parser = argparse.ArgumentParser(
        prog="rcpilot-identify-joystick",
        description="Print live axis values to identify your wheel/controller layout.",
    )
    parser.add_argument("--joy", type=int, default=0, help="Joystick index (default 0)")
    args = parser.parse_args(argv)

    if pygame is None:
        sys.exit(
            "pygame is not installed. Run `pip install pygame-ce` "
            f"first. Original error: {_IMPORT_ERROR}"
        )

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        sys.exit("No joystick detected. Plug in the wheel/controller and try again.")

    js = pygame.joystick.Joystick(args.joy)
    with contextlib.suppress(Exception):
        js.init()

    print(f"Device:  {js.get_name()}")
    print(f"Axes:    {js.get_numaxes()}")
    print(f"Buttons: {js.get_numbuttons()}")
    print(f"Hats:    {js.get_numhats()}")
    print()
    print("Wiggle each control to identify which axis it lives on. Ctrl-C to exit.")
    print()

    try:
        while True:
            pygame.event.pump()
            axes_text = "  ".join(
                f"a{i}={js.get_axis(i):+.3f}" for i in range(js.get_numaxes())
            )
            held = ",".join(
                str(i) for i in range(js.get_numbuttons()) if js.get_button(i)
            ) or "-"
            print(f"\r{axes_text}  | btn={held}        ", end="", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        print()
    finally:
        with contextlib.suppress(Exception):
            js.quit()
        pygame.joystick.quit()
        pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(identify_main())


__all__ = ["AxisReading", "JoystickAdapter", "identify_main"]
