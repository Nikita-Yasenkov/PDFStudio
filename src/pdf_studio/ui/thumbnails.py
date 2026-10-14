"""Page thumbnails side panel.

Thumbnails are rendered lazily: the delegate asks for an image only when a
row is painted, and missing images are queued at low priority on the shared
render service. They are keyed by stable page id + revision, so reordering
pages never re-rasterises them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Set

from PyQt6.QtCore import (QAbstractListModel, QItemSelectionModel, QModelIndex, QRect,
                          QRectF, QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QListView, QStyle,
                             QStyledItemDelegate, QStyleOptionViewItem,
                             QVBoxLayout, QWidget)

from ..core import geometry as geo
from ..core.render_cache import WHOLE_PAGE, TileKey
from ..core.render_queue import RenderRequest
from ..core.session import EventKind, SessionEvent
from . import theme
from .render_service import RenderService, tile_job
from .sidebar import PanelHeader

if TYPE_CHECKING:  # pragma: no cover
    from .document_view import DocumentView

LABEL_H = 22
PAD_Y = 10
MIN_THUMB_W = 80
MAX_THUMB_W = 240
THUMB_STEP = 20


class ThumbnailModel(QAbstractListModel):
    PageIndexRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: Optional["DocumentView"] = None
        # Qt requires the row count to change only inside reset/insert/remove
        # notifications, so it is cached rather than read live.
        self._count = 0
        self.ids: List[int] = []   # page id per row at the last reset

    def set_view(self, view: Optional["DocumentView"]) -> None:
        self.beginResetModel()
        self.view = view
        live = view is not None and not view.session.doc.closed
        self._count = view.session.page_count if live else 0
        self.ids = list(view.session.page_ids) if live else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._count

    def _live(self, row: int) -> bool:
        v = self.view
        return v is not None and not v.session.doc.closed and 0 <= row < v.session.page_count

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not self._live(index.row()):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.view.session.page_label(index.row())
        if role == Qt.ItemDataRole.ToolTipRole:
            w, h = self.view.session.page_size(index.row())
            return f"Page {index.row() + 1}"
        if role == self.PageIndexRole:
            return index.row()
        return None

    def flags(self, index: QModelIndex):
        f = super().flags(index)
        if index.isValid():
            f |= Qt.ItemFlag.ItemIsDragEnabled
        else:
            f |= Qt.ItemFlag.ItemIsDropEnabled
        return f

    def supportedDropActions(self):  # noqa: N802
        return Qt.DropAction.MoveAction

    def supportedDragActions(self):  # noqa: N802
        return Qt.DropAction.MoveAction

    def refresh_rows(self, rows) -> None:
        for r in rows:
            if 0 <= r < self.rowCount():
                idx = self.index(r)
                self.dataChanged.emit(idx, idx)


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, panel: "ThumbnailPanel"):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        if not self.panel.model._live(index.row()):
            return QSize(self.panel.list.viewport().width(), 40)
        box = self.panel.thumb_box(index.row())
        return QSize(self.panel.list.viewport().width(), box.height() + LABEL_H + 2 * PAD_Y)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        t = theme.current()
        view = self.panel.view
        if view is None:
            return
        row = index.row()
        if not self.panel.model._live(row):
            return
        r = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        current = row == view.current_page
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = QRectF(r).adjusted(8, 3, -8, -3)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_soft))
            p.drawRoundedRect(card, 8, 8)
        elif hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.surface_alt))
            p.drawRoundedRect(card, 8, 8)

        box = self.panel.thumb_box(row)
        x = r.x() + (r.width() - box.width()) / 2
        y = r.y() + PAD_Y
        target = QRectF(x, y, box.width(), box.height())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(target.adjusted(-1, 0, 1, 2), QColor(0, 0, 0, 28 if not t.is_dark else 80))
        p.fillRect(target, Qt.GlobalColor.white)
        img = self.panel.thumbnail(row)
        if img is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(target, img)
        if current:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(t.accent), 2.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(target.adjusted(-3, -3, 3, 3), 3, 3)
        else:
            p.setPen(QPen(QColor(0, 0, 0, 40 if not t.is_dark else 0), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)  # don't fill over the page with the card color
            p.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))

        label_rect = QRectF(r.x(), y + box.height() + 4, r.width(), LABEL_H - 4)
        f = QFont(option.font)
        if current:
            f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(t.accent if current else t.text_muted))
        p.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   index.data(Qt.ItemDataRole.DisplayRole))
        p.restore()


class ThumbnailList(QListView):
    """List view with thumbnail-friendly defaults and drag-to-reorder.

    Drops are not applied by Qt: the view reports (rows, target) and the
    panel turns that into an undoable page-move command.
    """

    rowsDropped = pyqtSignal(list, int)   # selected rows, drop position (0..n)
    filesDropped = pyqtSignal(list, int)  # external PDF paths, drop position
    deletePressed = pyqtSignal()
    contextRequested = pyqtSignal(object)  # global QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(36)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.verticalScrollBar().setSingleStep(24)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.contextRequested.emit(self.viewport().mapToGlobal(pos)))

    def drop_target(self, pos) -> int:
        """Insertion position (0..rowCount) for a drop at viewport ``pos``."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.model().rowCount()
        rect = self.visualRect(idx)
        return idx.row() + (1 if pos.y() > rect.center().y() else 0)

    @staticmethod
    def _pdf_paths(mime) -> list:
        return [u.toLocalFile() for u in mime.urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")]

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.source() is not self and self._pdf_paths(e.mimeData()):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.source() is not self and self._pdf_paths(e.mimeData()):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.source() is not self:
            paths = self._pdf_paths(e.mimeData())
            if paths:
                e.setDropAction(Qt.DropAction.CopyAction)
                e.accept()
                self.filesDropped.emit(paths, self.drop_target(e.position().toPoint()))
            else:
                e.ignore()
            return
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        target = self.drop_target(e.position().toPoint())
        e.setDropAction(Qt.DropAction.IgnoreAction)  # we apply the move ourselves
        e.accept()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if rows:
            self.rowsDropped.emit(rows, target)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deletePressed.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class ThumbnailPanel(QWidget):
    pageActivated = pyqtSignal(int)
    selectionChanged = pyqtSignal()
    #: (page ids, drop position before original index) — window applies the command
    moveRequested = pyqtSignal(list, int)
    filesDropped = pyqtSignal(list, int)
    deleteRequested = pyqtSignal(list)
    contextMenuRequested = pyqtSignal(list, object)   # page ids, global pos

    def __init__(self, service: RenderService, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.service = service
        self.view: Optional["DocumentView"] = None
        self._owner = ("thumbs", id(self))
        self._generation = 0
        self._wanted: Set[int] = set()
        self._thumb_w = 120
        self.requests_submitted = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PanelHeader("Pages", self)
        lay.addWidget(self.header)
        self.list = ThumbnailList(self)
        self.model = ThumbnailModel(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(ThumbnailDelegate(self))
        self.list.clicked.connect(self._on_clicked)
        self.list.selectionModel().selectionChanged.connect(lambda *_: self.selectionChanged.emit())
        self.list.rowsDropped.connect(self._on_rows_dropped)
        self.list.filesDropped.connect(self.filesDropped)
        self.list.deletePressed.connect(self._on_delete_pressed)
        self.list.contextRequested.connect(self._on_context)
        lay.addWidget(self.list, 1)

        self._submit_timer = QTimer(self)
        self._submit_timer.setSingleShot(True)
        self._submit_timer.setInterval(0)
        self._submit_timer.timeout.connect(self._submit)
        service.register(self._owner, self._on_tile_ready)

    # ---------------------------------------------------------------- binding
    def _ids(self, rows) -> List[int]:
        s = self.view.session
        return [s.page_id(r) for r in rows if 0 <= r < s.page_count]

    def _on_rows_dropped(self, rows: list, target: int) -> None:
        if self.view is not None:
            self.moveRequested.emit(self._ids(rows), target)

    def _on_delete_pressed(self) -> None:
        if self.view is not None and self.selected_rows():
            self.deleteRequested.emit(self._ids(self.selected_rows()))

    def _on_context(self, global_pos) -> None:
        if self.view is None:
            return
        pos = self.list.viewport().mapFromGlobal(global_pos)
        idx = self.list.indexAt(pos)
        if idx.isValid() and idx.row() not in self.selected_rows():
            self.list.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        rows = self.selected_rows() or ([idx.row()] if idx.isValid() else [])
        self.contextMenuRequested.emit(self._ids(rows), global_pos)

    def selected_rows(self) -> List[int]:
        return sorted(i.row() for i in self.list.selectionModel().selectedIndexes())

    def add_header_action(self, action) -> None:
        from PyQt6.QtWidgets import QToolButton
        b = QToolButton(self.header)
        b.setDefaultAction(action)
        b.setIconSize(QSize(16, 16))
        b.setAutoRaise(True)
        self.header.add_widget(b)

    def set_view(self, view: Optional["DocumentView"]) -> None:
        if view is self.view:
            return
        if self.view is not None:
            try:
                self.view.currentPageChanged.disconnect(self._on_current_changed)
            except TypeError:
                pass
            self.view.session.unsubscribe(self._on_session_event)
        self.view = view
        self._generation += 1
        self._wanted.clear()
        self.service.queue.cancel_owner(self._owner)
        self.model.set_view(view)
        if view is not None:
            view.currentPageChanged.connect(self._on_current_changed)
            view.session.subscribe(self._on_session_event)
            self._scroll_to_current()

    def dispose(self) -> None:
        self.set_view(None)
        self.service.unregister(self._owner)

    # ------------------------------------------------------------- geometry
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        avail = self.list.viewport().width() - 40
        w = max(MIN_THUMB_W, min(MAX_THUMB_W, int(avail // THUMB_STEP) * THUMB_STEP))
        if w != self._thumb_w:
            self._thumb_w = w
            self._generation += 1
            self.list.doItemsLayout()

    def thumb_box(self, row: int) -> QSize:
        if self.view is None:
            return QSize(self._thumb_w, int(self._thumb_w * 1.414))
        pw, ph = self.view.session.page_size(row)
        max_h = self._thumb_w * 1.414
        scale = min(self._thumb_w / pw, max_h / ph)
        return QSize(max(8, int(round(pw * scale))), max(8, int(round(ph * scale))))

    # ------------------------------------------------------------ thumbnails
    def _key(self, row: int) -> TileKey:
        s = self.view.session
        pid, rev = s.render_key(row)
        box = self.thumb_box(row)
        dpr = self.list.viewport().devicePixelRatioF()
        dev_scale = box.width() * dpr / s.page_size(row)[0]
        return TileKey(s.uid, pid, rev, geo.scale_key(dev_scale), WHOLE_PAGE, WHOLE_PAGE)

    def thumbnail(self, row: int) -> Optional[QImage]:
        if self.view is None or self.view.session.doc.closed:
            return None
        key = self._key(row)
        img = self.service.cache.get(key)
        if img is not None:
            return img
        self._wanted.add(row)
        self._submit_timer.start()
        # meanwhile, any raster of this page (older revision / other size)
        subs = self.service.cache.fallback(key.doc, key.page_id, key.revision, key.scale)
        for k, v in subs:
            if k.tx == WHOLE_PAGE:
                return v
        return None

    def _submit(self) -> None:
        if self.view is None:
            return
        s = self.view.session
        vp_rect = self.list.viewport().rect()
        reqs: List[RenderRequest] = []
        first = self.list.indexAt(vp_rect.topLeft()).row()
        for row in sorted(self._wanted):
            if row >= s.page_count:
                continue
            idx = self.model.index(row)
            if not self.list.visualRect(idx).intersects(vp_rect.adjusted(0, -200, 0, 200)):
                continue
            key = self._key(row)
            if key in self.service.cache:
                continue
            dev_scale = key.scale / 1000.0
            reqs.append(RenderRequest(self._owner, key, 200 + abs(row - max(0, first)),
                                      tile_job(s, key.page_id, key.revision, dev_scale, None),
                                      self._generation))
        self._wanted.clear()
        self.requests_submitted += len(reqs)
        self.service.submit(self._owner, reqs, self._generation)

    def _on_tile_ready(self, key: TileKey) -> None:
        if self.view is None or key.doc != self.view.session.uid:
            return
        row = self.view.session.index_of(key.page_id)
        if row is not None:
            self.model.refresh_rows([row])

    # ---------------------------------------------------------------- events
    def _on_clicked(self, index: QModelIndex) -> None:
        if index.isValid() and self.view is not None:
            self.view.go_to_page(index.row())
            self.pageActivated.emit(index.row())

    def _on_current_changed(self, row: int) -> None:
        # a single selection follows the current page; multi-selections stay
        if len(self.selected_rows()) <= 1:
            idx = self.model.index(row)
            if idx.isValid():
                self.list.selectionModel().setCurrentIndex(
                    idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.list.viewport().update()
        self._scroll_to_current()

    def _scroll_to_current(self) -> None:
        if self.view is None:
            return
        idx = self.model.index(self.view.current_page)
        if idx.isValid():
            self.list.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def _on_session_event(self, event: SessionEvent) -> None:
        if event.kind == EventKind.STRUCTURE:
            # keep the selection (by page id) across the reset
            selected = {self.model.ids[r] for r in self.selected_rows() if r < len(self.model.ids)}
            self.model.set_view(self.view)
            if len(selected) > 1:
                sm = self.list.selectionModel()
                for pid in selected:
                    row = self.view.session.index_of(pid)
                    if row is not None:
                        sm.select(self.model.index(row), QItemSelectionModel.SelectionFlag.Select)
            self._scroll_to_current()
            self.selectionChanged.emit()
        elif event.kind in (EventKind.PAGES, EventKind.ANNOTATIONS):
            s = self.view.session
            rows = [s.index_of(pid) for pid in event.page_ids]
            self.model.refresh_rows([r for r in rows if r is not None])

    def refresh_icons(self) -> None:
        self.header.refresh_icons()
