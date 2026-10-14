"""Document-level page tools: extract, split, merge (Qt-free)."""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence, Tuple, Union

import pymupdf as fitz

from .engine import ENGINE_LOCK, PdfDocument
from .errors import PageOperationError, PdfSaveError

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name: str, fallback: str = "document") -> str:
    name = _UNSAFE.sub("_", name).strip(" .")
    return name or fallback


def _write_atomic(path: str, data: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(directory):
        raise PdfSaveError(f"Folder does not exist: {directory}")
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise PdfSaveError(f"Could not write {os.path.basename(path)}: "
                           f"{exc.strerror or exc}") from exc


def extract_pages(doc: PdfDocument, indices: Sequence[int], out_path: str) -> str:
    """Write the given pages (in order) to a new PDF. The source is untouched."""
    if not indices:
        raise PageOperationError("No pages selected")
    _write_atomic(out_path, doc.export_pages(list(indices)))
    return os.path.abspath(out_path)


def split_document(doc: PdfDocument, groups: Sequence[Sequence[int]], out_dir: str,
                   base_name: str, pattern: str = "{name}-part{n}") -> List[str]:
    """Write each group of pages to its own file; returns the paths."""
    groups = [list(g) for g in groups if g]
    if not groups:
        raise PageOperationError("Nothing to split")
    if not os.path.isdir(out_dir):
        raise PdfSaveError(f"Folder does not exist: {out_dir}")
    width = len(str(len(groups)))
    paths = []
    for n, group in enumerate(groups, start=1):
        stem = pattern.format(name=base_name, n=str(n).zfill(width),
                              first=group[0] + 1, last=group[-1] + 1)
        path = os.path.join(out_dir, safe_filename(stem) + ".pdf")
        _write_atomic(path, doc.export_pages(group))
        paths.append(path)
    return paths


def bookmark_groups(doc: PdfDocument) -> List[List[int]]:
    """Page groups starting at each top-level bookmark (for split)."""
    starts = sorted({e.page_index for e in doc.outline() if e.level == 1 and e.page_index >= 0})
    n = doc.page_count
    if not starts:
        return []
    if starts[0] != 0:
        starts = [0] + starts
    bounds = starts + [n]
    return [list(range(bounds[k], bounds[k + 1])) for k in range(len(bounds) - 1)
            if bounds[k] < bounds[k + 1]]


def merge_files(sources: Sequence[Tuple[Union[str, bytes], Optional[str]]],
                titles: Optional[Sequence[str]] = None) -> bytes:
    """Concatenate PDFs into one new PDF.

    ``sources`` are ``(path or PDF bytes, password)`` pairs (bytes let open
    documents contribute their unsaved state). Outlines of the inputs are
    combined under one top-level entry per file.
    """
    if not sources:
        raise PageOperationError("No files to combine")
    with ENGINE_LOCK:
        out = fitz.open()
        toc: list = []
        try:
            for k, (source, password) in enumerate(sources):
                if titles is not None and k < len(titles):
                    title = titles[k]
                elif isinstance(source, str):
                    title = os.path.splitext(os.path.basename(source))[0]
                else:
                    title = f"Document {k + 1}"
                try:
                    src = (fitz.open(stream=source, filetype="pdf") if isinstance(source, bytes)
                           else fitz.open(source))
                except Exception as exc:  # noqa: BLE001
                    raise PageOperationError(f"Could not read {title}: {exc}") from exc
                try:
                    if src.needs_pass and not (password and src.authenticate(password)):
                        raise PageOperationError(f"{title} is password protected")
                    start = out.page_count
                    out.insert_pdf(src, annots=True, links=True)
                    toc.append([1, title, start + 1])
                    for entry in src.get_toc(simple=True):
                        lvl, title, page = entry[:3]
                        toc.append([lvl + 1, title, page + start if page > 0 else -1])
                finally:
                    src.close()
            try:
                out.set_toc(toc)
            except Exception:  # noqa: BLE001 - malformed outlines are not fatal
                pass
            return out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
