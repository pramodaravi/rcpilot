#!/usr/bin/env python3
"""Pluggable foreground-segmentation providers for the stitch seam.

The stitcher's job in the overlap region is "decide which camera to trust per
pixel". The historical answer was a feathered distance-transform blend, which
ghosts hands. The 2026-05-02 answer was an absdiff-based foreground snap that
detects pixel disagreement and snaps the seam to one camera there. Both
approaches share a flaw: they have no idea what they're looking at. They just
react to differences.

This module defines a `SegmentationProvider` interface that returns a binary
mask at the warped image resolution. Mask pixels == 255 mean "this is a
foreground / parallax-affected region — snap the blend weights to one
camera here instead of feathering". Different providers can be selected via
the `RCPILOT_STITCH_SEG` env var:

    off       — never returns a mask. Stitcher uses the baked feather only.
    absdiff   — refactored version of the current update_foreground_snap.
    skin      — HSV skin-tone heuristic. No ML; works on any Jetson without
                a model file installed. Catches hands well, false-triggers on
                skin-colored backgrounds. Useful as a stop-gap.
    yolo26    — Ultralytics YOLO26-seg with class filter (default: "person",
                we mask anything human in the overlap). Falls back to 'absdiff'
                if Ultralytics is not installed.

Providers self-cadence — they decide whether to refresh the mask on the
current frame, and may return `None` to mean "reuse last mask". The stitcher
treats the mask as authoritative for the overlap region: where the mask is
255, blend weights snap to one camera; everywhere else, the baked feather
weights apply.

Design tenets:
  - No provider may import a heavy/optional dependency at module import time.
    Imports happen inside the provider's __init__ so RCPILOT_STITCH_SEG=off
    on a Jetson without Ultralytics never trips an import error.
  - Providers must work on uint8 BGR ndarrays at the WARPED (output) image
    resolution — not the source camera resolution. The stitcher already has
    warped_left and warped_right when it calls in.
  - Providers must be deterministic given the same input pair, so the parity
    test in tests/test_seg_seam.py can pin behavior.
"""

from __future__ import annotations

import os
import time
from typing import Optional, Protocol

import numpy as np

try:
    import cv2  # noqa: F401
except ImportError:  # pragma: no cover — never on Jetson, only on minimal CI
    cv2 = None  # type: ignore


class SegmentationProvider(Protocol):
    """Returns a uint8 mask (0/255) at warped image resolution.

    A return value of None means "no update this frame" — the stitcher will
    keep using whatever mask was returned last.
    """

    name: str

    def mask(
        self,
        warped_left: np.ndarray,
        warped_right: np.ndarray,
        frame_idx: int,
        overlap_mask: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class OffProvider:
    """Always returns None. The stitcher will keep its baked feather weights."""

    name = "off"

    def mask(self, warped_left, warped_right, frame_idx, overlap_mask=None):
        return None


class AbsdiffProvider:
    """Pixel-disagreement foreground detector — equivalent to the legacy
    `update_foreground_snap` math, refactored out of stitch_video.py.

    Cheap (~2-3 ms on Orin Nano at 2560x720). Misses hands when the hand and
    the background behind it have similar luma. Triggers spuriously on
    parallax-shifted high-frequency texture even when there's no foreground.

    Tunables (env):
        RCPILOT_STITCH_ABSDIFF_THRESHOLD (default 30) — luma diff to call FG
        RCPILOT_STITCH_ABSDIFF_DILATE_PX (default 6)  — mask dilation radius
        RCPILOT_STITCH_ABSDIFF_EVERY_N   (default 10) — refresh cadence
    """

    name = "absdiff"

    def __init__(self) -> None:
        if cv2 is None:
            raise RuntimeError("AbsdiffProvider requires cv2")
        self.threshold = _env_int("RCPILOT_STITCH_ABSDIFF_THRESHOLD", 30)
        self.dilate_px = _env_int("RCPILOT_STITCH_ABSDIFF_DILATE_PX", 6)
        self.every_n = max(1, _env_int("RCPILOT_STITCH_ABSDIFF_EVERY_N", 10))
        self._kernel = (
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (self.dilate_px * 2 + 1, self.dilate_px * 2 + 1),
            )
            if self.dilate_px > 0
            else None
        )
        # Pre-allocated scratch (set lazily on first call when shapes are known)
        self._gray_l: Optional[np.ndarray] = None
        self._gray_r: Optional[np.ndarray] = None
        self._diff: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None

    def _ensure_buffers(self, h: int, w: int) -> None:
        if self._gray_l is None or self._gray_l.shape != (h, w):
            self._gray_l = np.empty((h, w), dtype=np.uint8)
            self._gray_r = np.empty((h, w), dtype=np.uint8)
            self._diff = np.empty((h, w), dtype=np.uint8)
            self._mask = np.empty((h, w), dtype=np.uint8)

    def mask(self, warped_left, warped_right, frame_idx, overlap_mask=None):
        if (frame_idx % self.every_n) != 0:
            return None
        h, w = warped_left.shape[:2]
        self._ensure_buffers(h, w)
        cv2.cvtColor(warped_left, cv2.COLOR_BGR2GRAY, dst=self._gray_l)
        cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY, dst=self._gray_r)
        cv2.absdiff(self._gray_l, self._gray_r, dst=self._diff)
        cv2.threshold(
            self._diff, self.threshold, 255, cv2.THRESH_BINARY, dst=self._mask,
        )
        if self._kernel is not None:
            cv2.dilate(self._mask, self._kernel, dst=self._mask)
        if overlap_mask is not None:
            cv2.bitwise_and(self._mask, overlap_mask, dst=self._mask)
        return self._mask


class SkinColorProvider:
    """HSV skin-tone segmentation. Catches hands without an ML model.

    Runs on the LEFT warped image only — the assumption is that the hand
    enters the cockpit view from the driver's side, which is the camera we
    want to mask. If your geometry is mirrored, set
    RCPILOT_STITCH_SKIN_SOURCE=right.

    Failure modes (which is why this is a stop-gap, not the v1 answer):
      - Wood/leather/orange backgrounds get masked too.
      - White or gloved hands miss entirely.
      - Lighting drift past the HSV range silently disables the detection.

    Tunables:
        RCPILOT_STITCH_SKIN_HMIN/HMAX/SMIN/SMAX/VMIN/VMAX — HSV range
        RCPILOT_STITCH_SKIN_SOURCE   left|right (default left)
        RCPILOT_STITCH_SKIN_DILATE_PX (default 8)
        RCPILOT_STITCH_SKIN_EVERY_N   (default 5)
        RCPILOT_STITCH_SKIN_MIN_AREA  (default 800) — pixels; below this we
                                                     return an empty mask to
                                                     avoid HSV noise triggers.
    """

    name = "skin"

    def __init__(self) -> None:
        if cv2 is None:
            raise RuntimeError("SkinColorProvider requires cv2")
        self.h_min = _env_int("RCPILOT_STITCH_SKIN_HMIN", 0)
        self.h_max = _env_int("RCPILOT_STITCH_SKIN_HMAX", 25)
        self.s_min = _env_int("RCPILOT_STITCH_SKIN_SMIN", 40)
        self.s_max = _env_int("RCPILOT_STITCH_SKIN_SMAX", 200)
        self.v_min = _env_int("RCPILOT_STITCH_SKIN_VMIN", 60)
        self.v_max = _env_int("RCPILOT_STITCH_SKIN_VMAX", 255)
        self.source = _env_str("RCPILOT_STITCH_SKIN_SOURCE", "left").lower()
        if self.source not in ("left", "right"):
            self.source = "left"
        self.dilate_px = _env_int("RCPILOT_STITCH_SKIN_DILATE_PX", 8)
        self.every_n = max(1, _env_int("RCPILOT_STITCH_SKIN_EVERY_N", 5))
        self.min_area = _env_int("RCPILOT_STITCH_SKIN_MIN_AREA", 800)
        self._kernel = (
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (self.dilate_px * 2 + 1, self.dilate_px * 2 + 1),
            )
            if self.dilate_px > 0
            else None
        )
        self._lower = np.array([self.h_min, self.s_min, self.v_min], dtype=np.uint8)
        self._upper = np.array([self.h_max, self.s_max, self.v_max], dtype=np.uint8)
        self._hsv: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None

    def _ensure_buffers(self, h: int, w: int) -> None:
        if self._hsv is None or self._hsv.shape[:2] != (h, w):
            self._hsv = np.empty((h, w, 3), dtype=np.uint8)
            self._mask = np.empty((h, w), dtype=np.uint8)

    def mask(self, warped_left, warped_right, frame_idx, overlap_mask=None):
        if (frame_idx % self.every_n) != 0:
            return None
        src = warped_left if self.source == "left" else warped_right
        h, w = src.shape[:2]
        self._ensure_buffers(h, w)
        cv2.cvtColor(src, cv2.COLOR_BGR2HSV, dst=self._hsv)
        cv2.inRange(self._hsv, self._lower, self._upper, dst=self._mask)
        if self._kernel is not None:
            cv2.dilate(self._mask, self._kernel, dst=self._mask)
        if overlap_mask is not None:
            cv2.bitwise_and(self._mask, overlap_mask, dst=self._mask)
        # Reject obvious noise — without this, a few stray skin-colored pixels
        # in cockpit fabric will jiggle the seam every refresh.
        if int(np.count_nonzero(self._mask)) < self.min_area:
            self._mask[:] = 0
        return self._mask


class Yolo26SegProvider:
    """Ultralytics YOLO26-seg via TensorRT engine.

    Loads a YOLO26-seg .engine (or .pt for dev) and produces a per-frame
    instance segmentation mask. By default we keep only the 'person' class
    and union all 'person' instances into one binary mask.

    LICENSE NOTE: Ultralytics YOLO is AGPL-3.0. This provider is fine for
    development; commercial deployment of a paid arcade attraction needs
    either an Ultralytics enterprise license or replacing this provider with
    a permissive alternative (RT-DETR Apache-2.0, or a TAO-trained model).
    The interface is designed so that swap is one new class plus a config
    flag — no stitcher changes.

    Tunables:
        RCPILOT_STITCH_YOLO_ENGINE   — path to .engine or .pt
        RCPILOT_STITCH_YOLO_CLASSES  — comma-separated class names to mask
                                       (default: 'person')
        RCPILOT_STITCH_YOLO_CONF     — confidence threshold (default 0.30)
        RCPILOT_STITCH_YOLO_IMGSZ    — inference image size (default 640)
        RCPILOT_STITCH_YOLO_EVERY_N  — refresh cadence (default 3 — much
                                       faster than absdiff/skin because the
                                       inference latency is the cap, not the
                                       postprocess)
        RCPILOT_STITCH_YOLO_DILATE_PX (default 4)
        RCPILOT_STITCH_YOLO_SOURCE   left|right (default left)
    """

    name = "yolo26"

    def __init__(self) -> None:
        if cv2 is None:
            raise RuntimeError("Yolo26SegProvider requires cv2")
        # Lazy-import so a missing ultralytics install only errors when this
        # provider is selected, not at module load.
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "RCPILOT_STITCH_SEG=yolo26 selected but ultralytics is not installed. "
                "Install with `pip install ultralytics`, or pick a different provider "
                "(RCPILOT_STITCH_SEG=absdiff is the v0.2 default)."
            ) from exc
        engine = _env_str("RCPILOT_STITCH_YOLO_ENGINE", "")
        if not engine:
            raise RuntimeError(
                "RCPILOT_STITCH_SEG=yolo26 requires RCPILOT_STITCH_YOLO_ENGINE to "
                "point at a .engine or .pt file."
            )
        if not os.path.exists(engine):
            raise RuntimeError(f"YOLO model file not found: {engine}")
        self._model = YOLO(engine)
        self._classes_text = _env_str("RCPILOT_STITCH_YOLO_CLASSES", "person")
        self._target_class_names = {
            c.strip() for c in self._classes_text.split(",") if c.strip()
        }
        self.conf = _env_float("RCPILOT_STITCH_YOLO_CONF", 0.30)
        self.imgsz = _env_int("RCPILOT_STITCH_YOLO_IMGSZ", 640)
        self.every_n = max(1, _env_int("RCPILOT_STITCH_YOLO_EVERY_N", 3))
        self.dilate_px = _env_int("RCPILOT_STITCH_YOLO_DILATE_PX", 4)
        self.source = _env_str("RCPILOT_STITCH_YOLO_SOURCE", "left").lower()
        if self.source not in ("left", "right"):
            self.source = "left"
        self._kernel = (
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (self.dilate_px * 2 + 1, self.dilate_px * 2 + 1),
            )
            if self.dilate_px > 0
            else None
        )
        # Resolve class names → ids once
        self._target_class_ids = self._resolve_class_ids()

    def _resolve_class_ids(self) -> Optional[set]:
        names = getattr(self._model, "names", None)
        if not names:
            return None
        if isinstance(names, dict):
            return {
                cid for cid, cname in names.items()
                if cname in self._target_class_names
            }
        return {cid for cid, cname in enumerate(names) if cname in self._target_class_names}

    def mask(self, warped_left, warped_right, frame_idx, overlap_mask=None):
        if (frame_idx % self.every_n) != 0:
            return None
        src = warped_left if self.source == "left" else warped_right
        h, w = src.shape[:2]
        # Ultralytics handles its own resize / preprocessing.
        results = self._model.predict(
            source=src,
            imgsz=self.imgsz,
            conf=self.conf,
            verbose=False,
            stream=False,
        )
        out = np.zeros((h, w), dtype=np.uint8)
        for r in results:
            seg_masks = getattr(r, "masks", None)
            if seg_masks is None or seg_masks.data is None:
                continue
            cls_ids = (
                r.boxes.cls.cpu().numpy().astype(int) if r.boxes is not None else []
            )
            mask_data = seg_masks.data.cpu().numpy()  # (N, mh, mw) bool/float
            for i, cid in enumerate(cls_ids):
                if (
                    self._target_class_ids is not None
                    and int(cid) not in self._target_class_ids
                ):
                    continue
                m = (mask_data[i] > 0.5).astype(np.uint8) * 255
                if m.shape != (h, w):
                    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
                cv2.bitwise_or(out, m, dst=out)
        if self._kernel is not None:
            cv2.dilate(out, self._kernel, dst=out)
        if overlap_mask is not None:
            cv2.bitwise_and(out, overlap_mask, dst=out)
        return out


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_PROVIDERS = {
    "off": OffProvider,
    "absdiff": AbsdiffProvider,
    "skin": SkinColorProvider,
    "yolo26": Yolo26SegProvider,
}


def get_provider(name: Optional[str] = None) -> SegmentationProvider:
    """Build the provider selected by RCPILOT_STITCH_SEG (or the `name` arg).

    Falls back to 'absdiff' (the v0.2 default behavior) if the requested
    provider can't be constructed — never silently to 'off', because
    silently disabling the seam fix would hide the regression. The fallback
    is logged via stderr.
    """
    selected = (name or _env_str("RCPILOT_STITCH_SEG", "absdiff")).lower()
    if selected not in _PROVIDERS:
        import sys
        print(
            f"[seg_seam] unknown RCPILOT_STITCH_SEG={selected!r}; "
            f"valid: {sorted(_PROVIDERS)}; falling back to absdiff",
            file=sys.stderr,
        )
        selected = "absdiff"
    cls = _PROVIDERS[selected]
    try:
        return cls()
    except Exception as exc:
        if selected == "absdiff":
            # absdiff itself failed — re-raise; we have no safer fallback.
            raise
        import sys
        print(
            f"[seg_seam] requested provider {selected!r} failed to init "
            f"({type(exc).__name__}: {exc}); falling back to absdiff",
            file=sys.stderr,
        )
        return AbsdiffProvider()


def list_providers() -> list:
    return sorted(_PROVIDERS)


# ---------------------------------------------------------------------------
# CLI helper for diagnostics — `python3 seg_seam.py --probe` prints what
# would be selected without running the full stitcher.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if "--probe" in sys.argv:
        sel = _env_str("RCPILOT_STITCH_SEG", "absdiff")
        print(f"RCPILOT_STITCH_SEG={sel}")
        try:
            p = get_provider()
            print(f"resolved provider: {p.name}")
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(0)
    print(f"providers available: {list_providers()}")
    print(
        "Use --probe to see which one the current env would select. "
        "Set RCPILOT_STITCH_SEG to choose."
    )
