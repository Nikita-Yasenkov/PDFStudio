"""Plain value types returned by the engine. Qt-free and PyMuPDF-free.

All geometry is in *displayed page space*: PDF points (1/72 in), origin at
the top-left of the page as the user sees it (i.e. after the page's
/Rotate is applied), y growing downwards.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence, Tuple

Color = Tuple[float, float, float]


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def translated(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def from_points(a: Point, b: Point) -> "Rect":
        return Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))

    @staticmethod
    def union_all(rects: Iterable["Rect"]) -> Optional["Rect"]:
        out: Optional[Rect] = None
        for r in rects:
            out = r if out is None else out.union(r)
        return out

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> Point:
        return Point((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def normalized(self) -> "Rect":
        return Rect(min(self.x0, self.x1), min(self.y0, self.y1),
                    max(self.x0, self.x1), max(self.y0, self.y1))

    def contains(self, p: Point) -> bool:
        return self.x0 <= p.x <= self.x1 and self.y0 <= p.y <= self.y1

    def intersects(self, other: "Rect") -> bool:
        return not (other.x0 >= self.x1 or other.x1 <= self.x0
                    or other.y0 >= self.y1 or other.y1 <= self.y0)

    def intersection(self, other: "Rect") -> "Rect":
        return Rect(max(self.x0, other.x0), max(self.y0, other.y0),
                    min(self.x1, other.x1), min(self.y1, other.y1))

    def union(self, other: "Rect") -> "Rect":
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0),
                    max(self.x1, other.x1), max(self.y1, other.y1))

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def inflated(self, d: float) -> "Rect":
        return Rect(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class PageInfo:
    index: int
    width: float          # displayed width in points
    height: float         # displayed height in points
    rotation: int         # /Rotate value: 0, 90, 180, 270
    label: str = ""


@dataclass(frozen=True)
class Word:
    rect: Rect
    text: str
    block: int
    line: int
    word: int

    @property
    def order_key(self) -> Tuple[int, int, int]:
        return (self.block, self.line, self.word)


@dataclass(frozen=True)
class OutlineEntry:
    title: str
    level: int                    # 1-based nesting level
    page_index: int               # -1 when the target is unresolved/external
    y: Optional[float] = None     # displayed-space y of the destination, if known
    uri: Optional[str] = None     # external link target, if any


@dataclass(frozen=True)
class SearchHit:
    page_index: int
    rects: Tuple[Rect, ...]       # one rect per line the match spans
    snippet: str                  # surrounding text, whitespace-collapsed
    match_start: int              # match offsets within ``snippet``
    match_end: int

    @property
    def bounds(self) -> Rect:
        r = Rect.union_all(self.rects)
        assert r is not None
        return r


@dataclass(frozen=True)
class RenderedImage:
    """Raw RGB888 pixels for a rendered page or tile."""
    data: bytes
    width: int
    height: int
    stride: int
    x: int = 0            # origin of this image in the page raster (device px)
    y: int = 0


@dataclass(frozen=True)
class TextStyle:
    """Approximate style of text found in a region (for replace-text)."""
    font_size: float = 11.0
    color: Color = (0.0, 0.0, 0.0)
    font_name: str = "helv"       # base-14 alias used for replacement text
    source_font: str = ""         # original font name, informational
    text: str = ""                # text found inside the region
    background: Color = (1.0, 1.0, 1.0)


class AnnotationKind(str, enum.Enum):
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    SQUIGGLY = "squiggly"
    NOTE = "note"
    FREE_TEXT = "free_text"
    INK = "ink"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    STAMP = "stamp"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]

    @property
    def is_text_markup(self) -> bool:
        return self in (AnnotationKind.HIGHLIGHT, AnnotationKind.UNDERLINE,
                        AnnotationKind.STRIKEOUT, AnnotationKind.SQUIGGLY)

    @property
    def is_vertex_based(self) -> bool:
        return self in (AnnotationKind.INK, AnnotationKind.LINE, AnnotationKind.ARROW)


_KIND_LABELS = {
    AnnotationKind.HIGHLIGHT: "Highlight",
    AnnotationKind.UNDERLINE: "Underline",
    AnnotationKind.STRIKEOUT: "Strikethrough",
    AnnotationKind.SQUIGGLY: "Squiggly underline",
    AnnotationKind.NOTE: "Sticky note",
    AnnotationKind.FREE_TEXT: "Text box",
    AnnotationKind.INK: "Freehand drawing",
    AnnotationKind.RECTANGLE: "Rectangle",
    AnnotationKind.ELLIPSE: "Ellipse",
    AnnotationKind.LINE: "Line",
    AnnotationKind.ARROW: "Arrow",
    AnnotationKind.STAMP: "Stamp",
    AnnotationKind.OTHER: "Annotation",
}


Stroke = Tuple[Point, ...]


@dataclass(frozen=True)
class AnnotationModel:
    """A Qt-free description of an annotation.

    Used both as the result of listing annotations and as the *spec* for
    creating/updating one. ``id`` is the annotation's /NM name and is stable
    across undo/redo (the engine re-applies it when recreating).
    """
    kind: AnnotationKind
    rect: Rect
    id: Optional[str] = None
    page_index: int = -1
    quads: Tuple[Rect, ...] = ()             # text markup line boxes
    strokes: Tuple[Stroke, ...] = ()         # ink strokes / line endpoints
    color: Optional[Color] = (1.0, 0.85, 0.0)
    fill: Optional[Color] = None
    opacity: float = 1.0
    width: float = 1.5                       # border/stroke width (pt)
    contents: str = ""
    font_size: float = 12.0                  # free text
    text_color: Color = (0.0, 0.0, 0.0)      # free text
    author: str = ""
    icon: str = "Note"                       # sticky note icon
    stamp_image: Optional[bytes] = field(default=None, repr=False)  # PNG/JPEG
    stamp_name: Optional[str] = None         # named rubber stamp
    subtype: str = ""                        # raw PDF subtype (informational)
    read_only: bool = False                  # foreign types we cannot edit

    def with_changes(self, **changes) -> "AnnotationModel":
        return replace(self, **changes)

    # ---- capability helpers used by tools / inspector -------------------
    @property
    def can_move(self) -> bool:
        return not self.read_only and not self.kind.is_text_markup

    @property
    def can_resize(self) -> bool:
        return not self.read_only and self.kind in (
            AnnotationKind.FREE_TEXT, AnnotationKind.RECTANGLE,
            AnnotationKind.ELLIPSE, AnnotationKind.STAMP, AnnotationKind.INK)

    @property
    def has_fill(self) -> bool:
        return self.kind in (AnnotationKind.RECTANGLE, AnnotationKind.ELLIPSE)

    @property
    def has_width(self) -> bool:
        return self.kind in (AnnotationKind.INK, AnnotationKind.RECTANGLE,
                             AnnotationKind.ELLIPSE, AnnotationKind.LINE,
                             AnnotationKind.ARROW)

    @property
    def has_contents(self) -> bool:
        return self.kind in (AnnotationKind.NOTE, AnnotationKind.FREE_TEXT) or (
            self.kind.is_text_markup)

    @property
    def has_color(self) -> bool:
        return self.kind not in (AnnotationKind.STAMP, AnnotationKind.OTHER)

    def translated(self, dx: float, dy: float) -> "AnnotationModel":
        return replace(
            self,
            rect=self.rect.translated(dx, dy),
            quads=tuple(q.translated(dx, dy) for q in self.quads),
            strokes=tuple(tuple(p.translated(dx, dy) for p in s) for s in self.strokes),
        )

    def resized(self, new_rect: Rect) -> "AnnotationModel":
        """Return a copy fitted into ``new_rect`` (strokes are scaled)."""
        old = self.rect
        sx = new_rect.width / old.width if old.width else 1.0
        sy = new_rect.height / old.height if old.height else 1.0

        def tx(p: Point) -> Point:
            return Point(new_rect.x0 + (p.x - old.x0) * sx,
                         new_rect.y0 + (p.y - old.y0) * sy)

        return replace(
            self,
            rect=new_rect,
            strokes=tuple(tuple(tx(p) for p in s) for s in self.strokes),
        )


@dataclass(frozen=True)
class ImageInfo:
    """An image drawn on a page (for the image-replace tool hit testing)."""
    rect: Rect
    width: int
    height: int


@dataclass(frozen=True)
class SaveResult:
    path: str
    incremental: bool


def rects_close(a: Sequence[float], b: Sequence[float], tol: float = 0.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))
