"""Undoable content-edit commands: redact, redact-and-replace, image cover,
and moving/deleting the placed text and images (Qt-free). Each keeps the
page-content snapshot taken just before it ran."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..pdf.models import Color, Rect, TextStyle, new_object_id, rects_close
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
        #: the placed text, kept across redo so a selection stays valid
        self.object_id = new_object_id()

    def execute(self, s: DocumentSession) -> None:
        self.font_size_used, self.snapshot = s.replace_text(
            self.page_id, self.rect, self.text, self.style, self.fill, self.align,
            self.object_id)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)

    @property
    def shrunk(self) -> bool:
        return (self.font_size_used is not None
                and self.font_size_used < self.style.font_size - 0.05)


class PlaceImageCommand(Command):
    """Place an image; ``replaces`` is a placed image it takes the place of."""

    def __init__(self, page_id: int, rect: Rect, image: bytes, keep_aspect: bool = True,
                 cover: bool = True, fill: Optional[Color] = (1.0, 1.0, 1.0),
                 label: str = "Replace image", replaces: Optional[str] = None):
        self.page_id = page_id
        self.rect = rect
        self.image = image
        self.keep_aspect = keep_aspect
        self.cover = cover
        self.fill = fill
        self.label = label
        self.replaces = replaces
        self.snapshot = None
        self.object_id = new_object_id()

    def execute(self, s: DocumentSession) -> None:
        self.snapshot = s.place_image(self.page_id, self.rect, self.image, self.keep_aspect,
                                      self.cover, self.fill, self.object_id, self.replaces)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)


class TransformObjectCommand(Command):
    """Move or resize placed text or an image so it fills ``rect``.

    ``merge_key`` collapses repeated nudges into one undo step.
    """

    def __init__(self, page_id: int, object_id: str, rect: Rect, label: str = "Move",
                 merge_key: Optional[str] = None):
        self.page_id = page_id
        self.object_id = object_id
        self.rect = rect
        self.label = label
        self.merge_key = merge_key
        self.snapshot = None
        self._noop = False

    def execute(self, s: DocumentSession) -> None:
        if self.snapshot is None:
            current = s.placed_object(self.page_id, self.object_id)
            if current is not None and rects_close(current.rect.as_tuple(),
                                                   self.rect.as_tuple(), 0.05):
                self._noop = True
                return
        self.snapshot = s.transform_object(self.page_id, self.object_id, self.rect)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)

    @property
    def is_noop(self) -> bool:
        return self._noop

    def merge_with(self, other: Command) -> bool:
        if (isinstance(other, TransformObjectCommand) and self.merge_key is not None
                and other.merge_key == self.merge_key and other.page_id == self.page_id
                and other.object_id == self.object_id):
            self.rect = other.rect
            return True
        return False


class DeleteObjectCommand(Command):
    def __init__(self, page_id: int, object_id: str, label: str = "Delete"):
        self.page_id = page_id
        self.object_id = object_id
        self.label = label
        self.snapshot = None

    def execute(self, s: DocumentSession) -> None:
        self.snapshot = s.delete_object(self.page_id, self.object_id)

    def undo(self, s: DocumentSession) -> None:
        s.restore_page_content(self.page_id, self.snapshot)
