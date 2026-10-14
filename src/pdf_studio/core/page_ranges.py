"""Page range strings such as ``"1-3, 5, 8-"`` (Qt-free).

User-facing numbers are 1-based; parsed results are 0-based indices.
"""

from __future__ import annotations

import re
from typing import Iterable, List

_PART = re.compile(r"^\s*(\d*)\s*(?:(-)\s*(\d*))?\s*$")


class PageRangeError(ValueError):
    pass


def parse_ranges(text: str, count: int, allow_duplicates: bool = False) -> List[int]:
    """Parse ``text`` into 0-based page indices for a document of ``count``
    pages. Open ends are allowed (``"8-"``, ``"-3"``); ``"all"`` or an empty
    string selects every page. Order is preserved; duplicates are dropped
    unless ``allow_duplicates``."""
    text = (text or "").strip()
    if not text or text.lower() in ("all", "*"):
        return list(range(count))
    out: List[int] = []
    for raw in re.split(r"[,;]", text):
        if not raw.strip():
            continue
        m = _PART.match(raw)
        if not m:
            raise PageRangeError(f"“{raw.strip()}” is not a page or range")
        a, dash, b = m.groups()
        if not dash:
            if not a:
                raise PageRangeError("Empty page number")
            start = end = int(a)
        else:
            start = int(a) if a else 1
            end = int(b) if b else count
        if start < 1 or end < 1:
            raise PageRangeError("Page numbers start at 1")
        if start > count or end > count:
            raise PageRangeError(f"The document has only {count} page{'s' if count != 1 else ''}")
        step = 1 if end >= start else -1
        for n in range(start, end + step, step):
            out.append(n - 1)
    if not allow_duplicates:
        seen = set()
        out = [i for i in out if not (i in seen or seen.add(i))]
    if not out:
        raise PageRangeError("No pages selected")
    return out


def format_ranges(indices: Iterable[int]) -> str:
    """0-based indices → compact 1-based string (sorted): ``[0,1,2,4]`` → ``"1-3, 5"``."""
    idx = sorted(set(int(i) for i in indices))
    parts: List[str] = []
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        a, b = idx[i] + 1, idx[j] + 1
        parts.append(str(a) if a == b else f"{a}-{b}")
        i = j + 1
    return ", ".join(parts)


def chunk(count: int, size: int) -> List[List[int]]:
    """Split ``range(count)`` into consecutive groups of ``size`` (split by N)."""
    if size < 1:
        raise PageRangeError("Group size must be at least 1")
    return [list(range(i, min(i + size, count))) for i in range(0, count, size)]


def split_before(count: int, starts: Iterable[int]) -> List[List[int]]:
    """Split into parts that begin at each 0-based index in ``starts``."""
    cuts = sorted({s for s in starts if 0 < s < count})
    bounds = [0] + cuts + [count]
    return [list(range(bounds[k], bounds[k + 1])) for k in range(len(bounds) - 1)]
