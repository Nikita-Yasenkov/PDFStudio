"""Qt-free PyMuPDF facade.

:class:`PdfDocument` wraps a ``pymupdf.Document`` and exposes plain models
(see :mod:`pdf_studio.pdf.models`). Nothing outside :mod:`pdf_studio.pdf`
ever sees a PyMuPDF object.

Threading: MuPDF is not safe for concurrent use, and PyMuPDF releases the GIL
while rasterising. Every call into PyMuPDF therefore runs under the global
re-entrant :data:`ENGINE_LOCK`. Background render/search threads take the
lock per tile/page so UI-thread calls interleave with short waits.
"""

from __future__ import annotations

import functools
import os
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf as fitz

from . import annotations as _annots
from . import editing as _edit
from . import page_operations as _pages
from . import text as _text
from .errors import PasswordRequiredError, PdfOpenError, PdfSaveError
from .models import (AnnotationModel, ImageInfo, OutlineEntry, PageInfo, Point, Rect,
                     RenderedImage, SaveResult, SearchHit, TextStyle, Word)

#: Global lock serialising all MuPDF access (all documents, all threads).
ENGINE_LOCK = threading.RLock()

# Keep MuPDF quiet on stderr; problems surface as exceptions instead.
try:  # pragma: no cover - depends on PyMuPDF build
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
except Exception:  # noqa: BLE001
    pass


def locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with ENGINE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def library_versions() -> Dict[str, str]:
    return {"PyMuPDF": fitz.VersionBind, "MuPDF": fitz.VersionFitz}


class PdfDocument:
    """An open PDF. Construct via :meth:`open` or :meth:`from_bytes`."""

    _DISPLAY_LIST_CAPACITY = 8

    def __init__(self, fdoc: "fitz.Document", path: Optional[str],
                 password: Optional[str] = None):
        self._doc = fdoc
        self._path = os.path.abspath(path) if path else None
        self._backing_path = self._path
        self._password = password
        self._encrypted = bool(fdoc.is_encrypted or password)
        self._display_lists: "OrderedDict[int, fitz.DisplayList]" = OrderedDict()
        self._closed = False
        self._display_name = os.path.basename(self._path) if self._path else "Untitled.pdf"
        #: set by destructive content edits; forces a full (garbage-collected)
        #: rewrite so redacted content cannot be recovered from old revisions.
        self.requires_full_save = False

    # ------------------------------------------------------------------ open
    @classmethod
    @locked
    def open(cls, path: str, password: Optional[str] = None) -> "PdfDocument":
        """Open ``path``.

        Raises :class:`PasswordRequiredError` when the file is encrypted and
        ``password`` is missing or wrong, :class:`PdfOpenError` otherwise.
        """
        if not os.path.isfile(path):
            raise PdfOpenError(f"File not found: {path}")
        try:
            fdoc = fitz.open(path)
        except Exception as exc:  # noqa: BLE001 - PyMuPDF raises various types
            raise PdfOpenError(f"Could not open {os.path.basename(path)}: {exc}") from exc
        return cls._finish_open(fdoc, path, password)

    @classmethod
    @locked
    def from_bytes(cls, data: bytes, name: Optional[str] = None,
                   password: Optional[str] = None) -> "PdfDocument":
        try:
            fdoc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise PdfOpenError(f"Could not read PDF data: {exc}") from exc
        doc = cls._finish_open(fdoc, None, password)
        doc._backing_path = None
        doc._display_name = name or "Untitled.pdf"
        return doc

    @classmethod
    def _finish_open(cls, fdoc, path, password) -> "PdfDocument":
        if not fdoc.is_pdf:
            fdoc.close()
            raise PdfOpenError(f"Not a PDF file: {os.path.basename(path or '')}")
        # owner-password-only files open without a password but are still encrypted
        encrypted = bool(fdoc.is_encrypted or fdoc.needs_pass
                         or (fdoc.metadata or {}).get("encryption"))
        needs_pass = bool(fdoc.needs_pass)
        if needs_pass:
            if not password:
                fdoc.close()
                raise PasswordRequiredError(path or "", wrong_password=False)
            if not fdoc.authenticate(password):
                fdoc.close()
                raise PasswordRequiredError(path or "", wrong_password=True)
        if fdoc.page_count == 0:
            fdoc.close()
            raise PdfOpenError(f"The document has no pages: {os.path.basename(path or '')}")
        doc = cls(fdoc, path, password if needs_pass else None)
        doc._encrypted = encrypted
        return doc

    @staticmethod
    @locked
    def needs_password(path: str) -> bool:
        try:
            with fitz.open(path) as d:
                return bool(d.needs_pass)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ properties
    @property
    def path(self) -> Optional[str]:
        return self._path

    @property
    def name(self) -> str:
        return os.path.basename(self._path) if self._path else self._display_name

    @property
    def is_encrypted(self) -> bool:
        return self._encrypted

    @property
    def password(self) -> Optional[str]:
        return self._password

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    @locked
    def page_count(self) -> int:
        return self._doc.page_count

    @locked
    def metadata(self) -> Dict[str, str]:
        meta = dict(self._doc.metadata or {})
        return {k: v for k, v in meta.items() if v}

    @locked
    def set_metadata(self, values: Dict[str, str]) -> Dict[str, str]:
        """Update document info fields; returns the previous values."""
        old = dict(self._doc.metadata or {})
        meta = dict(old)
        meta.update({k: v for k, v in values.items()})
        allowed = {"title", "author", "subject", "keywords", "creator", "producer",
                   "creationDate", "modDate"}
        self._doc.set_metadata({k: (v or "") for k, v in meta.items() if k in allowed})
        return {k: old.get(k, "") or "" for k in values}

    @locked
    def page_info(self, index: int) -> PageInfo:
        page = self._page(index)
        r = page.rect
        label = page.get_label() if self._has_labels() else ""
        return PageInfo(index, r.width, r.height, page.rotation, label)

    @locked
    def page_infos(self) -> List[PageInfo]:
        has_labels = self._has_labels()
        out = []
        for i in range(self._doc.page_count):
            page = self._doc.load_page(i)
            r = page.rect
            out.append(PageInfo(i, r.width, r.height, page.rotation,
                                page.get_label() if has_labels else ""))
        return out

    def page_sizes(self) -> List[Tuple[float, float]]:
        return [(p.width, p.height) for p in self.page_infos()]

    def _has_labels(self) -> bool:
        try:
            return bool(self._doc.get_page_labels())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- rendering
    @locked
    def render(self, index: int, scale: float, clip: Optional[Rect] = None,
               annots: bool = True) -> RenderedImage:
        """Rasterise page ``index`` at ``scale`` device px per point.

        ``clip`` is a displayed-space rect limiting the rendered area (used
        for tiles). The returned image's ``x``/``y`` give its origin in the
        full-page raster so callers can place tiles exactly.
        """
        dl = self._display_list(index) if annots else self._page(index).get_displaylist(annots=False)
        mat = fitz.Matrix(scale, scale)
        kwargs = {"matrix": mat, "alpha": False}
        if clip is not None:
            kwargs["clip"] = fitz.Rect(clip.as_tuple())
        pix = dl.get_pixmap(**kwargs)
        return RenderedImage(pix.samples, pix.width, pix.height, pix.stride, pix.x, pix.y)

    @locked
    def render_fit(self, index: int, max_width: int, max_height: int) -> RenderedImage:
        """Render the whole page scaled to fit a box (thumbnails)."""
        page = self._page(index)
        r = page.rect
        scale = min(max_width / r.width, max_height / r.height)
        return self.render(index, scale)

    def _display_list(self, index: int) -> "fitz.DisplayList":
        dl = self._display_lists.get(index)
        if dl is not None:
            self._display_lists.move_to_end(index)
            return dl
        dl = self._page(index).get_displaylist(annots=True)
        self._display_lists[index] = dl
        while len(self._display_lists) > self._DISPLAY_LIST_CAPACITY:
            self._display_lists.popitem(last=False)
        return dl

    def invalidate_page(self, index: Optional[int] = None) -> None:
        """Drop cached display lists (one page, or all when ``None``)."""
        with ENGINE_LOCK:
            if index is None:
                self._display_lists.clear()
            else:
                self._display_lists.pop(index, None)

    # ------------------------------------------------------------------ text
    @locked
    def words(self, index: int) -> List[Word]:
        return _text.extract_words(self._page(index))

    @locked
    def page_text(self, index: int) -> str:
        return self._page(index).get_text("text")

    @locked
    def text_in_rect(self, index: int, rect: Rect) -> str:
        page = self._page(index)
        return _text.text_in_rect(page, self._to_unrotated(page, rect))

    @locked
    def search_page(self, index: int, query: str, case_sensitive: bool = False,
                    whole_words: bool = False) -> List[SearchHit]:
        pattern = _text.build_pattern(query, case_sensitive, whole_words)
        if pattern is None:
            return []
        return _text.search_page(self._page(index), index, pattern)

    # --------------------------------------------------------------- outline
    @locked
    def outline(self) -> List[OutlineEntry]:
        entries: List[OutlineEntry] = []
        try:
            toc = self._doc.get_toc(simple=False)
        except Exception:  # noqa: BLE001 - damaged outlines shouldn't break open
            return entries
        count = self._doc.page_count
        for item in toc:
            level, title, page_no = item[0], item[1], item[2]
            dest = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
            index = page_no - 1 if 1 <= page_no <= count else -1
            y = None
            to = dest.get("to")
            if index >= 0 and isinstance(to, fitz.Point) and dest.get("kind") == fitz.LINK_GOTO:
                page = self._doc.load_page(index)
                p = fitz.Point(to) * page.rotation_matrix if page.rotation else fitz.Point(to)
                y = max(0.0, float(p.y))
            uri = dest.get("uri") if dest.get("kind") == fitz.LINK_URI else None
            entries.append(OutlineEntry(title.strip() or "(untitled)", level, index, y, uri))
        return entries

    # ----------------------------------------------------------- annotations
    @locked
    def annotations(self, index: int) -> List[AnnotationModel]:
        return _annots.list_annotations(self._page(index), index)

    @locked
    def annotation(self, index: int, annot_id: str, with_image: bool = False) -> AnnotationModel:
        """One annotation; ``with_image`` also recovers image-stamp pixels
        (needed to recreate it on undo)."""
        return _annots.get_annotation(self._page(index), index, annot_id, with_image)

    @locked
    def add_annotation(self, index: int, model: AnnotationModel) -> AnnotationModel:
        page = self._page(index)
        try:
            result = _annots.create_annotation(page, index, model)
        finally:
            self.invalidate_page(index)
        return result

    @locked
    def update_annotation(self, index: int, annot_id: str,
                          model: AnnotationModel) -> AnnotationModel:
        page = self._page(index)
        try:
            result = _annots.update_annotation(page, index, annot_id, model)
        finally:
            self.invalidate_page(index)
        return result

    @locked
    def place_stamp(self, index: int, rect: Rect, image: Optional[bytes] = None,
                    name: Optional[str] = None, author: str = "") -> AnnotationModel:
        """Place a visual signature (image) or a named rubber stamp."""
        from .stamps import signature_model, stamp_model
        if image is not None:
            model = signature_model(rect, image, author)
        else:
            model = stamp_model(rect, name or "Approved", author)
        return self.add_annotation(index, model)

    @locked
    def delete_annotation(self, index: int, annot_id: str) -> None:
        page = self._page(index)
        try:
            _annots.delete_annotation(page, annot_id)
        finally:
            self.invalidate_page(index)

    # ------------------------------------------------------- page operations
    @locked
    def rotate_pages(self, indices: Sequence[int], delta: int) -> Dict[int, int]:
        """Rotate pages (positive = clockwise); returns previous rotations."""
        try:
            return _pages.rotate_pages(self._doc, indices, delta)
        finally:
            self.invalidate_page(None)

    @locked
    def set_rotations(self, rotations: Dict[int, int]) -> None:
        try:
            _pages.set_rotations(self._doc, rotations)
        finally:
            self.invalidate_page(None)

    @locked
    def export_pages(self, indices: Sequence[int]) -> bytes:
        return _pages.export_pages(self._doc, indices)

    @locked
    def delete_pages(self, indices: Sequence[int]) -> int:
        try:
            _pages.delete_pages(self._doc, indices)
        finally:
            self.invalidate_page(None)
        return self._doc.page_count

    @locked
    def insert_pdf(self, data: bytes, at: int, pages: Optional[Sequence[int]] = None,
                   password: Optional[str] = None) -> int:
        try:
            return _pages.insert_pdf(self._doc, data, at, pages, password)
        finally:
            self.invalidate_page(None)

    @locked
    def duplicate_page(self, index: int, to: Optional[int] = None) -> int:
        try:
            return _pages.duplicate_page(self._doc, index, to)
        finally:
            self.invalidate_page(None)

    @locked
    def reorder_pages(self, order: Sequence[int]) -> None:
        try:
            _pages.reorder(self._doc, order)
        finally:
            self.invalidate_page(None)

    @locked
    def move_pages(self, indices: Sequence[int], target: int) -> List[int]:
        """Move pages as a block before original page ``target``; returns the
        applied order (old index per new position)."""
        order = _pages.move_order(self._doc.page_count, indices, target)
        self.reorder_pages(order)
        return order

    @locked
    def insert_blank_page(self, at: int, width: float = 595.0, height: float = 842.0) -> int:
        try:
            return _pages.insert_blank_page(self._doc, at, width, height)
        finally:
            self.invalidate_page(None)

    @locked
    def get_toc(self) -> list:
        return _pages.get_toc(self._doc)

    @locked
    def set_toc(self, toc: list) -> None:
        _pages.set_toc(self._doc, toc)

    @locked
    def page_bytes(self) -> bytes:
        """Serialised copy of the whole document (used by merge)."""
        return self._doc.tobytes(garbage=0)

    # ---------------------------------------------------------- content edits
    @locked
    def snapshot_page_content(self, index: int) -> "_edit.ContentSnapshot":
        return _edit.snapshot_content(self._doc, self._page(index))

    @locked
    def restore_page_content(self, index: int, snapshot: "_edit.ContentSnapshot") -> None:
        try:
            _edit.restore_content(self._doc, self._page(index), snapshot)
        finally:
            self.invalidate_page(index)

    @locked
    def probe_text_style(self, index: int, rect: Rect) -> TextStyle:
        return _edit.probe_text_style(self._page(index), rect)

    @locked
    def redact(self, index: int, rect: Rect, fill: Optional[tuple] = (0.0, 0.0, 0.0)) -> None:
        try:
            _edit.redact(self._page(index), rect, fill)
            self.requires_full_save = True
        finally:
            self.invalidate_page(index)

    @locked
    def replace_text(self, index: int, rect: Rect, text: str, style: TextStyle,
                     fill: Optional[tuple] = None, align: int = 0) -> float:
        try:
            size = _edit.replace_text(self._page(index), rect, text, style, fill, align)
            self.requires_full_save = True
            return size
        finally:
            self.invalidate_page(index)

    @locked
    def place_image(self, index: int, rect: Rect, image: bytes, keep_aspect: bool = True,
                    cover: bool = True, fill: Optional[tuple] = (1.0, 1.0, 1.0)) -> None:
        try:
            _edit.place_image(self._page(index), rect, image, keep_aspect, cover, fill)
            if cover:
                self.requires_full_save = True
        finally:
            self.invalidate_page(index)

    @locked
    def image_rects(self, index: int) -> List[ImageInfo]:
        return [ImageInfo(r, w, h) for r, w, h in _edit.image_rects(self._page(index))]

    # ------------------------------------------------------------------ save
    @locked
    def save(self, path: Optional[str] = None, allow_incremental: bool = True) -> SaveResult:
        """Write the document to ``path`` (default: its current path).

        * Saving back to the file the document was opened from uses an
          incremental update when PyMuPDF allows it and no destructive
          content edit (redaction) happened since the last save.
        * Otherwise the document is serialised *without* garbage collection
          (object numbers preserved, so undo snapshots stay valid), cleaned
          into a temp file with ``garbage=3`` and atomically moved into place.
          If the target is the backing file (locked on Windows), the open
          document first switches to an identical in-memory copy.
        """
        target = os.path.abspath(path or self._path or "")
        if not target or target == os.path.abspath(""):
            raise PdfSaveError("No file name given")
        same_backing = bool(self._backing_path) and (
            os.path.normcase(target) == os.path.normcase(self._backing_path))
        if (same_backing and allow_incremental and not self.requires_full_save
                and self._doc.can_save_incrementally()):
            try:
                self._doc.save(target, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                self._path = target
                return SaveResult(target, True)
            except Exception:  # noqa: BLE001 - fall back to a full rewrite
                pass
        self._full_save(target, same_backing)
        self._path = target
        self.requires_full_save = False
        return SaveResult(target, False)

    def _full_save(self, target: str, same_backing: bool) -> None:
        directory = os.path.dirname(target) or "."
        if not os.path.isdir(directory):
            raise PdfSaveError(f"Folder does not exist: {directory}")
        try:
            data = self._doc.tobytes(garbage=0, encryption=fitz.PDF_ENCRYPT_KEEP)
        except Exception as exc:  # noqa: BLE001
            raise PdfSaveError(f"Could not serialise the document: {exc}") from exc
        tmp = os.path.join(directory, f".{os.path.basename(target)}.{os.getpid()}.tmp")
        try:
            clean = fitz.open(stream=data, filetype="pdf")
            if clean.needs_pass and self._password:
                clean.authenticate(self._password)
            clean.save(tmp, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            clean.close()
        except Exception as exc:  # noqa: BLE001
            _silent_remove(tmp)
            raise PdfSaveError(f"Could not write {os.path.basename(target)}: {exc}") from exc
        if same_backing:
            # Release the lock on the backing file without disturbing object
            # numbers: switch to an in-memory copy of the same bytes.
            mem = fitz.open(stream=data, filetype="pdf")
            if mem.needs_pass and self._password:
                mem.authenticate(self._password)
            old = self._doc
            self._doc = mem
            self._display_lists.clear()
            old.close()
            self._backing_path = None
        try:
            os.replace(tmp, target)
        except OSError as exc:
            _silent_remove(tmp)
            raise PdfSaveError(
                f"Could not replace {os.path.basename(target)} — it may be open in another "
                f"program or read-only. ({exc.strerror or exc})") from exc

    # ------------------------------------------------------------- lifecycle
    @locked
    def close(self) -> None:
        if not self._closed:
            self._display_lists.clear()
            self._doc.close()
            self._closed = True

    # --------------------------------------------------------------- helpers
    def _page(self, index: int) -> "fitz.Page":
        if not 0 <= index < self._doc.page_count:
            raise IndexError(f"Page index {index} out of range")
        return self._doc.load_page(index)

    @staticmethod
    def _to_unrotated(page: "fitz.Page", rect: Rect) -> "fitz.Rect":
        r = fitz.Rect(rect.as_tuple())
        if page.rotation:
            r = r * page.derotation_matrix
        return r.normalize()

    @staticmethod
    def _point_to_unrotated(page: "fitz.Page", p: Point) -> "fitz.Point":
        pt = fitz.Point(p.x, p.y)
        return pt * page.derotation_matrix if page.rotation else pt

    @staticmethod
    def _point_to_display(page: "fitz.Page", p: "fitz.Point") -> Point:
        pt = fitz.Point(p) * page.rotation_matrix if page.rotation else fitz.Point(p)
        return Point(float(pt.x), float(pt.y))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PdfDocument {self.name!r}>"


def open_document(path: str, password: Optional[str] = None) -> PdfDocument:
    return PdfDocument.open(path, password)


def _silent_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def page_index_list(indices: Sequence[int]) -> List[int]:
    return sorted(set(int(i) for i in indices))
