"""Hit testing, handles, moving and resizing of annotation models (Qt-free).

All coordinates are displayed page space (points).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from ..pdf.models import AnnotationKind as K
from ..pdf.models import AnnotationModel, Point, Rect

# handle ids: corners/edges of the bounding box, or line endpoints
HANDLES_BOX = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLES_LINE = ("p0", "p1")
MIN_SIZE = 6.0


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    if dx == 0 and dy == 0:
        return math.hypot(p.x - a.x, p.y - a.y)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def _ellipse_border_dist(p: Point, r: Rect) -> float:
    cx, cy = r.center.x, r.center.y
    rx, ry = max(r.width / 2, 1e-6), max(r.height / 2, 1e-6)
    nx, ny = (p.x - cx) / rx, (p.y - cy) / ry
    d = math.hypot(nx, ny)
    return abs(d - 1.0) * min(rx, ry)


def hits(m: AnnotationModel, p: Point, tol: float) -> bool:
    k = m.kind
    if k.is_text_markup:
        return any(q.inflated(tol * 0.5).contains(p) for q in m.quads) if m.quads else \
            m.rect.contains(p)
    if k == K.INK:
        reach = tol + m.width / 2
        for stroke in m.strokes:
            for a, b in zip(stroke, stroke[1:]):
                if _seg_dist(p, a, b) <= reach:
                    return True
        return False
    if k in (K.LINE, K.ARROW):
        if m.strokes and len(m.strokes[0]) >= 2:
            return _seg_dist(p, m.strokes[0][0], m.strokes[0][-1]) <= tol + m.width / 2
        return False
    if k == K.RECTANGLE:
        if not m.rect.inflated(tol).contains(p):
            return False
        if m.fill is not None:
            return True
        inner = m.rect.inflated(-(tol + m.width / 2))
        return inner.is_empty or not inner.contains(p)
    if k == K.ELLIPSE:
        if not m.rect.inflated(tol).contains(p):
            return False
        if m.fill is not None:
            return True
        return _ellipse_border_dist(p, m.rect) <= tol + m.width / 2
    return m.rect.inflated(tol * 0.5).contains(p)


def hit_test(annots: Sequence[AnnotationModel], p: Point, tol: float) -> Optional[AnnotationModel]:
    """Topmost annotation under ``p`` (later annotations are drawn on top)."""
    for m in reversed(list(annots)):
        if m.kind == K.OTHER:
            continue
        if hits(m, p, tol):
            return m
    return None


def handles(m: AnnotationModel) -> List[Tuple[str, Point]]:
    if m.read_only:
        return []
    if m.kind in (K.LINE, K.ARROW) and m.strokes and len(m.strokes[0]) >= 2:
        return [("p0", m.strokes[0][0]), ("p1", m.strokes[0][-1])]
    if not m.can_resize:
        return []
    r = m.rect
    cx, cy = r.center.x, r.center.y
    pts = {"nw": Point(r.x0, r.y0), "n": Point(cx, r.y0), "ne": Point(r.x1, r.y0),
           "e": Point(r.x1, cy), "se": Point(r.x1, r.y1), "s": Point(cx, r.y1),
           "sw": Point(r.x0, r.y1), "w": Point(r.x0, cy)}
    names = HANDLES_BOX
    if m.kind == K.STAMP:
        names = ("nw", "ne", "se", "sw")  # keep aspect ratio: corners only
    return [(n, pts[n]) for n in names]


def handle_at(m: AnnotationModel, p: Point, tol: float) -> Optional[str]:
    for name, hp in handles(m):
        if abs(hp.x - p.x) <= tol and abs(hp.y - p.y) <= tol:
            return name
    return None


def clamp_delta(rect: Rect, dx: float, dy: float, page_w: float, page_h: float) -> Tuple[float, float]:
    """Limit a move so the rect stays on the page."""
    dx = max(-rect.x0, min(dx, page_w - rect.x1)) if rect.width <= page_w else dx
    dy = max(-rect.y0, min(dy, page_h - rect.y1)) if rect.height <= page_h else dy
    return dx, dy


def resize_rect(r: Rect, handle: str, p: Point, keep_aspect: bool = False,
                min_size: float = MIN_SIZE) -> Rect:
    x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
    if "w" in handle:
        x0 = min(p.x, x1 - min_size)
    if "e" in handle:
        x1 = max(p.x, x0 + min_size)
    if "n" in handle:
        y0 = min(p.y, y1 - min_size)
    if "s" in handle:
        y1 = max(p.y, y0 + min_size)
    if keep_aspect and r.width > 0 and r.height > 0 and len(handle) == 2:
        ratio = r.width / r.height
        w, h = x1 - x0, y1 - y0
        if w / h > ratio:
            w = h * ratio
        else:
            h = w / ratio
        if "w" in handle:
            x0 = x1 - w
        else:
            x1 = x0 + w
        if "n" in handle:
            y0 = y1 - h
        else:
            y1 = y0 + h
    return Rect(x0, y0, x1, y1)


def apply_handle(m: AnnotationModel, handle: str, p: Point, keep_aspect: bool = False) -> AnnotationModel:
    """New model after dragging ``handle`` to ``p``."""
    if handle in HANDLES_LINE and m.strokes:
        a, b = m.strokes[0][0], m.strokes[0][-1]
        if handle == "p0":
            a = p
        else:
            b = p
        return m.with_changes(strokes=((a, b),), rect=Rect.from_points(a, b))
    keep = keep_aspect or m.kind == K.STAMP
    new_rect = resize_rect(m.rect, handle, p, keep_aspect=keep)
    return m.resized(new_rect)


def cursor_for_handle(handle: str) -> str:
    return {"nw": "fdiag", "se": "fdiag", "ne": "bdiag", "sw": "bdiag",
            "n": "ver", "s": "ver", "e": "hor", "w": "hor",
            "p0": "cross", "p1": "cross"}.get(handle, "arrow")
