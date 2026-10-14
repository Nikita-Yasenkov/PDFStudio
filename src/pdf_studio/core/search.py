"""Document-wide text search runner (Qt-free, cancellable).

Searches page by page; each page search takes the engine lock briefly, so
rendering and UI calls interleave. Results are delivered in batches through
callbacks, which a UI thread wrapper turns into signals.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from ..pdf.models import SearchHit
from .session import DocumentSession

MAX_HITS = 5000


@dataclass(frozen=True)
class SearchOptions:
    query: str
    case_sensitive: bool = False
    whole_words: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.query.strip()


class SearchRun:
    def __init__(self, session: DocumentSession, options: SearchOptions,
                 max_hits: int = MAX_HITS):
        self.session = session
        self.options = options
        self.max_hits = max_hits
        self._cancel = threading.Event()
        self.hit_count = 0
        self.pages_done = 0
        self.truncated = False

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self, on_batch: Callable[[List[SearchHit]], None],
            on_progress: Optional[Callable[[int, int], None]] = None,
            batch_interval: float = 0.08) -> bool:
        """Search all pages. Returns True if completed (not cancelled)."""
        if self.options.is_empty:
            return True
        total = self.session.page_count
        batch: List[SearchHit] = []
        last_flush = time.perf_counter()
        for index in range(total):
            if self._cancel.is_set():
                return False
            try:
                if self.session.doc.closed:
                    return False
                hits = self.session.search_page(index, self.options.query,
                                                self.options.case_sensitive,
                                                self.options.whole_words)
            except Exception:  # noqa: BLE001 - a broken page shouldn't stop search
                hits = []
            room = self.max_hits - self.hit_count
            if len(hits) > room:
                hits = hits[:room]
                self.truncated = True
            batch.extend(hits)
            self.hit_count += len(hits)
            self.pages_done = index + 1
            now = time.perf_counter()
            if batch and (now - last_flush >= batch_interval or index == 0):
                on_batch(batch)
                batch = []
                last_flush = now
            if on_progress is not None and (index % 8 == 0 or index == total - 1):
                on_progress(index + 1, total)
            if self.truncated:
                break
        if batch:
            on_batch(batch)
        if on_progress is not None:
            on_progress(total, total)
        return True


def search_all(session: DocumentSession, options: SearchOptions) -> List[SearchHit]:
    """Synchronous helper (tests, small documents)."""
    out: List[SearchHit] = []
    SearchRun(session, options).run(out.extend)
    return out
