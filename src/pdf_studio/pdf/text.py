"""Text extraction and search helpers (Qt-free).

These operate on PyMuPDF ``Page`` objects and return plain models in
displayed page space. They are called by :class:`PdfDocument` while it holds
the engine lock.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

import pymupdf as fitz

from .models import Rect, SearchHit, Word

_SNIPPET_BEFORE = 36
_SNIPPET_AFTER = 64
_WS_RE = re.compile(r"\s+")

# Expand ligatures (so "fi" matches) but keep whitespace and clip to page.
SEARCH_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP


def to_display(rect: fitz.Rect, page: fitz.Page) -> Rect:
    """Convert an unrotated PyMuPDF rect to displayed page space."""
    if page.rotation:
        rect = rect * page.rotation_matrix
    r = fitz.Rect(rect).normalize()
    return Rect(r.x0, r.y0, r.x1, r.y1)


def extract_words(page: fitz.Page) -> List[Word]:
    words: List[Word] = []
    for x0, y0, x1, y1, text, block, line, wno in page.get_text("words"):
        words.append(Word(to_display(fitz.Rect(x0, y0, x1, y1), page), text,
                          int(block), int(line), int(wno)))
    words.sort(key=lambda w: w.order_key)
    return words


class _PageChars:
    """Page text flattened to a string with a per-character bbox map."""

    __slots__ = ("text", "boxes", "lines")

    def __init__(self, page: fitz.Page):
        chars: List[str] = []
        boxes: List[Optional[Tuple[float, float, float, float]]] = []
        lines: List[int] = []
        line_id = 0
        raw = page.get_text("rawdict", flags=SEARCH_FLAGS)
        for block in raw.get("blocks", ()):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    for ch in span.get("chars", ()):
                        chars.append(ch["c"])
                        boxes.append(tuple(ch["bbox"]))
                        lines.append(line_id)
                # line break acts as a word separator
                chars.append(" ")
                boxes.append(None)
                lines.append(line_id)
                line_id += 1
        self.text = "".join(chars)
        self.boxes = boxes
        self.lines = lines

    def rects_for(self, start: int, end: int, page: fitz.Page) -> Tuple[Rect, ...]:
        per_line: dict = {}
        order: List[int] = []
        for i in range(start, end):
            box = self.boxes[i]
            if box is None or box[2] <= box[0]:
                continue
            lid = self.lines[i]
            r = fitz.Rect(box)
            if lid in per_line:
                per_line[lid] |= r
            else:
                per_line[lid] = r
                order.append(lid)
        return tuple(to_display(per_line[lid], page) for lid in order)


def build_pattern(query: str, case_sensitive: bool = False,
                  whole_words: bool = False) -> Optional[re.Pattern]:
    tokens = [t for t in _WS_RE.split(query.strip()) if t]
    if not tokens:
        return None
    body = r"\s+".join(re.escape(t) for t in tokens)
    if whole_words:
        body = r"(?<!\w)" + body + r"(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(body, flags)


def _collapse(s: str) -> str:
    return _WS_RE.sub(" ", s)


def search_page(page: fitz.Page, page_index: int, pattern: re.Pattern,
                limit: int = 2000) -> List[SearchHit]:
    chars = _PageChars(page)
    hits: List[SearchHit] = []
    text = chars.text
    for m in pattern.finditer(text):
        s, e = m.span()
        rects = chars.rects_for(s, e, page)
        if not rects:
            continue
        a = max(0, s - _SNIPPET_BEFORE)
        b = min(len(text), e + _SNIPPET_AFTER)
        prefix = _collapse(text[a:s]).lstrip()
        match = _collapse(text[s:e])
        suffix = _collapse(text[e:b]).rstrip()
        if a > 0:
            prefix = "…" + prefix
        if b < len(text):
            suffix = suffix + "…"
        snippet = prefix + match + suffix
        hits.append(SearchHit(page_index, rects, snippet,
                              len(prefix), len(prefix) + len(match)))
        if len(hits) >= limit:
            break
    return hits


def text_in_rect(page: fitz.Page, rect_unrotated: fitz.Rect) -> str:
    return page.get_textbox(rect_unrotated).strip()
