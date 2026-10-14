"""Undoable page-structure commands (Qt-free).

Pages are referenced by stable page id; each command keeps exactly the data
needed to reverse itself (previous rotations, deleted page bytes + outline,
the ids it created, the previous order).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .commands import Command
from .session import DeletedPages, DocumentSession


def _plural(n: int, word: str = "page") -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


class RotatePagesCommand(Command):
    def __init__(self, page_ids: Sequence[int], delta: int):
        self.page_ids = list(page_ids)
        self.delta = delta
        self.previous: Dict[int, int] = {}
        direction = "right" if delta > 0 else "left"
        self.label = f"Rotate {_plural(len(self.page_ids))} {direction}"

    def execute(self, s: DocumentSession) -> None:
        self.previous = s.rotate_pages(self.page_ids, self.delta)

    def undo(self, s: DocumentSession) -> None:
        s.set_rotations(self.previous)


class DeletePagesCommand(Command):
    def __init__(self, page_ids: Sequence[int]):
        self.page_ids = list(page_ids)
        self.deleted: Optional[DeletedPages] = None
        self.label = f"Delete {_plural(len(self.page_ids))}"

    def execute(self, s: DocumentSession) -> None:
        self.deleted = s.delete_pages(self.page_ids)

    def undo(self, s: DocumentSession) -> None:
        s.restore_pages(self.deleted)


class DuplicatePagesCommand(Command):
    def __init__(self, page_ids: Sequence[int]):
        self.page_ids = list(page_ids)
        self.new_ids: List[int] = []
        self.label = f"Duplicate {_plural(len(self.page_ids))}"

    def execute(self, s: DocumentSession) -> None:
        self.new_ids = s.duplicate_pages(self.page_ids, self.new_ids or None)

    def undo(self, s: DocumentSession) -> None:
        s.remove_pages(self.new_ids)


class MovePagesCommand(Command):
    """Move pages as a block before original page index ``target``."""

    def __init__(self, page_ids: Sequence[int], target: int):
        self.page_ids = list(page_ids)
        self.target = target
        self.previous: List[int] = []
        self._noop = False
        self.label = f"Move {_plural(len(self.page_ids))}"

    def execute(self, s: DocumentSession) -> None:
        before = list(s.page_ids)
        self.previous = s.move_pages(self.page_ids, self.target)
        self._noop = s.page_ids == before

    def undo(self, s: DocumentSession) -> None:
        s.set_page_order(self.previous)

    @property
    def is_noop(self) -> bool:
        return self._noop


class SetPageOrderCommand(Command):
    """Apply an explicit page order (e.g. a drag of a non-contiguous selection)."""

    def __init__(self, order: Sequence[int], label: str = "Reorder pages"):
        self.order = list(order)
        self.previous: List[int] = []
        self.label = label
        self._noop = False

    def execute(self, s: DocumentSession) -> None:
        self.previous = list(s.page_ids)
        self._noop = self.previous == self.order
        if not self._noop:
            s.set_page_order(self.order)

    def undo(self, s: DocumentSession) -> None:
        s.set_page_order(self.previous)

    @property
    def is_noop(self) -> bool:
        return self._noop


class InsertBlankPageCommand(Command):
    def __init__(self, at: int, size: Tuple[float, float]):
        self.at = at
        self.size = size
        self.page_id: Optional[int] = None
        self.label = "Insert blank page"

    def execute(self, s: DocumentSession) -> None:
        self.page_id = s.insert_blank_page(self.at, self.size, self.page_id)

    def undo(self, s: DocumentSession) -> None:
        s.remove_pages([self.page_id])


class InsertPdfCommand(Command):
    """Insert pages from another PDF (kept as bytes, so redo never re-reads
    the source file)."""

    def __init__(self, data: bytes, at: int, pages: Optional[Sequence[int]] = None,
                 password: Optional[str] = None, label: Optional[str] = None):
        self.data = data
        self.at = at
        self.pages = list(pages) if pages is not None else None
        self.password = password
        self.new_ids: List[int] = []
        self.label = label or "Insert pages"

    def execute(self, s: DocumentSession) -> None:
        self.new_ids = s.insert_pdf(self.data, self.at, self.pages, self.new_ids or None,
                                    self.password)

    def undo(self, s: DocumentSession) -> None:
        s.remove_pages(self.new_ids)
