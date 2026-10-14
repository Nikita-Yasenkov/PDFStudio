"""Page-structure operations on a PyMuPDF document (Qt-free).

Indices are 0-based. Functions here do no locking; :class:`PdfDocument`
wraps them under the engine lock and invalidates its render caches.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf as fitz

from .errors import PageOperationError

TocEntry = list


def _check(doc: fitz.Document, indices: Sequence[int]) -> List[int]:
    n = doc.page_count
    out = sorted(set(int(i) for i in indices))
    if not out:
        raise PageOperationError("No pages given")
    for i in out:
        if not 0 <= i < n:
            raise PageOperationError(f"Page {i + 1} does not exist")
    return out


def rotate_pages(doc: fitz.Document, indices: Sequence[int], delta: int) -> Dict[int, int]:
    """Rotate pages by ``delta`` degrees (multiple of 90; positive = clockwise).

    Returns the previous /Rotate value per index (for undo).
    """
    if delta % 90:
        raise PageOperationError("Rotation must be a multiple of 90°")
    previous: Dict[int, int] = {}
    for i in _check(doc, indices):
        page = doc.load_page(i)
        previous[i] = page.rotation
        page.set_rotation((page.rotation + delta) % 360)
    return previous


def set_rotations(doc: fitz.Document, rotations: Dict[int, int]) -> None:
    for i, rot in rotations.items():
        doc.load_page(int(i)).set_rotation(int(rot) % 360)


def export_pages(doc: fitz.Document, indices: Sequence[int]) -> bytes:
    """A standalone PDF containing the given pages, in the given order.

    Annotations and links are kept; used for extract and as the undo snapshot
    for deletions.
    """
    order = [int(i) for i in indices]
    n = doc.page_count
    for i in order:
        if not 0 <= i < n:
            raise PageOperationError(f"Page {i + 1} does not exist")
    out = fitz.open()
    try:
        for i in order:
            out.insert_pdf(doc, from_page=i, to_page=i, annots=True, links=True)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def delete_pages(doc: fitz.Document, indices: Sequence[int]) -> None:
    idx = _check(doc, indices)
    if len(idx) >= doc.page_count:
        raise PageOperationError("A document must keep at least one page")
    doc.delete_pages(idx)


def insert_pdf(doc: fitz.Document, data: bytes, at: int,
               pages: Optional[Sequence[int]] = None, password: Optional[str] = None) -> int:
    """Insert pages from another PDF (given as bytes) before index ``at``.

    ``pages`` selects/orders source pages (default: all). Returns the number
    of pages inserted.
    """
    at = max(0, min(int(at), doc.page_count))
    try:
        src = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PageOperationError(f"Could not read the source PDF: {exc}") from exc
    try:
        if src.needs_pass and not (password and src.authenticate(password)):
            raise PageOperationError("The source PDF is password protected")
        order = list(range(src.page_count)) if pages is None else [int(p) for p in pages]
        for p in order:
            if not 0 <= p < src.page_count:
                raise PageOperationError(f"Source page {p + 1} does not exist")
        if not order:
            raise PageOperationError("No pages selected")
        if order != list(range(src.page_count)):
            src.select(order)
        count = src.page_count
        doc.insert_pdf(src, start_at=at if at < doc.page_count else -1,
                       annots=True, links=True)
        return count
    finally:
        src.close()


def duplicate_page(doc: fitz.Document, index: int, to: Optional[int] = None) -> int:
    """Full copy of page ``index`` (incl. annotations) inserted before ``to``
    (default: right after the source). Returns the new page's index."""
    _check(doc, [index])
    target = index + 1 if to is None else int(to)
    if target >= doc.page_count:
        doc.fullcopy_page(index, -1)
        return doc.page_count - 1
    doc.fullcopy_page(index, target)
    return target


def reorder(doc: fitz.Document, order: Sequence[int]) -> None:
    """Rearrange pages; ``order`` must be a permutation of all indices."""
    order = [int(i) for i in order]
    if sorted(order) != list(range(doc.page_count)):
        raise PageOperationError("Invalid page order")
    if order != list(range(doc.page_count)):
        doc.select(order)


def move_order(count: int, indices: Sequence[int], target: int) -> List[int]:
    """New page order after moving ``indices`` (as a block, keeping their
    relative order) so they start at slot ``target`` of the *remaining*
    pages. ``target`` counts positions in the original numbering: pages are
    dropped *before* original page ``target`` (``count`` = at the end)."""
    moving = sorted(set(int(i) for i in indices))
    rest = [i for i in range(count) if i not in moving]
    # number of non-moving pages before the drop position
    insert_at = sum(1 for i in rest if i < target)
    return rest[:insert_at] + moving + rest[insert_at:]


def insert_blank_page(doc: fitz.Document, at: int, width: float, height: float) -> int:
    at = max(0, min(int(at), doc.page_count))
    doc.new_page(pno=at if at < doc.page_count else -1, width=width, height=height)
    return at


def get_toc(doc: fitz.Document) -> List[TocEntry]:
    try:
        return doc.get_toc(simple=False)
    except Exception:  # noqa: BLE001
        return []


def set_toc(doc: fitz.Document, toc: List[TocEntry]) -> None:
    clean = []
    for entry in toc:
        e = list(entry)
        if len(e) > 3 and isinstance(e[3], dict):
            d = dict(e[3])
            d.pop("xref", None)  # stale object numbers
            e[3] = d
        clean.append(e)
    try:
        doc.set_toc(clean)
    except Exception:  # noqa: BLE001 - fall back to a simple outline
        doc.set_toc([e[:3] for e in clean])


def page_sizes(doc: fitz.Document) -> List[Tuple[float, float]]:
    return [(p.rect.width, p.rect.height) for p in doc]
