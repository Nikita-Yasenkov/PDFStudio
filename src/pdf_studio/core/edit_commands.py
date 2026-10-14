"""Undoable content-edit commands: redact, redact-and-replace, image cover
(Qt-free). Each keeps the page-content snapshot taken just before it ran."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..pdf.models import Color, Rect, TextStyle
from .commands import Command
from .session import DocumentSession


class RedactCommand(Command):
    def __init__(self, page_id: int, rects: Sequence[Rect],
                 fill: Optional[Color] = (0.0, 0.0, 0.0), label: str = "Redact"):
        self.page_id = page_id
        self.rects = list(rects)
        self.fill = fill
        self.label = label
        self.snapshot = None

    def execute(self, s: DocumentSession) -> None:
        self.snapshot = s.redact_many(self.page_id, self.rects, self.fill)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)


class ReplaceTextCommand(Command):
    def __init__(self, page_id: int, rect: Rect, text: str, style: TextStyle,
                 fill: Optional[Color] = None, align: int = 0, label: str = "Replace text"):
        self.page_id = page_id
        self.rect = rect
        self.text = text
        self.style = style
        self.fill = fill
        self.align = align
        self.label = label
        self.snapshot = None
        self.font_size_used: Optional[float] = None

    def execute(self, s: DocumentSession) -> None:
        self.font_size_used, self.snapshot = s.replace_text(
            self.page_id, self.rect, self.text, self.style, self.fill, self.align)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)

    @property
    def shrunk(self) -> bool:
        return (self.font_size_used is not None
                and self.font_size_used < self.style.font_size - 0.05)


class PlaceImageCommand(Command):
    def __init__(self, page_id: int, rect: Rect, image: bytes, keep_aspect: bool = True,
                 cover: bool = True, fill: Optional[Color] = (1.0, 1.0, 1.0),
                 label: str = "Replace image"):
        self.page_id = page_id
        self.rect = rect
        self.image = image
        self.keep_aspect = keep_aspect
        self.cover = cover
        self.fill = fill
        self.label = label
        self.snapshot = None

    def execute(self, s: DocumentSession) -> None:
        self.snapshot = s.place_image(self.page_id, self.rect, self.image, self.keep_aspect,
                                      self.cover, self.fill)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)
