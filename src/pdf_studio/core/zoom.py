"""Zoom model: modes, presets and stepping (Qt-free)."""

from __future__ import annotations

import enum
import re
from typing import Optional, Sequence, Tuple

from .layout import ViewMode, fit_page_zoom, fit_width_zoom

MIN_ZOOM = 0.10
MAX_ZOOM = 8.00
PRESETS: Tuple[float, ...] = (0.10, 0.25, 0.33, 0.50, 0.67, 0.75, 0.90, 1.00, 1.10, 1.25,
                              1.50, 1.75, 2.00, 2.50, 3.00, 4.00, 5.00, 6.00, 8.00)
#: "Automatic" zoom = fit width, but never larger than this
AUTO_MAX = 1.25
WHEEL_STEP = 1.12


class ZoomMode(str, enum.Enum):
    CUSTOM = "custom"
    AUTO = "auto"
    FIT_WIDTH = "fit_width"
    FIT_PAGE = "fit_page"
    ACTUAL = "actual"

    @property
    def label(self) -> str:
        return {"custom": "Custom", "auto": "Automatic", "fit_width": "Fit width",
                "fit_page": "Fit page", "actual": "Actual size"}[self.value]


def clamp(z: float) -> float:
    return max(MIN_ZOOM, min(MAX_ZOOM, z))


def zoom_in(z: float) -> float:
    for p in PRESETS:
        if p > z * 1.001:
            return p
    return MAX_ZOOM


def zoom_out(z: float) -> float:
    for p in reversed(PRESETS):
        if p < z * 0.999:
            return p
    return MIN_ZOOM


def wheel_zoom(z: float, notches: float) -> float:
    return clamp(z * (WHEEL_STEP ** notches))


def resolve(mode: ZoomMode, current: float, sizes: Sequence[Tuple[float, float]],
            viewport_w: float, viewport_h: float, view_mode: ViewMode, page: int,
            scrollbar_w: int = 0) -> float:
    """Zoom factor for a mode given the viewport."""
    if mode == ZoomMode.FIT_WIDTH:
        return clamp(fit_width_zoom(sizes, viewport_w, view_mode, page, scrollbar=scrollbar_w))
    if mode == ZoomMode.FIT_PAGE:
        return clamp(fit_page_zoom(sizes, viewport_w, viewport_h, view_mode, page))
    if mode == ZoomMode.AUTO:
        return clamp(min(AUTO_MAX, fit_width_zoom(sizes, viewport_w, view_mode, page,
                                                  scrollbar=scrollbar_w)))
    if mode == ZoomMode.ACTUAL:
        return 1.0
    return clamp(current)


def format_percent(z: float) -> str:
    pct = z * 100
    return f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%"


_PCT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*%?\s*$")


def parse_percent(text: str) -> Optional[float]:
    m = _PCT_RE.match(text or "")
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ".")) / 100.0
    except ValueError:
        return None
    if value <= 0:
        return None
    return clamp(value)
