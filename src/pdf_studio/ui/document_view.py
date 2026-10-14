"""DocumentView — one open document (a tab): session + canvas + view state.

Each view owns its own zoom mode, zoom, scroll position, current page, view
mode and tool state, so tabs never share mutable view state.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QAbstractScrollArea, QWidget

from ..core import zoom as zm
from ..core.layout import ViewMode, next_page_index
from ..core.annotation_commands import DeleteAnnotationCommand
from ..core.annotation_geometry import handles
from ..core.selection import TextSelection, select_all
from ..pdf.models import AnnotationKind, AnnotationModel
from ..core.session import DocumentSession, EventKind, SessionEvent
from . import theme
from .canvas import PageCanvas
from .floating_bar import PageZoomBar
from .render_service import RenderService
from .search import SearchController
from .tool_defaults import ToolDefaults
from .tools import ToolController
from .widgets import Toast


class _AnnotationOverlay:
    """Hover outline, selection frame + handles and the move/resize ghost."""

    HANDLE = 7.0

    def __init__(self, view: "DocumentView"):
        self.view = view

    def _model(self, key):
        if key is None:
            return None
        pid, aid = key
        index = self.view.session.index_of(pid)
        if index is None:
            return None
        m = self.view.session.annotation_by_id(pid, aid)
        return (index, m) if m is not None else None

    def _outline(self, p, canvas, index: int, m: AnnotationModel, pen: QPen) -> None:
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if m.kind in (AnnotationKind.LINE, AnnotationKind.ARROW) and m.strokes:
            a = canvas.pdf_to_widget(index, m.strokes[0][0])
            b = canvas.pdf_to_widget(index, m.strokes[0][-1])
            p.drawLine(a, b)
            return
        if m.kind.is_text_markup and m.quads:
            for q in m.quads:
                r = canvas.pdf_rect_to_widget(index, q)
                if r is not None:
                    p.drawRect(r.adjusted(-2, -1, 2, 1))
            return
        r = canvas.pdf_rect_to_widget(index, m.rect)
        if r is not None:
            p.drawRect(r.adjusted(-3, -3, 3, 3))

    def paint(self, p, canvas, slots) -> None:
        visible = {s.index for s in slots}
        t = theme.current()
        accent = QColor(t.annot_select)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hov = self._model(self.view.hover_annot) if self.view.hover_annot != self.view.selected_annot else None
        if hov is not None and hov[0] in visible:
            c = QColor(accent)
            c.setAlpha(110)
            self._outline(p, canvas, hov[0], hov[1], QPen(c, 1.2))
        preview = self.view.annotation_preview
        sel = self._model(self.view.selected_annot)
        if sel is not None and sel[0] in visible:
            index, m = sel
            shown = preview[1] if preview is not None and preview[0] == index else m
            if preview is not None:
                ghost = QColor(accent)
                ghost.setAlpha(40)
                self._ghost(p, canvas, index, shown, ghost, accent)
            self._outline(p, canvas, index, shown, QPen(accent, 1.4, Qt.PenStyle.DashLine
                                                        if preview is not None else Qt.PenStyle.SolidLine))
            if preview is None:
                for _name, hp in handles(shown):
                    c = canvas.pdf_to_widget(index, hp)
                    h = self.HANDLE
                    rect = QRectF(c.x() - h / 2, c.y() - h / 2, h, h)
                    p.setPen(QPen(accent, 1.4))
                    p.setBrush(QColor(t.surface))
                    p.drawRoundedRect(rect, 2, 2)
        p.restore()

    def _ghost(self, p, canvas, index: int, m: AnnotationModel, fill: QColor, line: QColor) -> None:
        scale = canvas.layout_.scale
        if m.kind == AnnotationKind.INK:
            pen = QPen(line, max(1.0, m.width * scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for stroke in m.strokes:
                pts = [canvas.pdf_to_widget(index, q) for q in stroke]
                for a, b in zip(pts, pts[1:]):
                    p.drawLine(a, b)
            return
        if m.kind in (AnnotationKind.LINE, AnnotationKind.ARROW) and m.strokes:
            p.setPen(QPen(line, max(1.0, m.width * scale)))
            p.drawLine(canvas.pdf_to_widget(index, m.strokes[0][0]),
                       canvas.pdf_to_widget(index, m.strokes[0][-1]))
            return
        r = canvas.pdf_rect_to_widget(index, m.rect)
        if r is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        if m.kind == AnnotationKind.ELLIPSE:
            p.drawEllipse(r)
        else:
            p.drawRect(r)


class _SelectionOverlay:
    """Paints the view's text selection."""

    def __init__(self, view: "DocumentView"):
        self.view = view

    def paint(self, p, canvas, slots) -> None:
        sel = self.view.text_selection
        if sel is None:
            return
        if not any(s.index == sel.page_index for s in slots):
            return
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.current().text_selection))
        for r in sel.line_rects:
            wr = canvas.pdf_rect_to_widget(sel.page_index, r.inflated(0.6))
            if wr is not None:
                p.drawRect(wr)
        p.restore()


class DocumentView(QWidget):
    titleChanged = pyqtSignal()
    currentPageChanged = pyqtSignal(int)
    zoomChanged = pyqtSignal(float)
    statusMessage = pyqtSignal(str)
    historyChanged = pyqtSignal()
    errorOccurred = pyqtSignal(str, str)  # title, message
    selectionChanged = pyqtSignal()       # text selection
    searchRequested = pyqtSignal(str)
    annotationSelectionChanged = pyqtSignal()
    viewModeChanged = pyqtSignal(object)
    propertiesRequested = pyqtSignal()

    def __init__(self, session: DocumentSession, service: RenderService,
                 parent: Optional[QWidget] = None, zoom_mode: zm.ZoomMode = zm.ZoomMode.AUTO,
                 view_mode: ViewMode = ViewMode.CONTINUOUS,
                 tool_defaults: Optional[ToolDefaults] = None):
        super().__init__(parent)
        self.session = session
        self.service = service
        self.zoom_mode = zoom_mode
        self.canvas = PageCanvas(session, service, self)
        self.canvas.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        if view_mode != ViewMode.CONTINUOUS:
            self.canvas._mode = view_mode  # before first layout
        self.search = SearchController(self)
        self.text_selection: Optional[TextSelection] = None
        self.selected_annot: Optional[Tuple[int, str]] = None   # (page id, annot id)
        self.hover_annot: Optional[Tuple[int, str]] = None
        self.annotation_preview: Optional[Tuple[int, AnnotationModel]] = None
        self.canvas.overlays.append(_SelectionOverlay(self))
        self.canvas.overlays.append(_AnnotationOverlay(self))
        self.tools = ToolController(self, tool_defaults)
        self.bar = PageZoomBar(self)
        self.toast_widget = Toast(self)
        self._fitted_once = False

        self.canvas.currentPageChanged.connect(self._on_page_changed)
        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self.canvas.wheel_zoom = self._on_wheel_zoom
        self.bar.prevRequested.connect(self.prev_page)
        self.bar.nextRequested.connect(self.next_page)
        self.bar.pageRequested.connect(self.go_to_page)
        self.bar.zoomInRequested.connect(self.zoom_in)
        self.bar.zoomOutRequested.connect(self.zoom_out)
        self.bar.zoomModeRequested.connect(self.set_zoom_mode)
        self.bar.zoomValueRequested.connect(lambda z: self.set_zoom(z))
        self.bar.viewModeRequested.connect(self.set_view_mode)
        self.bar.set_view_mode(view_mode)
        session.subscribe(self._on_session_event)
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(0)
        self._refit_timer.timeout.connect(self._refit)

    # ------------------------------------------------------------------ info
    @property
    def title(self) -> str:
        return self.session.display_name + (" *" if self.session.dirty else "")

    @property
    def current_page(self) -> int:
        return self.canvas.current_page

    @property
    def page_count(self) -> int:
        return self.session.page_count

    @property
    def zoom(self) -> float:
        return self.canvas.zoom

    @property
    def view_mode(self) -> ViewMode:
        return self.canvas.view_mode

    # ---------------------------------------------------------------- layout
    def resizeEvent(self, event) -> None:  # noqa: N802
        self.canvas.setGeometry(self.rect())
        self._place_bar()
        if self.zoom_mode != zm.ZoomMode.CUSTOM:
            self._refit_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._fitted_once:
            self._refit()
            self._on_page_changed(self.canvas.current_page)
        self._place_bar()

    def _place_bar(self) -> None:
        self.bar.adjustSize()
        vp = self.canvas.viewport().geometry()
        x = vp.x() + (vp.width() - self.bar.width()) // 2
        y = vp.y() + vp.height() - self.bar.height() - 18
        self.bar.move(max(8, x), max(8, y))
        self.bar.raise_()
        self.toast_widget.bottom_offset = self.height() - y + 10
        if self.toast_widget.isVisible():
            self.toast_widget.reposition()

    def toast(self, text: str, action: Optional[str] = None, callback=None,
              msec: int = 3200) -> None:
        """Show a transient, non-blocking message over the canvas."""
        self.toast_widget.show_message(text, action, callback, msec)

    # ------------------------------------------------------------------ zoom
    def _refit(self) -> None:
        vp = self.canvas.viewport().size()
        if vp.width() < 50 or vp.height() < 50:
            return
        self._fitted_once = True
        z = zm.resolve(self.zoom_mode, self.canvas.zoom, self.session.page_sizes(),
                       vp.width(), vp.height(), self.canvas.view_mode, self.canvas.current_page)
        if abs(z - self.canvas.zoom) > 1e-4:
            self.canvas.set_zoom(z)
        else:
            self._on_zoom_changed(z)
        if self.zoom_mode != zm.ZoomMode.CUSTOM:
            self.canvas.center_horizontally()

    def set_zoom_mode(self, mode: zm.ZoomMode) -> None:
        self.zoom_mode = mode
        if mode == zm.ZoomMode.CUSTOM:
            return
        self._refit()
        self._on_zoom_changed(self.canvas.zoom)

    def set_zoom(self, z: float, anchor: Optional[QPointF] = None) -> None:
        self.zoom_mode = zm.ZoomMode.CUSTOM
        self.canvas.set_zoom(zm.clamp(z), anchor)
        self._on_zoom_changed(self.canvas.zoom)

    def zoom_in(self) -> None:
        self.set_zoom(zm.zoom_in(self.canvas.zoom))

    def zoom_out(self) -> None:
        self.set_zoom(zm.zoom_out(self.canvas.zoom))

    def actual_size(self) -> None:
        self.set_zoom_mode(zm.ZoomMode.ACTUAL)

    def fit_width(self) -> None:
        self.set_zoom_mode(zm.ZoomMode.FIT_WIDTH)

    def fit_page(self) -> None:
        self.set_zoom_mode(zm.ZoomMode.FIT_PAGE)

    def _on_wheel_zoom(self, notches: float, pos: QPointF) -> None:
        self.set_zoom(zm.wheel_zoom(self.canvas.zoom, notches), anchor=pos)

    def _on_zoom_changed(self, z: float) -> None:
        self.bar.set_zoom(z, self.zoom_mode)
        self._place_bar()
        self.zoomChanged.emit(z)

    # ------------------------------------------------------------ navigation
    def go_to_page(self, index: int, y_pt: Optional[float] = None) -> None:
        self.canvas.go_to_page(index, y_pt)
        if self.zoom_mode == zm.ZoomMode.FIT_PAGE and self.canvas.view_mode != ViewMode.CONTINUOUS:
            self._refit()
        self._on_page_changed(self.canvas.current_page)

    def next_page(self) -> None:
        self.go_to_page(next_page_index(self.canvas.view_mode, self.page_count,
                                        self.current_page, +1))

    def prev_page(self) -> None:
        self.go_to_page(next_page_index(self.canvas.view_mode, self.page_count,
                                        self.current_page, -1))

    def first_page(self) -> None:
        self.go_to_page(0)

    def last_page(self) -> None:
        self.go_to_page(self.page_count - 1)

    def reveal(self, index: int, rect) -> None:
        """Make a PDF-space rect on a page visible (search hits, selections)."""
        self.canvas.ensure_visible(index, rect)
        self._on_page_changed(self.canvas.current_page)

    def set_view_mode(self, mode: ViewMode) -> None:
        if mode == self.canvas.view_mode:
            return
        self.canvas.set_view_mode(mode)
        if self.zoom_mode != zm.ZoomMode.CUSTOM:
            self._refit()
        self.bar.set_view_mode(mode)
        self._on_page_changed(self.canvas.current_page)
        self.viewModeChanged.emit(mode)

    def visible_page_range(self):
        """(first, last) page indices of the current view unit (spread aware)."""
        cur = self.current_page
        if self.canvas.view_mode == ViewMode.TWO_PAGE:
            first = (cur // 2) * 2
            return first, min(first + 1, self.page_count - 1)
        return cur, cur

    def _on_page_changed(self, index: int) -> None:
        self.bar.set_page(index, self.page_count)
        self.currentPageChanged.emit(index)

    # ------------------------------------------------------- session events
    def _on_session_event(self, event: SessionEvent) -> None:
        if event.kind == EventKind.STRUCTURE:
            self.clear_selection()
            if self.selected_annot is not None and self.selected_annotation() is None:
                self.clear_annotation_selection()
            self._on_page_changed(self.canvas.current_page)
            if self.zoom_mode != zm.ZoomMode.CUSTOM:
                self._refit_timer.start()
        elif event.kind in (EventKind.DIRTY, EventKind.PATH):
            self.titleChanged.emit()
        elif event.kind == EventKind.HISTORY:
            self.historyChanged.emit()
        elif event.kind == EventKind.ANNOTATIONS:
            if self.selected_annot is not None and self.selected_annotation() is None:
                self.clear_annotation_selection()
            elif self.selected_annot is not None:
                self.annotationSelectionChanged.emit()

    # ---------------------------------------------------------- selection
    def set_text_selection(self, sel: Optional[TextSelection]) -> None:
        if sel is not None and sel.is_empty:
            sel = None
        if sel == self.text_selection:
            return
        self.text_selection = sel
        self.canvas.viewport().update()
        self.selectionChanged.emit()

    def clear_selection(self) -> None:
        self.set_text_selection(None)

    def selected_text(self) -> str:
        return self.text_selection.text if self.text_selection else ""

    def copy_selection(self) -> bool:
        text = self.selected_text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        n = len(self.text_selection.words)
        self.toast(f"Copied {n} word{'s' if n != 1 else ''}", msec=1600)
        return True

    # ------------------------------------------------- annotation selection
    def select_annotation(self, page: int, annot_id: str) -> None:
        key = (self.session.page_id(page), annot_id)
        if key == self.selected_annot:
            return
        self.selected_annot = key
        self.canvas.viewport().update()
        self.annotationSelectionChanged.emit()

    def clear_annotation_selection(self) -> bool:
        if self.selected_annot is None:
            return False
        self.selected_annot = None
        self.canvas.viewport().update()
        self.annotationSelectionChanged.emit()
        return True

    def is_annotation_selected(self, page: int, annot_id: str) -> bool:
        return self.selected_annot == (self.session.page_id(page), annot_id)

    def selected_annotation(self) -> Optional[Tuple[int, AnnotationModel]]:
        if self.selected_annot is None or self.session.doc.closed:
            return None
        pid, aid = self.selected_annot
        index = self.session.index_of(pid)
        if index is None:
            return None
        m = self.session.annotation_by_id(pid, aid)
        return (index, m) if m is not None else None

    def set_hover_annotation(self, key: Optional[Tuple[int, str]]) -> None:
        if key != self.hover_annot:
            self.hover_annot = key
            self.canvas.viewport().update()

    def delete_selected_annotation(self) -> bool:
        sel = self.selected_annotation()
        if sel is None:
            return False
        page, m = sel
        if m.read_only:
            self.toast("This annotation type can't be deleted here")
            return False
        ok = self.execute(DeleteAnnotationCommand(self.session.page_id(page), m.id,
                                                  f"Delete {m.kind.label.lower()}"))
        if ok:
            self.clear_annotation_selection()
        return ok

    def edit_annotation(self, page: int, m: AnnotationModel) -> None:
        """Open the natural editor: note popup, inline text, or comment card."""
        from .annotation_tools import open_free_text_editor, open_note_editor
        self.select_annotation(page, m.id)
        if m.kind == AnnotationKind.FREE_TEXT:
            open_free_text_editor(self, page, m, m.rect, auto_width=False)
        elif m.kind == AnnotationKind.NOTE or m.has_contents or m.kind.is_vertex_based or \
                m.kind in (AnnotationKind.RECTANGLE, AnnotationKind.ELLIPSE):
            open_note_editor(self, page, m, m.rect)
        else:
            self.show_properties()

    def show_properties(self) -> None:
        self.propertiesRequested.emit()

    def search_for_text(self, text: str) -> None:
        """Ask the window to open search prefilled with ``text``."""
        self.searchRequested.emit(text)

    def select_all_text(self) -> None:
        index = self.current_page
        self.set_text_selection(select_all(index, self.session.words(index)))
        if self.text_selection is None:
            self.toast("This page has no selectable text", msec=2000)

    # --------------------------------------------------------------- editing
    def execute(self, command) -> bool:
        """Run a command on this document's undo stack.

        Returns False (and reports the error) when the command fails; the
        history and dirty flag are left unchanged in that case.
        """
        try:
            return self.session.stack.push(command)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.errorOccurred.emit(f"Could not {command.label.lower()}", str(exc))
            return False

    def undo(self) -> bool:
        try:
            return self.session.stack.undo()
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit("Undo failed", str(exc))
            return False

    def redo(self) -> bool:
        try:
            return self.session.stack.redo()
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit("Redo failed", str(exc))
            return False

    # -------------------------------------------------------------- closing
    def close_document(self) -> None:
        self.search.shutdown()
        self.session.unsubscribe(self._on_session_event)
        self.canvas.dispose()
        self.service.cache.invalidate_doc(self.session.uid)
        self.session.close()

    def refresh_icons(self) -> None:
        self.bar.refresh_icons()
        self.canvas.viewport().update()
