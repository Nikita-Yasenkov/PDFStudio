"""LRU cache for rendered tiles/thumbnails, bounded by bytes and entries.

Values are opaque to this module (the UI stores ``QImage``); callers pass
the approximate byte size. A secondary index by ``(doc, page_id)`` lets the
canvas find fallback rasters of the same page at another scale or an older
revision while the exact tile is still rendering.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 2000

#: tile coordinate used for whole-page images (thumbnails)
WHOLE_PAGE = -1


class TileKey(NamedTuple):
    doc: int        # session uid
    page_id: int    # stable page id
    revision: int   # page revision (content/annotation changes)
    scale: int      # quantised device scale (see geometry.scale_key)
    tx: int         # tile column, or WHOLE_PAGE
    ty: int         # tile row, or WHOLE_PAGE

    @property
    def page_key(self) -> Tuple[int, int]:
        return (self.doc, self.page_id)


@dataclass
class CacheStats:
    entries: int
    bytes: int
    hits: int
    misses: int
    evictions: int


class RenderCache:
    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_entries: int = DEFAULT_MAX_ENTRIES):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._data: "OrderedDict[TileKey, Tuple[Any, int]]" = OrderedDict()
        self._by_page: Dict[Tuple[int, int], Set[TileKey]] = {}
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._lock = threading.RLock()
        self._pinned: Set[Tuple[int, int]] = set()

    # ---------------------------------------------------------------- basics
    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: TileKey) -> bool:
        return key in self._data

    @property
    def total_bytes(self) -> int:
        return self._bytes

    def stats(self) -> CacheStats:
        return CacheStats(len(self._data), self._bytes, self._hits, self._misses,
                          self._evictions)

    def get(self, key: TileKey) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return item[0]

    def peek(self, key: TileKey) -> Optional[Any]:
        item = self._data.get(key)
        return None if item is None else item[0]

    def put(self, key: TileKey, value: Any, nbytes: int) -> None:
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._data[key] = (value, int(nbytes))
            self._bytes += int(nbytes)
            self._by_page.setdefault(key.page_key, set()).add(key)
            # A newer revision supersedes the same tile of older revisions.
            for k in list(self._by_page.get(key.page_key, ())):
                if (k.revision < key.revision and k.scale == key.scale
                        and k.tx == key.tx and k.ty == key.ty):
                    self._remove(k)
            self._evict()

    def _remove(self, key: TileKey) -> None:
        item = self._data.pop(key, None)
        if item is not None:
            self._bytes -= item[1]
        s = self._by_page.get(key.page_key)
        if s is not None:
            s.discard(key)
            if not s:
                del self._by_page[key.page_key]

    def _evict(self) -> None:
        while self._data and (self._bytes > self.max_bytes or len(self._data) > self.max_entries):
            key = next(iter(self._data))
            self._remove(key)
            self._evictions += 1

    # ------------------------------------------------------------ queries
    def keys_for_page(self, doc: int, page_id: int) -> List[TileKey]:
        return list(self._by_page.get((doc, page_id), ()))

    def fallback(self, doc: int, page_id: int, revision: int, scale: int,
                 exclude_scale: Optional[int] = None) -> List[Tuple[TileKey, Any]]:
        """Best substitute rasters for a page: the scale whose tiles are
        closest to ``scale`` (preferring the current revision, then the
        newest older one). Returns ``[(key, value), ...]`` for that scale."""
        keys = self._by_page.get((doc, page_id))
        if not keys:
            return []
        groups: Dict[Tuple[int, int], List[TileKey]] = {}
        for k in keys:
            if k.revision > revision or (exclude_scale is not None and k.scale == exclude_scale
                                         and k.revision == revision):
                continue
            groups.setdefault((k.revision, k.scale), []).append(k)
        if not groups:
            return []

        def rank(g):
            rev, sc = g
            # current revision first, then closeness of scale (prefer larger)
            return (0 if rev == revision else 1, -rev,
                    abs(sc - scale) if sc >= scale else 2 * abs(sc - scale))

        best = min(groups, key=rank)
        return [(k, self._data[k][0]) for k in groups[best] if k in self._data]

    # ------------------------------------------------------- invalidation
    def invalidate_page(self, doc: int, page_id: int) -> None:
        with self._lock:
            for k in list(self._by_page.get((doc, page_id), ())):
                self._remove(k)

    def invalidate_doc(self, doc: int) -> None:
        with self._lock:
            for pk in [pk for pk in self._by_page if pk[0] == doc]:
                for k in list(self._by_page.get(pk, ())):
                    self._remove(k)

    def drop_scales_except(self, doc: int, keep: Set[int], min_scale_entries: int = 0) -> int:
        """Drop tiles of a document whose scale is not in ``keep`` (used to
        shed stale zoom levels under pressure). Returns removed count."""
        removed = 0
        with self._lock:
            for k in [k for k in self._data if k.doc == doc and k.scale not in keep
                      and k.tx != WHOLE_PAGE]:
                self._remove(k)
                removed += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._by_page.clear()
            self._bytes = 0

    def __iter__(self) -> Iterator[TileKey]:
        return iter(list(self._data))
