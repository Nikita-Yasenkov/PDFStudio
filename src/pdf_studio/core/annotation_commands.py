"""Undoable annotation commands (Qt-free).

Commands address pages by *stable page id* and annotations by their /NM id,
so they stay valid when pages are reordered or annotations recreated.
"""

from __future__ import annotations

from typing import Optional

from ..pdf.errors import AnnotationError
from ..pdf.models import AnnotationKind, AnnotationModel
from .commands import Command
from .session import DocumentSession, EventKind


def _index(s: DocumentSession, page_id: int) -> int:
    idx = s.index_of(page_id)
    if idx is None:
        raise AnnotationError("The page no longer exists")
    return idx


class AddAnnotationCommand(Command):
    def __init__(self, page_id: int, model: AnnotationModel, label: Optional[str] = None):
        self.page_id = page_id
        self.model = model
        self.label = label or f"Add {model.kind.label.lower()}"
        self.created: Optional[AnnotationModel] = None

    @property
    def annot_id(self) -> Optional[str]:
        return self.created.id if self.created else None

    def execute(self, s: DocumentSession) -> None:
        idx = _index(s, self.page_id)
        spec = self.model if self.created is None else self.model.with_changes(id=self.created.id)
        self.created = s.doc.add_annotation(idx, spec)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)

    def undo(self, s: DocumentSession) -> None:
        s.doc.delete_annotation(_index(s, self.page_id), self.created.id)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)


class DeleteAnnotationCommand(Command):
    def __init__(self, page_id: int, annot_id: str, label: Optional[str] = None):
        self.page_id = page_id
        self.annot_id = annot_id
        self.snapshot: Optional[AnnotationModel] = None
        self.label = label or "Delete annotation"

    def execute(self, s: DocumentSession) -> None:
        idx = _index(s, self.page_id)
        self.snapshot = s.doc.annotation(idx, self.annot_id, with_image=True)
        if self.snapshot.read_only:
            raise AnnotationError("This annotation type cannot be deleted here")
        s.doc.delete_annotation(idx, self.annot_id)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)

    def undo(self, s: DocumentSession) -> None:
        s.doc.add_annotation(_index(s, self.page_id), self.snapshot)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)


class UpdateAnnotationCommand(Command):
    """Replace an annotation's properties/geometry with ``new``.

    ``merge_key`` lets rapid edits of the same property (slider drags,
    typing) collapse into one undo step.
    """

    def __init__(self, page_id: int, annot_id: str, new: AnnotationModel,
                 label: str = "Edit annotation", merge_key: Optional[str] = None):
        self.page_id = page_id
        self.annot_id = annot_id
        self.new = new
        self.old: Optional[AnnotationModel] = None
        self.label = label
        self.merge_key = merge_key
        self._noop = False

    def execute(self, s: DocumentSession) -> None:
        idx = _index(s, self.page_id)
        if self.old is None:
            self.old = s.doc.annotation(idx, self.annot_id,
                                        with_image=self.new.kind == AnnotationKind.STAMP)
        if _same(self.old, self.new):
            self._noop = True
            return
        s.doc.update_annotation(idx, self.annot_id, self.new)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)

    def undo(self, s: DocumentSession) -> None:
        s.doc.update_annotation(_index(s, self.page_id), self.annot_id, self.old)
        s.touch_pages([self.page_id], EventKind.ANNOTATIONS)

    @property
    def is_noop(self) -> bool:
        return self._noop

    def merge_with(self, other: Command) -> bool:
        if (isinstance(other, UpdateAnnotationCommand) and self.merge_key is not None
                and other.merge_key == self.merge_key and other.annot_id == self.annot_id
                and other.page_id == self.page_id):
            self.new = other.new
            return True
        return False


def _same(a: AnnotationModel, b: AnnotationModel) -> bool:
    fields = ("kind", "rect", "quads", "strokes", "color", "fill", "opacity", "width",
              "contents", "font_size", "text_color", "icon")
    for f in fields:
        va, vb = getattr(a, f), getattr(b, f)
        if f in ("color", "fill", "text_color") and va is not None and vb is not None:
            if any(abs(x - y) > 1e-3 for x, y in zip(va, vb)):
                return False
            continue
        if f == "opacity" or f == "width" or f == "font_size":
            if abs(float(va) - float(vb)) > 1e-3:
                return False
            continue
        if f in ("rect",):
            if any(abs(x - y) > 0.05 for x, y in zip(va.as_tuple(), vb.as_tuple())):
                return False
            continue
        if va != vb:
            return False
    return True
