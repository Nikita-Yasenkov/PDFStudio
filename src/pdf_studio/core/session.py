"""DocumentSession — one open document plus its editing state (Qt-free).

The session is the single entry point for reading page data and for
mutating the document (through commands). It owns:

* the engine :class:`~pdf_studio.pdf.PdfDocument`;
* stable **page ids** that survive reorder/insert/delete, so caches keyed by
  page id stay valid when pages move;
* per-page **revisions**, bumped whenever a page's pixels may change;
* small caches (page sizes, words, annotations, outline);
* the undo/redo :class:`~pdf_studio.core.commands.CommandStack` and dirty
  state;
* a tiny observer list publishing :class:`SessionEvent`.
"""

from __future__ import annotations

import enum
import itertools
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..pdf import (ENGINE_LOCK, AnnotationModel, ImageInfo, OutlineEntry, PageInfo,
                   PageOperationError, PdfDocument, Rect, RenderedImage, SaveResult,
                   SearchHit, TextStyle, Word)

_session_ids = itertools.count(1)


class EventKind(enum.Enum):
    STRUCTURE = "structure"      # page count/order/sizes changed
    PAGES = "pages"              # content of specific pages changed
    ANNOTATIONS = "annotations"  # annotations on specific pages changed
    HISTORY = "history"          # undo/redo availability or labels changed
    DIRTY = "dirty"              # dirty flag flipped
    PATH = "path"                # file path / name changed (Save As)
    OUTLINE = "outline"          # outline (bookmarks) changed


@dataclass(frozen=True)
class DeletedPages:
    """Undo snapshot for a page deletion."""
    positions: List[int]   # original indices, ascending
    page_ids: List[int]
    data: bytes            # one-page-per-entry PDF in the same order
    toc: list


@dataclass(frozen=True)
class SessionEvent:
    kind: EventKind
    page_ids: Tuple[int, ...] = ()


Listener = Callable[[SessionEvent], None]


class DocumentSession:
    def __init__(self, document: PdfDocument):
        self.uid = next(_session_ids)
        self.doc = document
        self._path: Optional[str] = document.path
        self._listeners: List[Listener] = []
        self._next_page_id = itertools.count(1)
        self.page_ids: List[int] = [next(self._next_page_id) for _ in range(document.page_count)]
        self._revisions: Dict[int, int] = {pid: 0 for pid in self.page_ids}
        self._infos: Optional[List[PageInfo]] = None
        self._words: Dict[Tuple[int, int], List[Word]] = {}
        self._annots: Dict[Tuple[int, int], List[AnnotationModel]] = {}
        self._images: Dict[Tuple[int, int], List[ImageInfo]] = {}
        self._outline: Optional[List[OutlineEntry]] = None
        from .commands import CommandStack  # local: avoid import cycle
        self.stack = CommandStack(self)
        self._last_dirty = False

    # ----------------------------------------------------------------- open
    @classmethod
    def open(cls, path: str, password: Optional[str] = None) -> "DocumentSession":
        return cls(PdfDocument.open(path, password))

    def close(self) -> None:
        self._listeners.clear()
        self.doc.close()

    # ------------------------------------------------------------ observers
    def subscribe(self, fn: Listener) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def emit(self, kind: EventKind, page_ids: Sequence[int] = ()) -> None:
        event = SessionEvent(kind, tuple(page_ids))
        for fn in list(self._listeners):
            fn(event)

    # ----------------------------------------------------------- properties
    @property
    def path(self) -> Optional[str]:
        return self._path

    @property
    def display_name(self) -> str:
        return os.path.basename(self._path) if self._path else self.doc.name

    @property
    def is_encrypted(self) -> bool:
        return self.doc.is_encrypted

    @property
    def dirty(self) -> bool:
        return not self.stack.is_clean

    # ----------------------------------------------------------- page model
    @property
    def page_count(self) -> int:
        return len(self.page_ids)

    def page_infos(self) -> List[PageInfo]:
        if self._infos is None:
            self._infos = self.doc.page_infos()
        return self._infos

    def page_info(self, index: int) -> PageInfo:
        return self.page_infos()[index]

    def page_size(self, index: int) -> Tuple[float, float]:
        info = self.page_infos()[index]
        return (info.width, info.height)

    def page_sizes(self) -> List[Tuple[float, float]]:
        return [(i.width, i.height) for i in self.page_infos()]

    def page_label(self, index: int) -> str:
        info = self.page_infos()[index]
        return info.label or str(index + 1)

    def page_id(self, index: int) -> int:
        return self.page_ids[index]

    def index_of(self, page_id: int) -> Optional[int]:
        try:
            return self.page_ids.index(page_id)
        except ValueError:
            return None

    def revision(self, page_id: int) -> int:
        return self._revisions.get(page_id, -1)

    def render_key(self, index: int) -> Tuple[int, int]:
        pid = self.page_ids[index]
        return pid, self._revisions[pid]

    def is_current(self, page_id: int, revision: int) -> bool:
        return self._revisions.get(page_id, -1) == revision

    def render_page_id(self, page_id: int, revision: int, scale: float,
                       clip: Optional[Rect] = None) -> Optional[RenderedImage]:
        """Render a page by stable id; ``None`` if it changed or vanished.

        Safe to call from the render thread: page-id resolution and
        rasterisation happen atomically under the engine lock.
        """
        with ENGINE_LOCK:
            if self.doc.closed or not self.is_current(page_id, revision):
                return None
            index = self.index_of(page_id)
            if index is None:
                return None
            return self.doc.render(index, scale, clip)

    # ----------------------------------------------------------------- text
    def words(self, index: int) -> List[Word]:
        key = self.render_key(index)
        cached = self._words.get(key)
        if cached is None:
            cached = self.doc.words(index)
            if len(self._words) > 64:
                self._words.clear()
            self._words[key] = cached
        return cached

    def annotations(self, index: int) -> List[AnnotationModel]:
        """Annotations on a page (cached per page revision)."""
        key = self.render_key(index)
        cached = self._annots.get(key)
        if cached is None:
            cached = self.doc.annotations(index)
            if len(self._annots) > 64:
                self._annots.clear()
            self._annots[key] = cached
        return cached

    def annotation_by_id(self, page_id: int, annot_id: str) -> Optional[AnnotationModel]:
        index = self.index_of(page_id)
        if index is None:
            return None
        for a in self.annotations(index):
            if a.id == annot_id:
                return a
        return None

    def outline(self) -> List[OutlineEntry]:
        if self._outline is None:
            self._outline = self.doc.outline()
        return self._outline

    def search_page(self, index: int, query: str, case_sensitive: bool = False,
                    whole_words: bool = False) -> List[SearchHit]:
        return self.doc.search_page(index, query, case_sensitive, whole_words)

    # ------------------------------------------------- mutation bookkeeping
    # Called by commands (under ENGINE_LOCK) after they changed the engine.
    def touch_pages(self, page_ids: Sequence[int], kind: EventKind = EventKind.PAGES) -> None:
        """Mark pages as changed: bump revisions and notify."""
        with ENGINE_LOCK:
            for pid in page_ids:
                if pid in self._revisions:
                    self._revisions[pid] += 1
                    idx = self.index_of(pid)
                    if idx is not None:
                        self.doc.invalidate_page(idx)
            self._words = {k: v for k, v in self._words.items() if k[0] not in page_ids}
        self.emit(kind, page_ids)

    def structure_changed(self, touched: Sequence[int] = ()) -> None:
        """Page list changed (insert/delete/move/rotate): refresh caches."""
        with ENGINE_LOCK:
            self._infos = None
            self._outline = None
            self.doc.invalidate_page(None)
            for pid in touched:
                if pid in self._revisions:
                    self._revisions[pid] += 1
            self._words = {k: v for k, v in self._words.items() if k[0] in self._revisions}
        self.emit(EventKind.STRUCTURE, touched)
        self.emit(EventKind.OUTLINE)

    def new_page_id(self) -> int:
        pid = next(self._next_page_id)
        self._revisions[pid] = 0
        return pid

    def register_page_id(self, pid: int) -> None:
        """Re-register a page id (redo of an insert reuses its old id)."""
        self._revisions.setdefault(pid, 0)
        self._revisions[pid] += 1

    def history_changed(self) -> None:
        self.emit(EventKind.HISTORY)
        d = self.dirty
        if d != self._last_dirty:
            self._last_dirty = d
            self.emit(EventKind.DIRTY)

    def set_path(self, path: str) -> None:
        self._path = os.path.abspath(path)
        self.emit(EventKind.PATH)

    # ------------------------------------------------------ page operations
    # Each primitive mutates the engine, keeps ``page_ids`` in step and
    # publishes a STRUCTURE event. Commands build undo/redo on top of these.
    def _indices(self, page_ids: Sequence[int]) -> List[int]:
        out = []
        for pid in page_ids:
            idx = self.index_of(pid)
            if idx is None:
                raise PageOperationError("A page no longer exists")
            out.append(idx)
        return out

    def rotate_pages(self, page_ids: Sequence[int], delta: int) -> Dict[int, int]:
        """Rotate; returns previous rotation per page id."""
        with ENGINE_LOCK:
            idx = self._indices(page_ids)
            prev = self.doc.rotate_pages(idx, delta)
            by_id = {self.page_ids[i]: rot for i, rot in prev.items()}
        self.structure_changed(list(page_ids))
        return by_id

    def set_rotations(self, rotations: Dict[int, int]) -> None:
        with ENGINE_LOCK:
            self.doc.set_rotations({self._indices([pid])[0]: rot for pid, rot in rotations.items()})
        self.structure_changed(list(rotations))

    def delete_pages(self, page_ids: Sequence[int]) -> "DeletedPages":
        with ENGINE_LOCK:
            pairs = sorted(zip(self._indices(page_ids), page_ids))
            positions = [i for i, _ in pairs]
            ids = [pid for _, pid in pairs]
            toc = self.doc.get_toc()
            data = self.doc.export_pages(positions)
            self.doc.delete_pages(positions)
            for pid in ids:
                self.page_ids.remove(pid)
        self.structure_changed()
        return DeletedPages(positions, ids, data, toc)

    def restore_pages(self, deleted: "DeletedPages") -> None:
        with ENGINE_LOCK:
            for k, (pos, pid) in enumerate(zip(deleted.positions, deleted.page_ids)):
                self.doc.insert_pdf(deleted.data, pos, pages=[k])
                self.page_ids.insert(pos, pid)
                self.register_page_id(pid)
            if deleted.toc:
                self.doc.set_toc(deleted.toc)
        self.structure_changed(deleted.page_ids)

    def remove_pages(self, page_ids: Sequence[int]) -> None:
        """Delete pages without keeping a snapshot (undo of insert/duplicate)."""
        with ENGINE_LOCK:
            idx = self._indices(page_ids)
            self.doc.delete_pages(idx)
            for pid in page_ids:
                self.page_ids.remove(pid)
        self.structure_changed()

    def insert_pdf(self, data: bytes, at: int, pages: Optional[Sequence[int]] = None,
                   page_ids: Optional[Sequence[int]] = None,
                   password: Optional[str] = None) -> List[int]:
        """Insert pages from PDF bytes before index ``at``; returns new page ids
        (``page_ids`` re-uses ids on redo)."""
        with ENGINE_LOCK:
            at = max(0, min(at, self.page_count))
            n = self.doc.insert_pdf(data, at, pages, password)
            ids = list(page_ids) if page_ids else [self.new_page_id() for _ in range(n)]
            for k, pid in enumerate(ids):
                self.page_ids.insert(at + k, pid)
                self.register_page_id(pid)
        self.structure_changed(ids)
        return ids

    def duplicate_pages(self, page_ids: Sequence[int],
                        new_ids: Optional[Sequence[int]] = None) -> List[int]:
        """Copy each page right after itself; returns the copies' ids in the
        order of ``page_ids``."""
        with ENGINE_LOCK:
            pairs = list(zip(self._indices(page_ids), page_ids))
            ids = list(new_ids) if new_ids else [self.new_page_id() for _ in pairs]
            # work from the back so earlier indices stay valid
            order = sorted(range(len(pairs)), key=lambda k: pairs[k][0], reverse=True)
            for k in order:
                idx, _pid = pairs[k]
                new_index = self.doc.duplicate_page(idx)
                self.page_ids.insert(new_index, ids[k])
                self.register_page_id(ids[k])
        self.structure_changed(ids)
        return ids

    def move_pages(self, page_ids: Sequence[int], target: int) -> List[int]:
        """Move pages (as a block) before original page index ``target``.
        Returns the previous page-id order (for undo)."""
        with ENGINE_LOCK:
            previous = list(self.page_ids)
            order = self.doc.move_pages(self._indices(page_ids), target)
            self.page_ids = [previous[i] for i in order]
        self.structure_changed()
        return previous

    def set_page_order(self, order_ids: Sequence[int]) -> None:
        with ENGINE_LOCK:
            if sorted(order_ids) != sorted(self.page_ids):
                raise PageOperationError("Invalid page order")
            pos = {pid: i for i, pid in enumerate(self.page_ids)}
            self.doc.reorder_pages([pos[pid] for pid in order_ids])
            self.page_ids = list(order_ids)
        self.structure_changed()

    def insert_blank_page(self, at: int, size: Tuple[float, float],
                          page_id: Optional[int] = None) -> int:
        with ENGINE_LOCK:
            at = max(0, min(at, self.page_count))
            self.doc.insert_blank_page(at, size[0], size[1])
            pid = page_id if page_id is not None else self.new_page_id()
            self.page_ids.insert(at, pid)
            self.register_page_id(pid)
        self.structure_changed([pid])
        return pid

    def export_pages(self, indices: Sequence[int]) -> bytes:
        return self.doc.export_pages(indices)

    # -------------------------------------------------------- content edits
    # Each edit returns the page-content snapshot needed to undo it.
    def probe_text_style(self, index: int, rect: Rect) -> TextStyle:
        return self.doc.probe_text_style(index, rect)

    def image_rects(self, index: int) -> List[ImageInfo]:
        """Images drawn on a page (cached per page revision)."""
        key = self.render_key(index)
        cached = self._images.get(key)
        if cached is None:
            cached = self.doc.image_rects(index)
            if len(self._images) > 64:
                self._images.clear()
            self._images[key] = cached
        return cached

    def _edit(self, page_id: int, fn):
        with ENGINE_LOCK:
            index = self._indices([page_id])[0]
            snapshot = self.doc.snapshot_page_content(index)
            try:
                result = fn(index)
            except Exception:
                # leave the page exactly as it was
                self.doc.restore_page_content(index, snapshot)
                raise
        self.touch_pages([page_id], EventKind.PAGES)
        return result, snapshot

    def restore_page_content(self, page_id: int, snapshot) -> None:
        with ENGINE_LOCK:
            self.doc.restore_page_content(self._indices([page_id])[0], snapshot)
        self.touch_pages([page_id], EventKind.PAGES)

    def redact(self, page_id: int, rect: Rect, fill=(0.0, 0.0, 0.0)):
        _r, snap = self._edit(page_id, lambda i: self.doc.redact(i, rect, fill))
        return snap

    def redact_many(self, page_id: int, rects: Sequence[Rect], fill=(0.0, 0.0, 0.0)):
        """Redact several boxes (e.g. one per selected text line) as one edit."""
        def run(i):
            for r in rects:
                self.doc.redact(i, r, fill)
        _r, snap = self._edit(page_id, run)
        return snap

    def replace_text(self, page_id: int, rect: Rect, text: str, style: TextStyle,
                     fill=None, align: int = 0):
        """Returns (font size used, snapshot)."""
        return self._edit(page_id, lambda i: self.doc.replace_text(i, rect, text, style,
                                                                     fill, align))

    def place_image(self, page_id: int, rect: Rect, image: bytes, keep_aspect: bool = True,
                    cover: bool = True, fill=(1.0, 1.0, 1.0)):
        _r, snap = self._edit(page_id, lambda i: self.doc.place_image(i, rect, image,
                                                                       keep_aspect, cover, fill))
        return snap

    # ----------------------------------------------------------------- save
    def save(self, path: Optional[str] = None) -> SaveResult:
        """Save to ``path`` (Save As) or the current path (Save)."""
        old = self._path
        with ENGINE_LOCK:
            result = self.doc.save(path or old)
        new = os.path.abspath(result.path)
        if not old or os.path.normcase(new) != os.path.normcase(old):
            self.set_path(new)
        self.stack.set_clean()
        return result
