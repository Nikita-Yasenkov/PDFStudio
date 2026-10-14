"""Page layout for the viewer (Qt-free).

Computes page *slots* (rects in canvas px) for a view mode, zoom and
viewport, plus navigation helpers. The canvas paints only slots that
intersect the viewport.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .geometry import PX_PER_PT, PageTransform

MARGIN = 24     # canvas px around the content
GAP = 14        # canvas px between pages / rows
SPREAD_GAP = 8  # canvas px between the two pages of a spread


class ViewMode(str, enum.Enum):
    CONTINUOUS = "continuous"
    SINGLE = "single"
    TWO_PAGE = "two_page"

    @property
    def label(self) -> str:
        return {"continuous": "Continuous scroll", "single": "Single page",
                "two_page": "Two-page spread"}[self.value]


@dataclass(frozen=True)
class Slot:
    index: int
    x: float
    y: float
    w: float
    h: float

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def right(self) -> float:
        return self.x + self.w

    def transform(self, scale: float) -> PageTransform:
        return PageTransform(self.x, self.y, scale)

    def contains(self, cx: float, cy: float) -> bool:
        return self.x <= cx <= self.right and self.y <= cy <= self.bottom


@dataclass(frozen=True)
class Layout:
    slots: Tuple[Slot, ...]
    width: float
    height: float
    scale: float            # canvas px per point
    mode: ViewMode
    rows: Tuple[Tuple[int, ...], ...]  # page indices per row (1 or 2 each)

    def slot_for(self, index: int) -> Optional[Slot]:
        for s in self.slots:
            if s.index == index:
                return s
        return None

    def visible_slots(self, x: float, y: float, w: float, h: float) -> List[Slot]:
        out = []
        for s in self.slots:
            if s.bottom >= y and s.y <= y + h and s.right >= x and s.x <= x + w:
                out.append(s)
        return out

    def slot_at(self, cx: float, cy: float) -> Optional[Slot]:
        for s in self.slots:
            if s.contains(cx, cy):
                return s
        return None

    def nearest_slot(self, cx: float, cy: float) -> Optional[Slot]:
        best, best_d = None, None
        for s in self.slots:
            dx = max(s.x - cx, 0.0, cx - s.right)
            dy = max(s.y - cy, 0.0, cy - s.bottom)
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best, best_d = s, d
        return best


def spreads(count: int) -> List[Tuple[int, ...]]:
    """Pair pages for two-page view: (0,1), (2,3), …"""
    return [tuple(range(i, min(i + 2, count))) for i in range(0, count, 2)]


def rows_for(mode: ViewMode, count: int, current: int) -> List[Tuple[int, ...]]:
    if count <= 0:
        return []
    current = max(0, min(current, count - 1))
    if mode == ViewMode.SINGLE:
        return [(current,)]
    if mode == ViewMode.TWO_PAGE:
        return spreads(count)
    return [(i,) for i in range(count)]


def compute_layout(sizes: Sequence[Tuple[float, float]], zoom: float, viewport_w: float,
                   viewport_h: float, mode: ViewMode = ViewMode.CONTINUOUS,
                   current: int = 0, margin: int = MARGIN, gap: int = GAP) -> Layout:
    scale = zoom * PX_PER_PT
    rows = rows_for(mode, len(sizes), current)
    row_dims = []
    content_w = 0.0
    for row in rows:
        ws = [sizes[i][0] * scale for i in row]
        hs = [sizes[i][1] * scale for i in row]
        rw = sum(ws) + SPREAD_GAP * (len(row) - 1)
        row_dims.append((ws, hs, rw, max(hs)))
        content_w = max(content_w, rw)
    if mode == ViewMode.TWO_PAGE and rows:
        # spreads are laid out around a shared spine; reserve both halves
        half = 0.0
        for row, (ws, _hs, _rw, _h) in zip(rows, row_dims):
            half = max(half, *ws)
        content_w = max(content_w, 2 * half + SPREAD_GAP)
    width = max(float(viewport_w), content_w + 2 * margin)
    slots: List[Slot] = []
    y = float(margin)
    total_rows_h = sum(d[3] for d in row_dims) + gap * max(0, len(rows) - 1)
    if mode == ViewMode.SINGLE and total_rows_h + 2 * margin < viewport_h:
        y = round((viewport_h - total_rows_h) / 2)
    center = width / 2
    for row, (ws, hs, rw, rh) in zip(rows, row_dims):
        if mode == ViewMode.TWO_PAGE:
            if len(row) == 2:
                xs = [center - SPREAD_GAP / 2 - ws[0], center + SPREAD_GAP / 2]
            else:
                xs = [center - ws[0] / 2]
        else:
            xs = [center - rw / 2]
        for i, idx in enumerate(row):
            sy = y + (rh - hs[i]) / 2
            slots.append(Slot(idx, float(round(xs[i])), float(round(sy)), ws[i], hs[i]))
        y += rh + gap
    height = y - gap + margin if rows else float(margin * 2)
    if mode == ViewMode.SINGLE:
        height = max(height, float(viewport_h))
    return Layout(tuple(slots), float(round(width)), float(round(height)), scale, mode,
                  tuple(rows))


# ---------------------------------------------------------------- zoom fits
def fit_width_zoom(sizes: Sequence[Tuple[float, float]], viewport_w: float,
                   mode: ViewMode, current: int, margin: int = MARGIN,
                   scrollbar: int = 0) -> float:
    avail = max(50.0, viewport_w - 2 * margin - scrollbar)
    if not sizes:
        return 1.0
    current = max(0, min(current, len(sizes) - 1))
    if mode == ViewMode.TWO_PAGE:
        row = spreads(len(sizes))[current // 2]
        widest = max(sizes[i][0] for i in row)
        return max(10.0, avail - SPREAD_GAP) / (2 * widest * PX_PER_PT)
    # like pdf.js/Acrobat: fit the current page's width
    return avail / (sizes[current][0] * PX_PER_PT)


def fit_page_zoom(sizes: Sequence[Tuple[float, float]], viewport_w: float,
                  viewport_h: float, mode: ViewMode, current: int,
                  margin: int = MARGIN) -> float:
    if not sizes:
        return 1.0
    current = max(0, min(current, len(sizes) - 1))
    avail_w = max(50.0, viewport_w - 2 * margin)
    avail_h = max(50.0, viewport_h - 2 * margin)
    if mode == ViewMode.TWO_PAGE:
        row = spreads(len(sizes))[current // 2]
        w = sum(sizes[i][0] for i in row)
        h = max(sizes[i][1] for i in row)
        avail_w = max(10.0, avail_w - SPREAD_GAP * (len(row) - 1))
    else:
        w, h = sizes[current]
    return min(avail_w / (w * PX_PER_PT), avail_h / (h * PX_PER_PT))


# ---------------------------------------------------------- navigation
def row_of(layout: Layout, index: int) -> int:
    for r, row in enumerate(layout.rows):
        if index in row:
            return r
    return 0


def next_page_index(mode: ViewMode, count: int, current: int, step: int = 1) -> int:
    """Index of the first page of the next (step=+1) or previous (-1) view unit."""
    if count <= 0:
        return 0
    if mode == ViewMode.TWO_PAGE:
        start = (current // 2) * 2
        target = start + 2 * step
    else:
        target = current + step
    return max(0, min(count - 1, target))


def current_page_for_view(layout: Layout, view_x: float, view_y: float, view_w: float,
                          view_h: float) -> int:
    """The page occupying the largest area of the view (continuous modes)."""
    best, best_area = None, -1.0
    for s in layout.visible_slots(view_x, view_y, view_w, view_h):
        w = min(s.right, view_x + view_w) - max(s.x, view_x)
        h = min(s.bottom, view_y + view_h) - max(s.y, view_y)
        area = max(0.0, w) * max(0.0, h)
        if area > best_area + 1e-6:
            best, best_area = s.index, area
    if best is None:
        s = layout.nearest_slot(view_x + view_w / 2, view_y + view_h / 2)
        return s.index if s else 0
    return best
