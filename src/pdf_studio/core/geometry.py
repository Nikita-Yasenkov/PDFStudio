"""Coordinate transforms between PDF page space and the screen (Qt-free).

Spaces
------
* **pdf**      displayed page space, points, origin top-left of the page.
* **canvas**   logical pixels in the scrollable content area; each page has a
               *slot* (its rect in canvas space) computed by ``core.layout``.
* **widget**   logical pixels in the viewport widget: ``canvas - scroll``.
* **device**   physical pixels: ``logical × devicePixelRatio`` (rendering only).

``scale`` is canvas px per PDF point: ``zoom × PX_PER_PT``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..pdf.models import Point, Rect

#: 100 % zoom shows physical size on a 96-dpi display (1 pt = 1/72 in).
PX_PER_PT = 96.0 / 72.0


@dataclass(frozen=True)
class PageTransform:
    """Maps one page's PDF space to canvas space."""
    x: float        # slot origin in canvas px
    y: float
    scale: float    # canvas px per point

    def to_canvas(self, p: Point) -> Tuple[float, float]:
        return (self.x + p.x * self.scale, self.y + p.y * self.scale)

    def to_pdf(self, cx: float, cy: float) -> Point:
        return Point((cx - self.x) / self.scale, (cy - self.y) / self.scale)

    def rect_to_canvas(self, r: Rect) -> Tuple[float, float, float, float]:
        """Returns (x, y, w, h) in canvas px."""
        return (self.x + r.x0 * self.scale, self.y + r.y0 * self.scale,
                r.width * self.scale, r.height * self.scale)

    def rect_to_pdf(self, x: float, y: float, w: float, h: float) -> Rect:
        a = self.to_pdf(x, y)
        b = self.to_pdf(x + w, y + h)
        return Rect.from_points(a, b)


@dataclass(frozen=True)
class Scroll:
    x: float = 0.0
    y: float = 0.0


def widget_to_canvas(wx: float, wy: float, scroll: Scroll) -> Tuple[float, float]:
    return (wx + scroll.x, wy + scroll.y)


def canvas_to_widget(cx: float, cy: float, scroll: Scroll) -> Tuple[float, float]:
    return (cx - scroll.x, cy - scroll.y)


def widget_to_pdf(wx: float, wy: float, scroll: Scroll, transform: PageTransform) -> Point:
    cx, cy = widget_to_canvas(wx, wy, scroll)
    return transform.to_pdf(cx, cy)


def pdf_to_widget(p: Point, scroll: Scroll, transform: PageTransform) -> Tuple[float, float]:
    cx, cy = transform.to_canvas(p)
    return canvas_to_widget(cx, cy, scroll)


def pdf_rect_to_widget(r: Rect, scroll: Scroll,
                       transform: PageTransform) -> Tuple[float, float, float, float]:
    x, y, w, h = transform.rect_to_canvas(r)
    return (x - scroll.x, y - scroll.y, w, h)


def widget_rect_to_pdf(x: float, y: float, w: float, h: float, scroll: Scroll,
                       transform: PageTransform) -> Rect:
    return transform.rect_to_pdf(x + scroll.x, y + scroll.y, w, h)


def device_scale(zoom: float, dpr: float) -> float:
    """Device pixels per PDF point for rendering."""
    return zoom * PX_PER_PT * dpr


def scale_key(scale: float) -> int:
    """Quantised render scale used in cache keys."""
    return int(round(scale * 1000))


def tile_grid(page_w_pt: float, page_h_pt: float, dev_scale: float,
              tile: int) -> Tuple[int, int, int, int]:
    """Returns (raster_w, raster_h, cols, rows) for a page at ``dev_scale``."""
    w = max(1, int(round(page_w_pt * dev_scale)))
    h = max(1, int(round(page_h_pt * dev_scale)))
    return w, h, (w + tile - 1) // tile, (h + tile - 1) // tile


def tile_clip(tx: int, ty: int, raster_w: int, raster_h: int, dev_scale: float,
              tile: int) -> Rect:
    """PDF-space clip rect for tile (tx, ty)."""
    x0, y0 = tx * tile, ty * tile
    x1, y1 = min(raster_w, x0 + tile), min(raster_h, y0 + tile)
    return Rect(x0 / dev_scale, y0 / dev_scale, x1 / dev_scale, y1 / dev_scale)


def simplify_polyline(points, epsilon: float = 0.35):
    """Ramer–Douglas–Peucker simplification (keeps the stroke's shape)."""
    pts = list(points)
    if len(pts) < 3:
        return pts

    def dist(p, a, b):
        dx, dy = b.x - a.x, b.y - a.y
        if dx == 0 and dy == 0:
            return ((p.x - a.x) ** 2 + (p.y - a.y) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)))
        px, py = a.x + t * dx, a.y + t * dy
        return ((p.x - px) ** 2 + (p.y - py) ** 2) ** 0.5

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, -1
        for k in range(i + 1, j):
            d = dist(pts[k], pts[i], pts[j])
            if d > best:
                best, idx = d, k
        if best > epsilon and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


def constrain_angle(a: Point, b: Point, step_deg: float = 45.0) -> Point:
    """Snap segment a→b to the nearest multiple of ``step_deg`` (Shift-drag)."""
    import math
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length == 0:
        return b
    ang = math.atan2(dy, dx)
    step = math.radians(step_deg)
    ang = round(ang / step) * step
    return Point(a.x + length * math.cos(ang), a.y + length * math.sin(ang))


def square_from(a: Point, b: Point) -> Point:
    """End point making a→b a square (Shift-drag for rectangles/ellipses)."""
    dx, dy = b.x - a.x, b.y - a.y
    side = max(abs(dx), abs(dy))
    return Point(a.x + (side if dx >= 0 else -side), a.y + (side if dy >= 0 else -side))


def visible_tiles(slot_x: float, slot_y: float, view_x: float, view_y: float,
                  view_w: float, view_h: float, dpr: float, raster_w: int,
                  raster_h: int, tile: int):
    """Tile indices of a page slot intersecting a canvas-space view rect."""
    lx0 = max(0.0, view_x - slot_x) * dpr
    ly0 = max(0.0, view_y - slot_y) * dpr
    lx1 = min(raster_w, (view_x + view_w - slot_x) * dpr)
    ly1 = min(raster_h, (view_y + view_h - slot_y) * dpr)
    if lx1 <= lx0 or ly1 <= ly0:
        return []
    c0, r0 = int(lx0 // tile), int(ly0 // tile)
    c1, r1 = int((lx1 - 1) // tile), int((ly1 - 1) // tile)
    return [(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]
