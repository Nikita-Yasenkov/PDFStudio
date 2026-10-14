"""Search: per-view controller (worker thread + canvas highlights) and the
side panel listing results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PyQt6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QRectF,
                          QSize, Qt, QThread, QTimer, pyqtSignal)
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QLabel,
                             QLineEdit, QListView, QProgressBar, QStackedWidget,
                             QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                             QWidget)

from ..core.search import SearchOptions, SearchRun
from ..core.session import EventKind, SessionEvent
from ..pdf.models import SearchHit
from . import theme
from .icons import icons
from .sidebar import PanelHeader

if TYPE_CHECKING:  # pragma: no cover
    from .document_view import DocumentView


class _SearchThread(QThread):
    batch = pyqtSignal(int, object)       # generation, [SearchHit]
    progress = pyqtSignal(int, int, int)  # generation, done, total
    finishedRun = pyqtSignal(int, bool)   # generation, completed

    def __init__(self, run: SearchRun, generation: int):
        super().__init__()
        self.search_run = run
        self.generation = generation

    def run(self) -> None:  # pragma: no cover - exercised through controller tests
        g = self.generation
        ok = self.search_run.run(lambda b: self.batch.emit(g, list(b)),
                                 lambda d, t: self.progress.emit(g, d, t))
        self.finishedRun.emit(g, ok)


class SearchController(QObject):
    """Owns one view's search: options, hits, current hit, highlights."""

    resultsChanged = pyqtSignal()
    progressChanged = pyqtSignal(int, int)
    currentChanged = pyqtSignal(int)
    stateChanged = pyqtSignal()

    def __init__(self, view: "DocumentView"):
        super().__init__(view)
        self.view = view
        self.options = SearchOptions("")
        self.hits: List[SearchHit] = []
        self.by_page: Dict[int, List[int]] = {}
        self.current = -1
        self.searching = False
        self.pages_done = 0
        self.truncated = False
        self.no_text = False  # finished without hits in a document with no text layer
        self._gen = 0
        self._threads: List[_SearchThread] = []
        self._run: Optional[SearchRun] = None
        self._want_jump = False
        self._start_page = 0
        view.canvas.overlays.insert(0, self)
        view.session.subscribe(self._on_session_event)
        self._rerun = QTimer(self)
        self._rerun.setSingleShot(True)
        self._rerun.setInterval(300)
        self._rerun.timeout.connect(lambda: self.search(self.options, jump=False))

    # ------------------------------------------------------------- control
    def search(self, options: SearchOptions, jump: bool = True) -> None:
        self.cancel()
        self._gen += 1
        self.options = options
        self.hits = []
        self.by_page = {}
        self.current = -1
        self.pages_done = 0
        self.truncated = False
        self.no_text = False
        self._want_jump = jump
        self._start_page = self.view.current_page
        if options.is_empty:
            self.searching = False
            self.resultsChanged.emit()
            self.currentChanged.emit(-1)
            self.stateChanged.emit()
            self.view.canvas.viewport().update()
            return
        self._run = SearchRun(self.view.session, options)
        th = _SearchThread(self._run, self._gen)
        th.batch.connect(self._on_batch)
        th.progress.connect(self._on_progress)
        th.finishedRun.connect(self._on_finished)
        th.finished.connect(lambda t=th: self._reap(t))
        self._threads.append(th)
        self.searching = True
        self.resultsChanged.emit()
        self.stateChanged.emit()
        self.view.canvas.viewport().update()
        th.start()

    def cancel(self) -> None:
        if self._run is not None:
            self._run.cancel()
            self._run = None
        if self.searching:
            self.searching = False
            self.stateChanged.emit()

    def clear(self) -> None:
        self.search(SearchOptions("", self.options.case_sensitive, self.options.whole_words),
                    jump=False)

    def shutdown(self) -> None:
        self.cancel()
        self._rerun.stop()
        self.view.session.unsubscribe(self._on_session_event)
        for th in list(self._threads):
            th.wait(2000)

    def _reap(self, th: _SearchThread) -> None:
        if th in self._threads:
            self._threads.remove(th)
        th.deleteLater()

    # --------------------------------------------------------- navigation
    def next(self) -> None:
        if self.hits:
            self.set_current((self.current + 1) % len(self.hits))

    def prev(self) -> None:
        if self.hits:
            self.set_current((self.current - 1) % len(self.hits) if self.current >= 0
                             else len(self.hits) - 1)

    def set_current(self, index: int, reveal: bool = True) -> None:
        if not 0 <= index < len(self.hits):
            return
        self.current = index
        self._want_jump = False
        if reveal:
            hit = self.hits[index]
            self.view.reveal(hit.page_index, hit.bounds)
        self.view.canvas.viewport().update()
        self.currentChanged.emit(index)

    @property
    def page_count_with_hits(self) -> int:
        return len(self.by_page)

    # ------------------------------------------------------------ results
    def _on_batch(self, gen: int, batch: List[SearchHit]) -> None:
        if gen != self._gen:
            return
        base = len(self.hits)
        self.hits.extend(batch)
        for i, h in enumerate(batch, start=base):
            self.by_page.setdefault(h.page_index, []).append(i)
        self.resultsChanged.emit()
        self.view.canvas.viewport().update()
        if self._want_jump and self.current < 0:
            for i, h in enumerate(self.hits):
                if h.page_index >= self._start_page:
                    self.set_current(i)
                    break

    def _on_progress(self, gen: int, done: int, total: int) -> None:
        if gen == self._gen:
            self.pages_done = done
            self.progressChanged.emit(done, total)

    def _on_finished(self, gen: int, completed: bool) -> None:
        if gen != self._gen:
            return
        self.searching = False
        if self._run is not None:
            self.truncated = self._run.truncated
        self._run = None
        if completed and not self.hits:
            self.no_text = not self._document_has_text()
        if self._want_jump and self.current < 0 and self.hits:
            self.set_current(0)  # wrap around
        self.stateChanged.emit()

    def _document_has_text(self, sample: int = 12) -> bool:
        """Whether any of up to ``sample`` evenly spread pages has text."""
        session = self.view.session
        n = session.page_count
        pages = range(n) if n <= sample else sorted(
            {round(i * (n - 1) / (sample - 1)) for i in range(sample)})
        try:
            return any(session.words(i) for i in pages)
        except Exception:  # noqa: BLE001 - unknown: don't claim the text is missing
            return True

    def _on_session_event(self, e: SessionEvent) -> None:
        if e.kind in (EventKind.STRUCTURE, EventKind.PAGES) and not self.options.is_empty:
            self._rerun.start()

    # ------------------------------------------------------------ overlay
    def paint(self, p: QPainter, canvas, slots) -> None:
        if not self.hits:
            return
        t = theme.current()
        hit_color = QColor(t.search_hit)
        cur_color = QColor(t.search_current)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for slot in slots:
            for i in self.by_page.get(slot.index, ()):
                hit = self.hits[i]
                for r in hit.rects:
                    wr = canvas.pdf_rect_to_widget(slot.index, r)
                    if wr is None:
                        continue
                    wr = wr.adjusted(-1, -1, 1, 1)
                    if i == self.current:
                        p.setBrush(cur_color)
                        p.drawRect(wr)
                        p.setPen(QPen(QColor("#EA580C"), 1.5))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(wr)
                        p.setPen(Qt.PenStyle.NoPen)
                    else:
                        p.setBrush(hit_color)
                        p.drawRect(wr)
        p.restore()


# ------------------------------------------------------------------ panel
HEADER_ROW = 0
HIT_ROW = 1


class SearchResultsModel(QAbstractListModel):
    KindRole = Qt.ItemDataRole.UserRole + 1
    HitRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: List[Tuple[int, int, int]] = []   # (kind, page, hit index or count)
        self.ctl: Optional[SearchController] = None
        self.hit_to_row: Dict[int, int] = {}

    def rebuild(self, ctl: Optional[SearchController]) -> None:
        self.beginResetModel()
        self.ctl = ctl
        self.rows = []
        self.hit_to_row = {}
        if ctl is not None:
            for page in sorted(ctl.by_page):
                idxs = ctl.by_page[page]
                self.rows.append((HEADER_ROW, page, len(idxs)))
                for i in idxs:
                    self.hit_to_row[i] = len(self.rows)
                    self.rows.append((HIT_ROW, page, i))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, page, val = self.rows[index.row()]
        if role == self.KindRole:
            return kind
        if role == self.HitRole:
            return val if kind == HIT_ROW else -1
        if role == Qt.ItemDataRole.DisplayRole:
            if kind == HEADER_ROW:
                return f"Page {page + 1}"
            return self.ctl.hits[val].snippet if self.ctl else ""
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        kind = self.rows[index.row()][0]
        if kind == HEADER_ROW:
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class SearchResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        kind = index.data(SearchResultsModel.KindRole)
        return QSize(option.rect.width(), 28 if kind == HEADER_ROW else 30)

    def paint(self, p: QPainter, option, index) -> None:
        t = theme.current()
        model: SearchResultsModel = index.model()
        kind, page, val = model.rows[index.row()]
        r = QRectF(option.rect)
        p.save()
        if kind == HEADER_ROW:
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() - 0.5))
            p.setFont(f)
            p.setPen(QColor(t.text_muted))
            text = f"PAGE {page + 1}"
            p.drawText(r.adjusted(14, 6, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            p.setPen(QColor(t.text_faint))
            p.drawText(r.adjusted(14, 6, -12, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{val}")
            p.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = r.adjusted(6, 1, -6, -1)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_soft))
            p.drawRoundedRect(card, 6, 6)
        elif hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.surface_alt))
            p.drawRoundedRect(card, 6, 6)
        hit = model.ctl.hits[val] if model.ctl else None
        if hit is None:
            p.restore()
            return
        text_rect = card.adjusted(10, 0, -8, 0)
        fm = QFontMetrics(option.font)
        bold = QFont(option.font)
        bold.setBold(True)
        bfm = QFontMetrics(bold)
        pre = hit.snippet[:hit.match_start]
        mid = hit.snippet[hit.match_start:hit.match_end]
        post = hit.snippet[hit.match_end:]
        avail = int(text_rect.width())
        mid_w = bfm.horizontalAdvance(mid)
        if mid_w > avail:
            mid = bfm.elidedText(mid, Qt.TextElideMode.ElideRight, avail)
            mid_w = bfm.horizontalAdvance(mid)
            pre, post = "", ""
        pre_budget = min(fm.horizontalAdvance(pre), max(0, (avail - mid_w) // 3))
        pre = fm.elidedText(pre, Qt.TextElideMode.ElideLeft, pre_budget) if pre else ""
        pre_w = fm.horizontalAdvance(pre)
        post = fm.elidedText(post, Qt.TextElideMode.ElideRight, max(0, avail - pre_w - mid_w))
        x = text_rect.x()
        y_mid = text_rect.center().y()
        baseline = y_mid + (fm.ascent() - fm.descent()) / 2
        p.setFont(option.font)
        p.setPen(QColor(t.text_muted))
        p.drawText(int(x), int(baseline), pre)
        x += pre_w
        mark = QRectF(x - 1, y_mid - fm.height() / 2, mid_w + 2, fm.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.search_hit))
        p.drawRoundedRect(mark, 3, 3)
        p.setFont(bold)
        p.setPen(QColor(t.text))
        p.drawText(int(x), int(baseline), mid)
        x += mid_w
        p.setFont(option.font)
        p.setPen(QColor(t.text_muted))
        p.drawText(int(x), int(baseline), post)
        p.restore()


class SearchPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.view: Optional["DocumentView"] = None
        self.ctl: Optional[SearchController] = None
        self._syncing = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PanelHeader("Search", self)
        lay.addWidget(self.header)

        box = QWidget(self)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 2, 12, 6)
        bl.setSpacing(8)
        self.field = QLineEdit(box)
        self.field.setObjectName("SearchField")
        self.field.setPlaceholderText("Find in document")
        self.field.setClearButtonEnabled(True)
        self._icon_action = QAction(icons.icon("search"), "", self.field)
        self.field.addAction(self._icon_action, QLineEdit.ActionPosition.LeadingPosition)
        self.field.textEdited.connect(self._on_text_edited)
        self.field.returnPressed.connect(self._on_return)
        self.field.installEventFilter(self)
        bl.addWidget(self.field)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self.case_box = QCheckBox("Match case", box)
        self.words_box = QCheckBox("Whole words", box)
        for cb in (self.case_box, self.words_box):
            cb.toggled.connect(lambda _=False: self._run_now(jump=False))
            opts.addWidget(cb)
        opts.addStretch(1)
        bl.addLayout(opts)

        # result count + previous/next; shown only while there is something to count
        self.status_row = QWidget(box)
        status = QHBoxLayout(self.status_row)
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(2)
        self.status = QLabel("", box)
        self.status.setProperty("role", "muted")
        status.addWidget(self.status, 1)
        self.prev_btn = QToolButton(box)
        self.prev_btn.setIcon(icons.icon("chevron-up"))
        self.prev_btn.setProperty("icon_name", "chevron-up")
        self.prev_btn.setToolTip("Previous result (Shift+F3)")
        self.prev_btn.setAutoRaise(True)
        self.prev_btn.clicked.connect(self.prev)
        self.next_btn = QToolButton(box)
        self.next_btn.setIcon(icons.icon("chevron-down"))
        self.next_btn.setProperty("icon_name", "chevron-down")
        self.next_btn.setToolTip("Next result (F3 or Enter)")
        self.next_btn.setAutoRaise(True)
        self.next_btn.clicked.connect(self.next)
        status.addWidget(self.prev_btn)
        status.addWidget(self.next_btn)
        bl.addWidget(self.status_row)
        lay.addWidget(box)

        self.progress = QProgressBar(self)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        lay.addWidget(self.progress)

        # results and the empty state share one stretching slot, so the rows
        # above keep their size whichever is shown
        self.body = QStackedWidget(self)
        self.list = QListView(self.body)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model = SearchResultsModel(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(SearchResultDelegate(self.list))
        self.list.selectionModel().currentChanged.connect(self._on_row_selected)
        self.list.clicked.connect(lambda idx: self._on_row_selected(idx, None))
        self.body.addWidget(self.list)

        self.empty = QWidget(self.body)
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(20, 36, 20, 16)
        el.setSpacing(6)
        self.empty_icon = QLabel(self.empty)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(self.empty_icon)
        el.addSpacing(4)
        self.empty_title = QLabel(self.empty)
        self.empty_title.setProperty("role", "empty-title")
        self.empty_detail = QLabel(self.empty)
        self.empty_detail.setProperty("role", "faint")
        for lbl in (self.empty_title, self.empty_detail):
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setWordWrap(True)
            el.addWidget(lbl)
        el.addStretch(1)
        self.body.addWidget(self.empty)
        lay.addWidget(self.body, 1)
        self._refresh_empty_icon()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(lambda: self._run_now(jump=True))
        self._update_status()

    # --------------------------------------------------------------- binding
    def set_view(self, view: Optional["DocumentView"]) -> None:
        if view is self.view:
            return
        if self.ctl is not None:
            for sig, slot in self._connections():
                try:
                    sig.disconnect(slot)
                except TypeError:
                    pass
        self.view = view
        self.ctl = view.search if view is not None else None
        if self.ctl is not None:
            for sig, slot in self._connections():
                sig.connect(slot)
            o = self.ctl.options
            self._syncing = True
            self.field.setText(o.query)
            self.case_box.setChecked(o.case_sensitive)
            self.words_box.setChecked(o.whole_words)
            self._syncing = False
        else:
            self._syncing = True
            self.field.clear()
            self._syncing = False
        self.field.setEnabled(view is not None)
        self._on_results()
        self._on_state()

    def _connections(self):
        c = self.ctl
        return [(c.resultsChanged, self._on_results), (c.stateChanged, self._on_state),
                (c.progressChanged, self._on_progress), (c.currentChanged, self._on_current)]

    # ---------------------------------------------------------------- input
    def focus_field(self, text: Optional[str] = None) -> None:
        if text:
            self.field.setText(text)
            self._run_now(jump=True)
        self.field.setFocus()
        self.field.selectAll()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.field and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.field.clear()
                self._run_now(jump=False)
                if self.view is not None:
                    self.view.canvas.setFocus()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.prev()
                return True
            if event.key() == Qt.Key.Key_Down:
                self.next()
                return True
            if event.key() == Qt.Key.Key_Up:
                self.prev()
                return True
        return super().eventFilter(obj, event)

    def _on_text_edited(self, _text: str) -> None:
        self._debounce.start()

    def _options(self) -> SearchOptions:
        return SearchOptions(self.field.text(), self.case_box.isChecked(),
                             self.words_box.isChecked())

    def _run_now(self, jump: bool = True) -> None:
        if self._syncing or self.ctl is None:
            return
        self._debounce.stop()
        opts = self._options()
        if opts != self.ctl.options:
            self.ctl.search(opts, jump=jump)

    def _on_return(self) -> None:
        if self.ctl is None:
            return
        opts = self._options()
        if opts != self.ctl.options:
            self._debounce.stop()
            self.ctl.search(opts, jump=True)
        else:
            self.next()

    def next(self) -> None:
        if self.ctl is not None:
            self._run_now(jump=True)
            self.ctl.next()

    def prev(self) -> None:
        if self.ctl is not None:
            self._run_now(jump=True)
            self.ctl.prev()

    # -------------------------------------------------------------- updates
    def _on_results(self) -> None:
        self.model.rebuild(self.ctl)
        if self.ctl is not None and self.ctl.current >= 0:
            self._on_current(self.ctl.current)
        self._update_status()

    def _on_state(self) -> None:
        searching = bool(self.ctl and self.ctl.searching)
        self.progress.setVisible(searching)
        if searching:
            self.progress.setRange(0, max(1, self.view.page_count))
            self.progress.setValue(0)
        self._update_status()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self._update_status()

    def _on_current(self, index: int) -> None:
        row = self.model.hit_to_row.get(index)
        self._syncing = True
        if row is not None:
            mi = self.model.index(row)
            self.list.setCurrentIndex(mi)
            self.list.scrollTo(mi)
        else:
            self.list.clearSelection()
        self._syncing = False
        self._update_status()

    def _on_row_selected(self, current: QModelIndex, _previous) -> None:
        if self._syncing or self.ctl is None or not current.isValid():
            return
        hit = current.data(SearchResultsModel.HitRole)
        if hit is not None and hit >= 0 and hit != self.ctl.current:
            self.ctl.set_current(hit)

    def _update_status(self) -> None:
        c = self.ctl
        has_query = bool(c and not c.options.is_empty)
        n = len(c.hits) if c else 0
        if c is None or not has_query:
            text = ""
        elif c.searching:
            text = f"Searching… {n} found · page {c.pages_done} of {self.view.page_count}"
        elif n == 0:
            text = "No results"
        else:
            pages = c.page_count_with_hits
            cur = f"{c.current + 1} of {n}" if c.current >= 0 else f"{n} result{'s' if n != 1 else ''}"
            text = f"{cur} · {pages} page{'s' if pages != 1 else ''}"
            if c.truncated:
                text += " (first 5000)"
        self.status.setText(text)
        self.prev_btn.setEnabled(n > 0)
        self.next_btn.setEnabled(n > 0)
        self.status_row.setVisible(has_query and (n > 0 or bool(c and c.searching)))
        if n > 0 or (c is not None and c.searching):
            self.body.setCurrentWidget(self.list)
            return
        self.body.setCurrentWidget(self.empty)
        if not has_query:
            title = "Search this document"
            detail = "Find words and numbers on every page. Press Enter or F3 for the next match."
        elif c.no_text:
            title = "No searchable text"
            detail = ("This document seems to be scanned images, so there is no text "
                      "to search.")
        else:
            title = f"No matches for “{c.options.query}”"
            on = [name for name, box in (("Match case", self.case_box),
                                         ("Whole words", self.words_box)) if box.isChecked()]
            if on:
                detail = f"Try turning off {' and '.join(on)}."
            else:
                detail = "Check the spelling, or search for part of the word or number."
        self.empty_title.setText(title)
        self.empty_detail.setText(detail)

    def _refresh_empty_icon(self) -> None:
        self.empty_icon.setPixmap(icons.pixmap("search", 28, QColor(theme.current().text_faint)))

    def refresh_icons(self) -> None:
        self.header.refresh_icons()
        self._icon_action.setIcon(icons.icon("search"))
        self._refresh_empty_icon()
        self.prev_btn.setIcon(icons.icon("chevron-up"))
        self.next_btn.setIcon(icons.icon("chevron-down"))
