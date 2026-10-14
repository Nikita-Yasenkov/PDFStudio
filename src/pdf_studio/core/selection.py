"""Word-based text selection (Qt-free).

Words come from the PDF text layer in displayed page space, sorted in
reading order (block, line, word). A selection is the contiguous run of
words between an anchor and a focus word; it is drawn and used for markup
as one rect per text line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..pdf.models import Point, Rect, Word

#: how far (pt) outside a word a press still counts as "on text"
HIT_SLOP = 3.0


@dataclass(frozen=True)
class TextSelection:
    page_index: int
    words: Tuple[Word, ...]

    @property
    def is_empty(self) -> bool:
        return not self.words

    @property
    def text(self) -> str:
        out: List[str] = []
        prev: Optional[Word] = None
        for w in self.words:
            if prev is not None:
                same_line = (w.block, w.line) == (prev.block, prev.line)
                out.append(" " if same_line else "\n")
            out.append(w.text)
            prev = w
        return "".join(out)

    @property
    def line_rects(self) -> List[Rect]:
        """One rect per text line (merged word boxes), in reading order."""
        rects: List[Rect] = []
        keys: List[Tuple[int, int]] = []
        for w in self.words:
            k = (w.block, w.line)
            if keys and keys[-1] == k:
                rects[-1] = rects[-1].union(w.rect)
            else:
                keys.append(k)
                rects.append(w.rect)
        return rects

    @property
    def bounds(self) -> Optional[Rect]:
        return Rect.union_all(w.rect for w in self.words)


def word_at(words: Sequence[Word], p: Point, slop: float = HIT_SLOP) -> Optional[int]:
    """Index of the word under ``p`` (with a little slop), else None."""
    best, best_d = None, None
    for i, w in enumerate(words):
        r = w.rect.inflated(slop)
        if r.contains(p):
            c = w.rect.center
            d = abs(c.x - p.x) + abs(c.y - p.y)
            if best_d is None or d < best_d:
                best, best_d = i, d
    return best


def nearest_word(words: Sequence[Word], p: Point) -> Optional[int]:
    """Index of the word closest to ``p``; lines are favoured over columns,
    so dragging past the end of a line selects to its last word."""
    best, best_d = None, None
    for i, w in enumerate(words):
        r = w.rect
        dx = max(r.x0 - p.x, 0.0, p.x - r.x1)
        dy = max(r.y0 - p.y, 0.0, p.y - r.y1)
        d = dx + 4.0 * dy
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def is_near_text(words: Sequence[Word], p: Point, slop: float = 6.0) -> bool:
    return word_at(words, p, slop) is not None


def select_between(page_index: int, words: Sequence[Word], anchor: Point,
                   focus: Point) -> TextSelection:
    if not words:
        return TextSelection(page_index, ())
    a = word_at(words, anchor)
    if a is None:
        a = nearest_word(words, anchor)
    b = nearest_word(words, focus)
    if a is None or b is None:
        return TextSelection(page_index, ())
    lo, hi = min(a, b), max(a, b)
    return TextSelection(page_index, tuple(words[lo:hi + 1]))


def select_word(page_index: int, words: Sequence[Word], p: Point) -> TextSelection:
    i = word_at(words, p)
    return TextSelection(page_index, (words[i],) if i is not None else ())


def select_line(page_index: int, words: Sequence[Word], p: Point) -> TextSelection:
    i = word_at(words, p)
    if i is None:
        return TextSelection(page_index, ())
    key = (words[i].block, words[i].line)
    return TextSelection(page_index, tuple(w for w in words if (w.block, w.line) == key))


def select_all(page_index: int, words: Sequence[Word]) -> TextSelection:
    return TextSelection(page_index, tuple(words))


def select_in_rect(page_index: int, words: Sequence[Word], rect: Rect) -> TextSelection:
    """Words whose centre lies inside ``rect`` (area selection)."""
    return TextSelection(page_index, tuple(w for w in words if rect.contains(w.rect.center)))
