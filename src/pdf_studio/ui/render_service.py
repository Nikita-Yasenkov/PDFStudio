"""Background rendering: one worker thread feeding a shared tile cache."""

from __future__ import annotations

import logging
import types
import weakref
from typing import Callable, Dict, Hashable, Iterable, Optional

from PyQt6.QtCore import QObject, QPoint, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from ..core.render_cache import RenderCache, TileKey
from ..core.render_queue import RenderQueue, RenderRequest
from ..core.session import DocumentSession
from ..pdf.models import Rect
from .qtutil import to_qimage

log = logging.getLogger(__name__)


class _Worker(QThread):
    jobDone = pyqtSignal(object, object, int, object)  # owner, key, generation, QImage|None

    def __init__(self, queue: RenderQueue):
        super().__init__()
        self.queue = queue
        self.setObjectName("pdf-render-worker")

    def run(self) -> None:  # pragma: no cover - exercised via integration tests
        while True:
            req = self.queue.take(timeout=0.25)
            if req is None:
                if self.queue.closed:
                    return
                continue
            img = None
            try:
                if self.queue.is_current(req):
                    img = req.job()
            except Exception:  # noqa: BLE001 - a bad page must not kill the worker
                log.exception("render job failed: %s", req.key)
            finally:
                self.queue.done(req)
            self.jobDone.emit(req.owner, req.key, req.generation, img)


def tile_job(session: DocumentSession, page_id: int, revision: int, scale: float,
             clip: Optional[Rect]) -> Callable[[], Optional[QImage]]:
    """A job rendering one tile (or a whole page when ``clip`` is None)."""

    def job() -> Optional[QImage]:
        raw = session.render_page_id(page_id, revision, scale, clip)
        if raw is None:
            return None
        img = to_qimage(raw)
        img.setOffset(QPoint(raw.x, raw.y))
        return img

    return job


class RenderService(QObject):
    """Shared by all tabs. Owners register a callback that fires (on the UI
    thread) when one of their tiles lands in the cache."""

    def __init__(self, cache: Optional[RenderCache] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cache = cache or RenderCache()
        self.queue = RenderQueue()
        self._owners: Dict[Hashable, Callable[[TileKey], None]] = {}
        self._worker = _Worker(self.queue)
        self._worker.jobDone.connect(self._on_done)
        self._worker.start(QThread.Priority.LowPriority)
        self.discarded_results = 0

    def register(self, owner: Hashable, callback: Callable[[TileKey], None]) -> None:
        if isinstance(callback, types.MethodType):
            ref = weakref.WeakMethod(callback)
            self._owners[owner] = lambda key, r=ref: (r() or (lambda _k: None))(key)
        else:
            self._owners[owner] = callback

    def unregister(self, owner: Hashable) -> None:
        self._owners.pop(owner, None)
        self.queue.cancel_owner(owner)

    def submit(self, owner: Hashable, requests: Iterable[RenderRequest], generation: int) -> int:
        return self.queue.submit(owner, requests, generation)

    def _on_done(self, owner, key, generation, img) -> None:
        if img is None or img.isNull():
            return
        if self.queue.generation(owner) != generation:
            self.discarded_results += 1
            return
        self.cache.put(key, img, img.sizeInBytes())
        cb = self._owners.get(owner)
        if cb is not None:
            cb(key)

    def shutdown(self) -> None:
        self.queue.close()
        self._worker.wait(3000)

    @property
    def worker(self) -> QThread:
        return self._worker
