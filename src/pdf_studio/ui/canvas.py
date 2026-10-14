"""Virtualized page canvas.

Only page slots intersecting the viewport are painted. Each page is split
into device-pixel tiles rendered on the worker thread at
``zoom × 96/72 × devicePixelRatio``. Missing tiles are covered by the best
cached raster of the same page (another zoom level, an older revision, or
the thumbnail), so zooming and edits never flash blank pages.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Protocol, Tuple

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QAbstractScrollArea, QWidget

from ..core import geometry as geo
from ..core.layout import Layout, Slot, ViewMode, compute_layout, current_page_for_view
from ..core.render_cache import WHOLE_PAGE, TileKey
from ..core.render_queue import RenderRequest
from ..core.session import DocumentSession, EventKind, SessionEvent
from ..pdf.models import Point, Rect
from . import theme
from .render_service import RenderService, tile_job

TILE = 512
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0


class Overlay(Protocol):
    def paint(self, painter: QPainter, canvas: "PageCanvas", slots: List[Slot]) -> None: ...


class PageCanvas(QAbstractScrollArea):
    currentPageChanged = pyqtSignal(int)
    zoomChanged = pyqtSignal(float)
    layoutChanged = pyqtSignal()
    viewChanged = pyqtSignal()

    def __init__(self, session: DocumentSession, service: RenderService,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.service = service
        self._zoom = 1.0
        self._mode = ViewMode.CONTINUOUS
        self._current = 0
        self._layout: Optional[Layout] = None
        self._generation = 0
        self._dpr = 1.0
        self._scale_key: Optional[int] = None
        self._flip_accum = 0
        self._owner = ("canvas", id(self))
        self._suppress_scroll = False
        self.overlays: List[Overlay] = []
        self.mouse_handler = None  # set by ToolController
        self.wheel_zoom: Optional[Callable[[float, QPointF], None]] = None
        self.requests_submitted = 0

        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(40)

        service.register(self._owner, self._on_tile_ready)
        session.subscribe(self._on_session_event)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(lambda: self.relayout())

    # ------------------------------------------------------------ lifecycle
    def dispose(self) -> None:
        self.service.unregister(self._owner)
        self.session.unsubscribe(self._on_session_event)

    # --------------------------------------------------------------- layout
    @property
    def layout_(self) -> Layout:
        if self._layout is None:
            self.relayout()
        assert self._layout is not None
        return self._layout

    @property
    def view_mode(self) -> ViewMode:
        return self._mode

    def scroll_pos(self) -> geo.Scroll:
        return geo.Scroll(float(self.horizontalScrollBar().value()),
                          float(self.verticalScrollBar().value()))

    def viewport_size(self) -> QSize:
        return self.viewport().size()

    def relayout(self, anchor: Optional[Tuple[int, Point, QPointF]] = None) -> None:
        """Recompute slots. ``anchor`` = (page, pdf point, widget point) to keep
        fixed on screen; by default the point at the top-centre is kept."""
        if self.session.page_count == 0:
            return
        first = self._layout is None
        if anchor is None and not first:
            anchor = self._default_anchor()
        vp = self.viewport().size()
        self._current = max(0, min(self._current, self.session.page_count - 1))
        self._layout = compute_layout(self.session.page_sizes(), self._zoom, vp.width(),
                                      vp.height(), self._mode, self._current)
        self._apply_scroll_ranges()
        if anchor is not None:
            self._restore_anchor(*anchor)
        else:
            self.center_horizontally()
        self._generation += 1
        self.layoutChanged.emit()
        self.viewport().update()
        self._update_current_page()

    def _apply_scroll_ranges(self) -> None:
        lay = self._layout
        vp = self.viewport().size()
        self._suppress_scroll = True
        try:
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setRange(0, max(0, int(lay.width - vp.width())))
            v.setRange(0, max(0, int(lay.height - vp.height())))
            h.setPageStep(vp.width())
            v.setPageStep(max(1, vp.height() - 40))
        finally:
            self._suppress_scroll = False

    def center_horizontally(self) -> None:
        """Centre the current page horizontally (mixed page widths)."""
        h = self.horizontalScrollBar()
        if h.maximum() <= 0 or self._layout is None:
            return
        lay = self._layout
        # centre the whole row (a spread in two-page mode), not just one page
        row = next((r for r in lay.rows if self._current in r), (self._current,))
        slots = [s for s in (lay.slot_for(i) for i in row) if s is not None]
        vp_w = self.viewport().width()
        if slots:
            x0 = min(s.x for s in slots)
            x1 = max(s.right for s in slots)
            if x1 - x0 <= vp_w:
                h.setValue(int(round((x0 + x1) / 2 - vp_w / 2)))
                return
            h.setValue(int(round(x0 - 12)))
            return
        h.setValue(h.maximum() // 2)

    def _default_anchor(self) -> Optional[Tuple[int, Point, QPointF]]:
        vp = self.viewport().size()
        wp = QPointF(vp.width() / 2, min(60.0, vp.height() / 4))
        hit = self.nearest_page_at(wp)
        if hit is None:
            return None
        return (hit[0], hit[1], wp)

    def _restore_anchor(self, index: int, pt: Point, wp: QPointF) -> None:
        slot = self._layout.slot_for(index)
        if slot is None:
            return
        cx, cy = slot.transform(self._layout.scale).to_canvas(pt)
        self.horizontalScrollBar().setValue(int(round(cx - wp.x())))
        self.verticalScrollBar().setValue(int(round(cy - wp.y())))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()

    # ----------------------------------------------------------------- zoom
    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float, anchor_widget: Optional[QPointF] = None) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-4:
            return
        anchor = None
        if anchor_widget is not None:
            hit = self.nearest_page_at(anchor_widget)
            if hit is not None:
                anchor = (hit[0], hit[1], QPointF(anchor_widget))
        if anchor is None:
            vp = self.viewport().size()
            centre = QPointF(vp.width() / 2, vp.height() / 2)
            hit = self.nearest_page_at(centre)
            if hit is not None:
                anchor = (hit[0], hit[1], centre)
        self._zoom = zoom
        self.relayout(anchor)
        self.zoomChanged.emit(zoom)

    # ----------------------------------------------------------- navigation
    @property
    def current_page(self) -> int:
        return self._current

    def go_to_page(self, index: int, y_pt: Optional[float] = None) -> None:
        if self.session.page_count == 0:
            return
        index = max(0, min(index, self.session.page_count - 1))
        if self._mode == ViewMode.SINGLE:
            if index != self._current or self._layout is None:
                self._relayout_for_page(index)
            lay = self.layout_
            slot = lay.slot_for(index)
            y = 0.0 if (y_pt is None or slot is None) else slot.y + y_pt * lay.scale - 24
            self.verticalScrollBar().setValue(int(round(y)))
            return
        lay = self.layout_
        slot = lay.slot_for(index)
        if slot is None:
            return
        if y_pt is None:
            y = slot.y - 12
        else:
            y = slot.y + y_pt * lay.scale - 24
        self.verticalScrollBar().setValue(int(round(y)))
        if self._current != index:
            self._current = index
            self.currentPageChanged.emit(index)
        vp_w = self.viewport().width()
        hx = self.horizontalScrollBar().value()
        if slot.w <= vp_w and (slot.x < hx or slot.right > hx + vp_w):
            self.center_horizontally()

    def ensure_visible(self, index: int, rect: Rect, margin: int = 48) -> None:
        """Scroll minimally so a PDF-space rect on page ``index`` is visible."""
        if self._mode == ViewMode.SINGLE and index != self._current:
            self._relayout_for_page(index)
        lay = self.layout_
        slot = lay.slot_for(index)
        if slot is None:
            return
        x, y, w, h = slot.transform(lay.scale).rect_to_canvas(rect)
        s = self.scroll_pos()
        vp = self.viewport().size()
        if y < s.y + margin or y + h > s.y + vp.height() - margin:
            self.verticalScrollBar().setValue(int(round(y - vp.height() / 3)))
        if x < s.x + margin or x + w > s.x + vp.width() - margin:
            if w < vp.width() - 2 * margin:
                self.horizontalScrollBar().setValue(int(round(x + w / 2 - vp.width() / 2)))
            else:
                self.horizontalScrollBar().setValue(int(round(x - margin)))
        self._update_current_page()

    def _relayout_for_page(self, index: int) -> None:
        self._current = index
        vp = self.viewport().size()
        self._layout = compute_layout(self.session.page_sizes(), self._zoom, vp.width(),
                                      vp.height(), self._mode, self._current)
        self._apply_scroll_ranges()
        self.verticalScrollBar().setValue(0)
        self._generation += 1
        self.layoutChanged.emit()
        self.viewport().update()
        self.currentPageChanged.emit(index)

    def set_view_mode(self, mode: ViewMode) -> None:
        if mode == self._mode:
            return
        current = self._current
        self._mode = mode
        self._layout = None
        self.relayout(anchor=None)
        self.go_to_page(current)

    def _update_current_page(self) -> None:
        if self._layout is None or self._mode == ViewMode.SINGLE:
            return
        s = self.scroll_pos()
        vp = self.viewport().size()
        idx = current_page_for_view(self._layout, s.x, s.y, vp.width(), vp.height())
        if idx != self._current:
            self._current = idx
            self.currentPageChanged.emit(idx)

    def _on_scrolled(self, _value: int) -> None:
        if self._suppress_scroll:
            return
        self._update_current_page()
        self.viewport().update()
        self.viewChanged.emit()

    # ----------------------------------------------------------- coordinates
    def transform(self, index: int) -> Optional[geo.PageTransform]:
        slot = self.layout_.slot_for(index)
        return slot.transform(self.layout_.scale) if slot else None

    def page_at(self, wp: QPointF) -> Optional[Tuple[int, Point]]:
        """(page index, pdf point) under a widget point, or None."""
        s = self.scroll_pos()
        cx, cy = geo.widget_to_canvas(wp.x(), wp.y(), s)
        slot = self.layout_.slot_at(cx, cy)
        if slot is None:
            return None
        return slot.index, slot.transform(self.layout_.scale).to_pdf(cx, cy)

    def nearest_page_at(self, wp: QPointF, clamp: bool = False) -> Optional[Tuple[int, Point]]:
        s = self.scroll_pos()
        cx, cy = geo.widget_to_canvas(wp.x(), wp.y(), s)
        slot = self.layout_.slot_at(cx, cy) or self.layout_.nearest_slot(cx, cy)
        if slot is None:
            return None
        p = slot.transform(self.layout_.scale).to_pdf(cx, cy)
        if clamp:
            w, h = self.session.page_size(slot.index)
            p = Point(min(max(p.x, 0.0), w), min(max(p.y, 0.0), h))
        return slot.index, p

    def widget_to_pdf(self, index: int, wp: QPointF) -> Optional[Point]:
        t = self.transform(index)
        if t is None:
            return None
        return geo.widget_to_pdf(wp.x(), wp.y(), self.scroll_pos(), t)

    def pdf_to_widget(self, index: int, p: Point) -> Optional[QPointF]:
        t = self.transform(index)
        if t is None:
            return None
        x, y = geo.pdf_to_widget(p, self.scroll_pos(), t)
        return QPointF(x, y)

    def pdf_rect_to_widget(self, index: int, r: Rect) -> Optional[QRectF]:
        t = self.transform(index)
        if t is None:
            return None
        x, y, w, h = geo.pdf_rect_to_widget(r, self.scroll_pos(), t)
        return QRectF(x, y, w, h)

    def page_rect_widget(self, index: int) -> Optional[QRectF]:
        slot = self.layout_.slot_for(index)
        if slot is None:
            return None
        s = self.scroll_pos()
        return QRectF(slot.x - s.x, slot.y - s.y, slot.w, slot.h)

    def visible_slots(self) -> List[Slot]:
        s = self.scroll_pos()
        vp = self.viewport().size()
        return self.layout_.visible_slots(s.x, s.y, vp.width(), vp.height())

    def visible_pages(self) -> List[int]:
        return [s.index for s in self.visible_slots()]

    # ------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self.viewport())
        t = theme.current()
        p.fillRect(self.viewport().rect(), QColor(t.canvas))
        if self.session.doc.closed or self.session.page_count == 0:
            p.end()
            return
        lay = self.layout_
        dpr = self.viewport().devicePixelRatioF()
        if abs(dpr - self._dpr) > 1e-3:
            self._dpr = dpr
            self._generation += 1
        dev_scale = geo.device_scale(self._zoom, dpr)
        sk = geo.scale_key(dev_scale)
        if sk != self._scale_key:
            # zoom changed: keep the previous level as a fallback, shed the rest
            keep = {sk} | ({self._scale_key} if self._scale_key is not None else set())
            self.service.cache.drop_scales_except(self.session.uid, keep)
            self._scale_key = sk
        s = self.scroll_pos()
        vp = self.viewport().size()
        slots = lay.visible_slots(s.x, s.y, vp.width(), vp.height())
        cache = self.service.cache
        uid = self.session.uid
        missing: List[Tuple[float, TileKey, int, int, float, float]] = []
        cx_mid, cy_mid = s.x + vp.width() / 2, s.y + vp.height() / 2
        shadow = QColor(0, 0, 0, 34 if not t.is_dark else 90)
        border = QColor(0, 0, 0, 30 if not t.is_dark else 0)

        for slot in slots:
            page_rect = QRectF(slot.x - s.x, slot.y - s.y, slot.w, slot.h)
            # soft shadow + paper
            p.fillRect(page_rect.adjusted(-1, 0, 1, 2), shadow)
            p.fillRect(page_rect.adjusted(0, 1, 2, 3), QColor(0, 0, 0, 12))
            p.fillRect(page_rect, Qt.GlobalColor.white)
            pid, rev = self.session.render_key(slot.index)
            pw, ph = self.session.page_size(slot.index)
            rw, rh, _cols, _rows = geo.tile_grid(pw, ph, dev_scale, TILE)
            tiles = geo.visible_tiles(slot.x, slot.y, s.x, s.y, vp.width(), vp.height(),
                                      dpr, rw, rh, TILE)
            have = []
            page_missing = False
            for tx, ty in tiles:
                key = TileKey(uid, pid, rev, sk, tx, ty)
                img = cache.get(key)
                if img is None:
                    page_missing = True
                    tcx = slot.x + (tx + 0.5) * TILE / dpr
                    tcy = slot.y + (ty + 0.5) * TILE / dpr
                    dist = abs(tcx - cx_mid) / max(1, vp.width()) + abs(tcy - cy_mid) / max(1, vp.height())
                    missing.append((dist, key, rw, rh, pw, ph))
                else:
                    have.append(img)
            p.save()
            p.setClipRect(page_rect)
            if page_missing:
                self._paint_fallback(p, page_rect, uid, pid, rev, sk, pw)
            for img in have:
                off = img.offset()
                target = QRectF(page_rect.x() + off.x() / dpr, page_rect.y() + off.y() / dpr,
                                img.width() / dpr, img.height() / dpr)
                p.drawImage(target, img)
            p.restore()
            if border.alpha():
                p.setPen(QPen(border, 1))
                p.drawRect(page_rect.adjusted(-0.5, -0.5, 0.5, 0.5))

        for overlay in self.overlays:
            overlay.paint(p, self, slots)
        p.end()
        self._submit_requests(missing, dev_scale, sk, dpr, s, vp)

    def _paint_fallback(self, p: QPainter, page_rect: QRectF, uid: int, pid: int, rev: int,
                        sk: int, page_w_pt: float) -> None:
        subs = self.service.cache.fallback(uid, pid, rev, sk, exclude_scale=sk)
        if not subs:
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        px_per_pt = page_rect.width() / page_w_pt
        for key, img in subs:
            if key.tx == WHOLE_PAGE:
                p.drawImage(page_rect, img)
                continue
            s_k = key.scale / 1000.0  # device px per pt of that raster
            off = img.offset()
            f = px_per_pt / s_k
            p.drawImage(QRectF(page_rect.x() + off.x() * f, page_rect.y() + off.y() * f,
                               img.width() * f, img.height() * f), img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    # ------------------------------------------------------------- requests
    def _submit_requests(self, missing, dev_scale: float, sk: int, dpr: float,
                         s: geo.Scroll, vp: QSize) -> None:
        cache = self.service.cache
        uid = self.session.uid
        reqs: List[RenderRequest] = []
        missing.sort(key=lambda m: m[0])
        for rank, (dist, key, rw, rh, _pw, _ph) in enumerate(missing):
            clip = geo.tile_clip(key.tx, key.ty, rw, rh, dev_scale, TILE)
            reqs.append(RenderRequest(self._owner, key, rank,
                                      tile_job(self.session, key.page_id, key.revision,
                                               dev_scale, clip), self._generation))
        # Prefetch: one viewport above and below, lower priority.
        lay = self._layout
        bx, by, bw, bh = s.x, s.y - vp.height(), vp.width(), vp.height() * 3
        visible = {k.page_id for _d, k, *_ in missing}
        count = 0
        for slot in lay.visible_slots(bx, by, bw, bh):
            pid, rev = self.session.render_key(slot.index)
            pw, ph = self.session.page_size(slot.index)
            rw, rh, _c, _r = geo.tile_grid(pw, ph, dev_scale, TILE)
            for tx, ty in geo.visible_tiles(slot.x, slot.y, bx, by, bw, bh, dpr, rw, rh, TILE):
                key = TileKey(uid, pid, rev, sk, tx, ty)
                if key in cache or (pid in visible and any(r.key == key for r in reqs)):
                    continue
                clip = geo.tile_clip(tx, ty, rw, rh, dev_scale, TILE)
                reqs.append(RenderRequest(self._owner, key, 100 + count,
                                          tile_job(self.session, pid, rev, dev_scale, clip),
                                          self._generation))
                count += 1
                if count > 48:
                    break
        self.requests_submitted += len(reqs)
        self.service.submit(self._owner, reqs, self._generation)

    def _on_tile_ready(self, key: TileKey) -> None:
        if key.doc == self.session.uid:
            self.viewport().update()

    # -------------------------------------------------------- session events
    def _on_session_event(self, event: SessionEvent) -> None:
        if event.kind == EventKind.STRUCTURE:
            self._current = max(0, min(self._current, self.session.page_count - 1))
            self._layout = None if self.session.page_count == 0 else self._layout
            self.relayout()
        elif event.kind in (EventKind.PAGES, EventKind.ANNOTATIONS):
            self.viewport().update()

    # --------------------------------------------------------- input routing
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.mouse_handler is None or not self.mouse_handler.mouse_press(e):
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.mouse_handler is None or not self.mouse_handler.mouse_move(e):
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self.mouse_handler is None or not self.mouse_handler.mouse_release(e):
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.mouse_handler is None or not self.mouse_handler.mouse_double_click(e):
            super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        if self.mouse_handler is not None and hasattr(self.mouse_handler, "context_menu"):
            self.mouse_handler.context_menu(e)
            e.accept()
            return
        super().contextMenuEvent(e)

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = e.angleDelta().y() or e.pixelDelta().y() / 2
            if delta and self.wheel_zoom is not None:
                self.wheel_zoom(delta / 120.0, e.position())
            e.accept()
            return
        if self._mode == ViewMode.SINGLE and self._flip_on_wheel(e):
            e.accept()
            return
        super().wheelEvent(e)

    def _flip_on_wheel(self, e) -> bool:
        """Single-page mode: scrolling past a page edge turns the page."""
        dy = e.angleDelta().y() or e.pixelDelta().y()
        bar = self.verticalScrollBar()
        at_end = dy < 0 and bar.value() >= bar.maximum()
        at_start = dy > 0 and bar.value() <= bar.minimum()
        if not (at_end or at_start):
            self._flip_accum = 0
            return False
        self._flip_accum += dy
        if abs(self._flip_accum) < 120:
            return True  # absorb until a full notch past the edge
        self._flip_accum = 0
        if at_end and self._current < self.session.page_count - 1:
            self._relayout_for_page(self._current + 1)
            self.verticalScrollBar().setValue(0)
        elif at_start and self._current > 0:
            self._relayout_for_page(self._current - 1)
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        return True

    def viewportEvent(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.NativeGesture and self.wheel_zoom is not None:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                # convert the pinch factor into equivalent wheel notches
                import math
                notches = math.log(max(0.01, 1.0 + e.value())) / math.log(1.12)
                self.wheel_zoom(notches, e.position())
                return True
        return super().viewportEvent(e)

    def generation(self) -> int:
        return self._generation
