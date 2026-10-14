"""Thread-safe render job queue with per-owner replacement (Qt-free).

Owners (a canvas, a thumbnail panel) *replace* their whole pending set on
every submit. Jobs that are no longer wanted, such as tiles for a previous
zoom level or for pages scrolled away, are dropped before they run. A
generation number per owner lets the consumer discard results of jobs that
were already running when the owner moved on.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Set


@dataclass
class RenderRequest:
    owner: Hashable
    key: Hashable
    priority: float
    job: Callable[[], Any] = field(repr=False)
    generation: int = 0


@dataclass(order=True)
class _Entry:
    priority: float
    seq: int
    request: RenderRequest = field(compare=False)


class RenderQueue:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: Dict[Hashable, Dict[Hashable, RenderRequest]] = {}
        self._generation: Dict[Hashable, int] = {}
        self._heap: List[_Entry] = []
        self._seq = itertools.count()
        self._in_flight: Set[Hashable] = set()
        self._closed = False
        self.dropped = 0  # total obsolete jobs discarded before running

    # ----------------------------------------------------------- producers
    def submit(self, owner: Hashable, requests: Iterable[RenderRequest],
               generation: Optional[int] = None) -> int:
        """Replace ``owner``'s pending jobs. Returns how many were dropped."""
        with self._cond:
            old = self._pending.get(owner, {})
            new: Dict[Hashable, RenderRequest] = {}
            for r in requests:
                if r.key in self._in_flight or r.key in new:
                    continue
                new[r.key] = r
            dropped = sum(1 for k in old if k not in new)
            self.dropped += dropped
            self._pending[owner] = new
            if generation is not None:
                self._generation[owner] = generation
            for r in new.values():
                heapq.heappush(self._heap, _Entry(r.priority, next(self._seq), r))
            self._compact()
            if new:
                self._cond.notify()
            return dropped

    def cancel_owner(self, owner: Hashable) -> int:
        with self._cond:
            old = self._pending.pop(owner, {})
            self._generation.pop(owner, None)
            self.dropped += len(old)
            self._compact()
            return len(old)

    def generation(self, owner: Hashable) -> int:
        with self._cond:
            return self._generation.get(owner, 0)

    def is_current(self, request: RenderRequest) -> bool:
        with self._cond:
            return self._generation.get(request.owner, 0) == request.generation

    # ----------------------------------------------------------- consumer
    def take(self, timeout: Optional[float] = None) -> Optional[RenderRequest]:
        """Pop the most urgent live request, blocking up to ``timeout``."""
        with self._cond:
            while True:
                if self._closed:
                    return None
                while self._heap:
                    entry = heapq.heappop(self._heap)
                    r = entry.request
                    pend = self._pending.get(r.owner)
                    if pend is not None and pend.get(r.key) is r:
                        del pend[r.key]
                        self._in_flight.add(r.key)
                        return r
                if not self._cond.wait(timeout):
                    return None

    def done(self, request: RenderRequest) -> None:
        with self._cond:
            self._in_flight.discard(request.key)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._pending.clear()
            self._heap.clear()
            self._cond.notify_all()

    # --------------------------------------------------------------- info
    def pending_count(self, owner: Optional[Hashable] = None) -> int:
        with self._cond:
            if owner is not None:
                return len(self._pending.get(owner, {}))
            return sum(len(p) for p in self._pending.values())

    def pending_keys(self, owner: Hashable) -> Set[Hashable]:
        with self._cond:
            return set(self._pending.get(owner, {}))

    def in_flight(self) -> Set[Hashable]:
        with self._cond:
            return set(self._in_flight)

    @property
    def closed(self) -> bool:
        return self._closed

    def _compact(self) -> None:
        live = sum(len(p) for p in self._pending.values())
        if len(self._heap) > 4 * live + 256:
            self._heap = [e for e in self._heap
                          if self._pending.get(e.request.owner, {}).get(e.request.key) is e.request]
            heapq.heapify(self._heap)
