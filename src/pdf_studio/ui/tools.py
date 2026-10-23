"""Interactive tools and the per-view ToolController.

Exactly one tool is active at a time. The canvas forwards mouse events to
the controller, which handles global gestures (middle-button pan, Space for
a temporary hand) and otherwise delegates to the active tool. Tools also
paint their own previews as a canvas overlay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import html
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import QApplication, QMenu, QToolTip

from ..core.annotation_commands import AddAnnotationCommand, UpdateAnnotationCommand
from ..core.annotation_geometry import (apply_handle, clamp_delta, corner_handle_at,
                                        cursor_for_handle, handle_at, hit_test, resize_rect)
from ..core.edit_commands import TransformObjectCommand
from ..core.layout import Slot
from ..core.selection import (TextSelection, is_near_text, select_between,
                              select_line, select_word, word_at)
from ..pdf.models import AnnotationKind, AnnotationModel, PlacedObject, Point, Rect
from .icons import icons
from .tool_defaults import ToolDefaults

if TYPE_CHECKING:  # pragma: no cover
    from .canvas import PageCanvas
    from .document_view import DocumentView


class Tool(QObject):
    name = "tool"
    label = "Tool"
    icon = "select"
    shortcut = ""
    hint = ""
    cursor = Qt.CursorShape.ArrowCursor
    #: switching to this tool keeps the current text selection
    keeps_text_selection = False

    def __init__(self, controller: "ToolController"):
        super().__init__(controller)
        self.ctl = controller

    # convenience
    @property
    def view(self) -> "DocumentView":
        return self.ctl.view

    @property
    def canvas(self) -> "PageCanvas":
        return self.ctl.view.canvas

    @property
    def session(self):
        return self.ctl.view.session

    # lifecycle
    def activate(self) -> None:
        self.canvas.viewport().setCursor(QCursor(self.cursor))

    def deactivate(self) -> None:
        """Called when another tool takes over; aborts gestures by default."""
        self.cancel()

    # events: return True when handled
    def mouse_press(self, e: QMouseEvent) -> bool:
        return False

    def mouse_move(self, e: QMouseEvent) -> bool:
        return False

    def mouse_release(self, e: QMouseEvent) -> bool:
        return False

    def mouse_double_click(self, e: QMouseEvent) -> bool:
        return False

    def key_press(self, e: QKeyEvent) -> bool:
        return False

    def paint(self, painter: QPainter, canvas: "PageCanvas", slots: List[Slot]) -> None:
        pass

    def cancel(self) -> bool:
        """Abort an in-progress gesture (Escape). Return True if one was active."""
        return False

    def context_menu(self, e, menu) -> None:
        """Add tool-specific entries to the canvas context menu."""


class TextSelectGesture:
    """Press-drag text selection on one page (word granular)."""

    def __init__(self, tool: Tool):
        self.tool = tool
        self.page: Optional[int] = None
        self.anchor: Optional[Point] = None
        self.moved = False

    @property
    def active(self) -> bool:
        return self.page is not None

    def begin(self, page: int, p: Point) -> bool:
        words = self.tool.session.words(page)
        if not words or not is_near_text(words, p):
            return False
        self.page, self.anchor, self.moved = page, p, False
        self.tool.view.set_text_selection(None)
        return True

    def update(self, wp: QPointF) -> None:
        if self.page is None:
            return
        canvas = self.tool.canvas
        self._autoscroll(wp)
        p = canvas.widget_to_pdf(self.page, wp)
        if p is None:
            return
        self.moved = True
        words = self.tool.session.words(self.page)
        self.tool.view.set_text_selection(select_between(self.page, words, self.anchor, p))

    def _autoscroll(self, wp: QPointF) -> None:
        vp = self.tool.canvas.viewport()
        bar = self.tool.canvas.verticalScrollBar()
        if wp.y() < 0:
            bar.setValue(bar.value() + int(wp.y() / 2) - 4)
        elif wp.y() > vp.height():
            bar.setValue(bar.value() + int((wp.y() - vp.height()) / 2) + 4)

    def end(self) -> Optional[TextSelection]:
        page = self.page
        self.page = None
        if page is None:
            return None
        return self.tool.view.text_selection

    def cancel(self) -> bool:
        if self.page is None:
            return False
        self.page = None
        self.tool.view.set_text_selection(None)
        return True


class PanGesture:
    """Click-and-drag scrolling shared by the Hand tool and middle-button pan."""

    def __init__(self, canvas: "PageCanvas"):
        self.canvas = canvas
        self.origin: Optional[QPointF] = None
        self.start_scroll = QPoint()

    def begin(self, pos: QPointF) -> None:
        self.origin = QPointF(pos)
        self.start_scroll = QPoint(self.canvas.horizontalScrollBar().value(),
                                   self.canvas.verticalScrollBar().value())
        self.canvas.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def move(self, pos: QPointF) -> None:
        if self.origin is None:
            return
        d = pos - self.origin
        self.canvas.horizontalScrollBar().setValue(int(self.start_scroll.x() - d.x()))
        self.canvas.verticalScrollBar().setValue(int(self.start_scroll.y() - d.y()))

    def end(self) -> None:
        self.origin = None

    @property
    def active(self) -> bool:
        return self.origin is not None


class SelectTool(Tool):
    name = "select"
    label = "Select"
    icon = "select"
    shortcut = "V"
    hint = ("Click annotations or added text and images to select, drag to move. "
            "Double-click to edit. Drag over text to select it. Hold Space to pan.")
    cursor = Qt.CursorShape.ArrowCursor
    keeps_text_selection = True
    DRAG_START = 3.0  # px before a press becomes a drag

    def __init__(self, controller):
        super().__init__(controller)
        self.text = TextSelectGesture(self)
        self._last_double = (0.0, QPointF())
        # annotation gesture state
        self._mode = ""            # "", "pending", "move", "resize", "markup"
        self._press_wp = QPointF()
        self._press_pt: Optional[Point] = None
        self._page = -1
        self._model: Optional[AnnotationModel] = None
        self._object: Optional[PlacedObject] = None  # placed text/image instead of _model
        self._handle = ""
        self.preview: Optional[AnnotationModel] = None

    # ------------------------------------------------------------- helpers
    def _tol(self) -> float:
        return 5.0 / max(0.05, self.canvas.layout_.scale)

    def _annotation_hit(self, wp: QPointF):
        hit = self.canvas.page_at(wp)
        if hit is None:
            return None
        page, p = hit
        m = hit_test(self.session.annotations(page), p, self._tol())
        return (page, p, m) if m is not None else None

    def _selected_handle(self, wp: QPointF):
        sel = self.view.selected_annotation()
        if sel is None:
            return None
        page, m = sel
        p = self.canvas.widget_to_pdf(page, wp)
        if p is None:
            return None
        h = handle_at(m, p, self._tol() * 1.2)
        return (page, m, h) if h else None

    def _object_hit(self, wp: QPointF):
        hit = self.canvas.page_at(wp)
        if hit is None:
            return None
        page, p = hit
        for obj in reversed(self.session.placed_objects(page)):
            if obj.rect.inflated(self._tol() * 0.5).contains(p):
                return page, p, obj
        return None

    def _annotation_or_object_hit(self, wp: QPointF):
        """(annotation hit, object hit), at most one set. Annotations are on
        top, except that placed text under a highlight is what gets dragged."""
        ah, oh = self._annotation_hit(wp), self._object_hit(wp)
        if ah is not None and oh is not None and ah[2].kind.is_text_markup:
            ah = None
        return ah, (oh if ah is None else None)

    def _selected_object_handle(self, wp: QPointF):
        sel = self.view.selected_placed_object()
        if sel is None:
            return None
        page, obj = sel
        p = self.canvas.widget_to_pdf(page, wp)
        if p is None:
            return None
        h = corner_handle_at(obj.rect, p, self._tol() * 1.2)
        return (page, obj, h) if h else None

    def _is_triple_click(self, e) -> bool:
        t, pos = self._last_double
        return (time.monotonic() - t < QApplication.doubleClickInterval() / 1000
                and (pos - e.position()).manhattanLength() < 6)

    # ------------------------------------------------------------- mouse
    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        wp = e.position()
        self._press_wp = QPointF(wp)
        page_hit = self.canvas.page_at(wp)
        if page_hit is not None and self._is_triple_click(e):
            self.view.set_text_selection(
                select_line(page_hit[0], self.session.words(page_hit[0]), page_hit[1]))
            return True
        handle = self._selected_handle(wp)
        if handle is not None:
            self._page, self._model, self._handle = handle
            self._object = None
            self._press_pt = self.canvas.widget_to_pdf(self._page, wp)
            self._mode = "resize"
            return True
        handle = self._selected_object_handle(wp)
        if handle is not None:
            self._page, self._object, self._handle = handle
            self._model = None
            self._press_pt = self.canvas.widget_to_pdf(self._page, wp)
            self._mode = "resize"
            return True
        ah, oh = self._annotation_or_object_hit(wp)
        if ah is not None:
            page, p, m = ah
            self.view.clear_selection()
            self.view.select_annotation(page, m.id)
            self._page, self._model, self._press_pt = page, m, p
            self._object = None
            self._mode = "markup" if m.kind.is_text_markup else ("pending" if m.can_move else "")
            return True
        if oh is not None:
            page, p, obj = oh
            self.view.clear_selection()
            self.view.select_object(page, obj.id)
            self._page, self._object, self._press_pt = page, obj, p
            self._model = None
            self._mode = "pending"
            return True
        self.view.clear_annotation_selection()
        self.view.clear_object_selection()
        if page_hit is not None and self.text.begin(page_hit[0], page_hit[1]):
            self.canvas.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            return True
        self.view.clear_selection()
        return True

    def mouse_move(self, e):
        wp = e.position()
        if self.text.active:
            self.text.update(wp)
            return True
        if self._mode and e.buttons() & Qt.MouseButton.LeftButton:
            dragged = (wp - self._press_wp).manhattanLength() >= self.DRAG_START
            if self._mode == "markup" and dragged:
                # dragging from a highlight selects text instead
                self._mode = ""
                self.view.clear_annotation_selection()
                if self.text.begin(self._page, self._press_pt):
                    self.text.update(wp)
                return True
            if self._mode == "pending" and dragged:
                self._mode = "move"
            if self._mode == "move":
                p = self.canvas.widget_to_pdf(self._page, wp)
                dx, dy = p.x - self._press_pt.x, p.y - self._press_pt.y
                pw, ph = self.session.page_size(self._page)
                if self._object is not None:
                    dx, dy = clamp_delta(self._object.rect, dx, dy, pw, ph)
                    self._set_object_preview(self._object.rect.translated(dx, dy))
                else:
                    dx, dy = clamp_delta(self._model.rect, dx, dy, pw, ph)
                    self._set_preview(self._model.translated(dx, dy))
                self.canvas.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            elif self._mode == "resize":
                p = self.canvas.widget_to_pdf(self._page, wp)
                pw, ph = self.session.page_size(self._page)
                p = Point(min(max(p.x, 0.0), pw), min(max(p.y, 0.0), ph))
                if self._object is not None:
                    self._set_object_preview(resize_rect(self._object.rect, self._handle, p,
                                                         keep_aspect=True))
                else:
                    keep = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self._set_preview(apply_handle(self._model, self._handle, p, keep))
            return True
        if not e.buttons():
            self._update_hover(wp)
        return False

    def mouse_release(self, e):
        if self.text.active:
            self.text.end()
            if not self.text.moved:
                self.view.clear_selection()
            self._update_hover(e.position())
            return True
        mode, preview, model = self._mode, self.preview, self._model
        obj, obj_preview = self._object, self.view.object_preview
        self._mode = ""
        self._set_preview(None)
        self._set_object_preview(None)
        if mode in ("move", "resize") and obj is not None and obj_preview is not None:
            verb = "Move" if mode == "move" else "Resize"
            self.view.execute(TransformObjectCommand(
                self.session.page_id(self._page), obj.id, obj_preview[1],
                f"{verb} {obj.label.lower()}"))
            self._update_hover(e.position())
            return True
        if mode in ("move", "resize") and preview is not None and model is not None:
            label = "Move annotation" if mode == "move" else "Resize annotation"
            self.view.execute(UpdateAnnotationCommand(
                self.session.page_id(self._page), model.id, preview, label))
            self._update_hover(e.position())
            return True
        return bool(mode)

    def mouse_double_click(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        ah = self._annotation_hit(e.position())
        if ah is not None:
            page, _p, m = ah
            self.view.edit_annotation(page, m)
            return True
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return False
        self.view.set_text_selection(select_word(hit[0], self.session.words(hit[0]), hit[1]))
        self._last_double = (time.monotonic(), QPointF(e.position()))
        return True

    def _set_preview(self, m: Optional[AnnotationModel]) -> None:
        self.preview = m
        self.view.annotation_preview = (self._page, m) if m is not None else None
        self.canvas.viewport().update()

    def _set_object_preview(self, r: Optional[Rect]) -> None:
        self.view.object_preview = (self._page, r) if r is not None else None
        self.canvas.viewport().update()

    def _update_hover(self, wp: QPointF) -> None:
        vp = self.canvas.viewport()
        handle = self._selected_handle(wp) or self._selected_object_handle(wp)
        if handle is not None:
            vp.setCursor(_HANDLE_CURSORS.get(cursor_for_handle(handle[2]), Qt.CursorShape.ArrowCursor))
            self.view.set_hover_annotation(None)
            self.view.set_hover_object(None)
            return
        ah, oh = self._annotation_or_object_hit(wp)
        if ah is not None:
            page, _p, m = ah
            self.view.set_hover_object(None)
            self.view.set_hover_annotation((self.session.page_id(page), m.id))
            vp.setCursor(Qt.CursorShape.SizeAllCursor if m.can_move and self.view.is_annotation_selected(page, m.id)
                         else Qt.CursorShape.PointingHandCursor)
            tip = m.contents.strip()
            if tip:
                head = m.kind.label + (f" · {m.author}" if m.author else "")
                QToolTip.showText(self.canvas.viewport().mapToGlobal(wp.toPoint()),
                                  f"<b>{html.escape(head)}</b><br>{html.escape(tip[:300])}",
                                  self.canvas.viewport())
            else:
                QToolTip.hideText()
            return
        QToolTip.hideText()
        self.view.set_hover_annotation(None)
        if oh is not None:
            page, _p, obj = oh
            self.view.set_hover_object((self.session.page_id(page), obj.id))
            vp.setCursor(Qt.CursorShape.SizeAllCursor if self.view.is_object_selected(page, obj.id)
                         else Qt.CursorShape.PointingHandCursor)
            return
        self.view.set_hover_object(None)
        hit = self.canvas.page_at(wp)
        on_text = hit is not None and word_at(self.session.words(hit[0]), hit[1]) is not None
        vp.setCursor(Qt.CursorShape.IBeamCursor if on_text else self.cursor)

    # ---------------------------------------------------------- keyboard
    def key_press(self, e) -> bool:
        sel = self.view.selected_annotation()
        if sel is None:
            return self._object_key_press(e)
        page, m = sel
        key = e.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.view.delete_selected_annotation()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.view.edit_annotation(page, m)
            return True
        if key in _ARROWS and m.can_move:
            step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx, dy = _ARROWS[key]
            pw, ph = self.session.page_size(page)
            dx, dy = clamp_delta(m.rect, dx * step, dy * step, pw, ph)
            self.view.execute(UpdateAnnotationCommand(self.session.page_id(page), m.id,
                                                      m.translated(dx, dy), "Nudge annotation",
                                                      merge_key="nudge"))
            return True
        return False

    def deactivate(self) -> None:
        super().deactivate()
        self.view.set_hover_annotation(None)
        self.view.set_hover_object(None)

    def _object_key_press(self, e) -> bool:
        sel = self.view.selected_placed_object()
        if sel is None:
            return False
        page, obj = sel
        key = e.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.view.delete_selected_object()
            return True
        if key in _ARROWS:
            step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx, dy = _ARROWS[key]
            pw, ph = self.session.page_size(page)
            dx, dy = clamp_delta(obj.rect, dx * step, dy * step, pw, ph)
            self.view.execute(TransformObjectCommand(self.session.page_id(page), obj.id,
                                                     obj.rect.translated(dx, dy),
                                                     f"Nudge {obj.label.lower()}",
                                                     merge_key="nudge"))
            return True
        return False

    def cancel(self) -> bool:
        if self._mode:
            self._mode = ""
            self._set_preview(None)
            self._set_object_preview(None)
            return True
        return self.text.cancel()

    def context_menu(self, e, menu) -> None:
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        ah, oh = self._annotation_or_object_hit(pos)
        if ah is None:
            if oh is not None:
                page, _p, obj = oh
                self.view.select_object(page, obj.id)
                menu.addAction(icons.icon("delete"), f"Delete {obj.label}",
                               self.view.delete_selected_object)
                menu.addSeparator()
            return
        page, _p, m = ah
        self.view.select_annotation(page, m.id)
        if m.has_contents:
            menu.addAction("Edit Comment…" if m.kind.is_text_markup else "Edit…",
                           lambda: self.view.edit_annotation(page, m))
        menu.addAction(icons.icon("delete"), "Delete Annotation",
                       self.view.delete_selected_annotation)
        menu.addAction("Properties…", self.view.show_properties)
        menu.addSeparator()


_ARROWS = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
           Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}

_HANDLE_CURSORS = {
    "fdiag": Qt.CursorShape.SizeFDiagCursor, "bdiag": Qt.CursorShape.SizeBDiagCursor,
    "ver": Qt.CursorShape.SizeVerCursor, "hor": Qt.CursorShape.SizeHorCursor,
    "cross": Qt.CursorShape.CrossCursor,
}


class HandTool(Tool):
    name = "hand"
    label = "Hand"
    icon = "hand"
    shortcut = "H"
    hint = "Drag to scroll the page. Ctrl+wheel zooms."
    cursor = Qt.CursorShape.OpenHandCursor

    def __init__(self, controller):
        super().__init__(controller)
        self._pan: Optional[PanGesture] = None

    def mouse_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pan = PanGesture(self.canvas)
            self._pan.begin(e.position())
            return True
        return False

    def mouse_move(self, e):
        if self._pan and self._pan.active:
            self._pan.move(e.position())
            return True
        return False

    def mouse_release(self, e):
        if self._pan and self._pan.active:
            self._pan.end()
            self.canvas.viewport().setCursor(QCursor(self.cursor))
            return True
        return False


class MarkupTool(Tool):
    """Highlight / underline / strikethrough from a text selection."""

    kind = AnnotationKind.HIGHLIGHT
    cursor = Qt.CursorShape.IBeamCursor
    keeps_text_selection = True
    #: picking the tool while text is selected applies it once
    applies_to_selection = True

    def __init__(self, controller):
        super().__init__(controller)
        self.text = TextSelectGesture(self)

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self.canvas.page_at(e.position())
        if hit is not None and self.text.begin(hit[0], hit[1]):
            return True
        self.view.clear_selection()
        return True

    def mouse_move(self, e):
        if self.text.active:
            self.text.update(e.position())
            return True
        return False

    def mouse_release(self, e):
        if not self.text.active:
            return False
        sel = self.text.end()
        if sel is not None and self.text.moved:
            self.apply(sel)
        self.view.clear_selection()
        return True

    def apply(self, sel: TextSelection) -> bool:
        if sel is None or sel.is_empty:
            return False
        lines = tuple(sel.line_rects)
        bounds = Rect.union_all(lines)
        style = self.ctl.defaults.style(self.name)
        model = AnnotationModel(self.kind, bounds, quads=lines, contents="", **style)
        return self.view.execute(AddAnnotationCommand(
            self.session.page_id(sel.page_index), model, label=f"Add {self.kind.label.lower()}"))

    def cancel(self) -> bool:
        return self.text.cancel()


class HighlightTool(MarkupTool):
    name = "highlight"
    label = "Highlight"
    icon = "highlight"
    shortcut = "M"
    hint = "Drag over text to highlight it."
    kind = AnnotationKind.HIGHLIGHT


class UnderlineTool(MarkupTool):
    name = "underline"
    label = "Underline"
    icon = "underline"
    shortcut = "U"
    hint = "Drag over text to underline it."
    kind = AnnotationKind.UNDERLINE


class StrikeoutTool(MarkupTool):
    name = "strikeout"
    label = "Strikethrough"
    icon = "strikeout"
    shortcut = "K"
    hint = "Drag over text to strike it through."
    kind = AnnotationKind.STRIKEOUT


MARKUP_TOOLS = (HighlightTool, UnderlineTool, StrikeoutTool)


class ToolController(QObject):
    toolChanged = pyqtSignal(str)
    hintChanged = pyqtSignal(str)

    def __init__(self, view: "DocumentView", defaults: Optional[ToolDefaults] = None):
        super().__init__(view)
        self.view = view
        self.defaults = defaults or ToolDefaults()
        self.tools: Dict[str, Tool] = {}
        self._active: Optional[Tool] = None
        self._pan = PanGesture(view.canvas)
        self._space_pan = False
        self._switching = False
        view.canvas.mouse_handler = self
        view.canvas.overlays.append(self)
        view.canvas.installEventFilter(self)
        from .annotation_tools import ANNOTATION_TOOLS  # avoid import cycle
        from .edit_tools import EDIT_TOOLS
        from .sign_tool import SignatureTool
        for cls in (SelectTool, HandTool) + MARKUP_TOOLS + ANNOTATION_TOOLS + EDIT_TOOLS + (SignatureTool,):
            self.register(cls(self))
        self.set_tool("select")

    # ------------------------------------------------------------- registry
    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    @property
    def active(self) -> Tool:
        assert self._active is not None
        return self._active

    @property
    def active_name(self) -> str:
        return self._active.name if self._active else ""

    def set_tool(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool is None:
            return False
        if tool is self._active:
            return True
        sel = self.view.text_selection
        if getattr(tool, "applies_to_selection", False) and sel is not None:
            # one-shot: mark up the current selection, keep the current tool
            tool.apply(sel)
            self.view.clear_selection()
            if self._active is not None:
                self.toolChanged.emit(self._active.name)
            return True
        if self._active is not None:
            self._switching = True
            try:
                self._active.deactivate()
            finally:
                self._switching = False
        if not tool.keeps_text_selection:
            self.view.clear_selection()
        self._active = tool
        tool.activate()
        self.view.canvas.viewport().update()
        self.toolChanged.emit(name)
        self.hintChanged.emit(tool.hint)
        return True

    # --------------------------------------------------------- mouse routing
    def mouse_press(self, e: QMouseEvent) -> bool:
        if e.button() == Qt.MouseButton.MiddleButton or (
                self._space_pan and e.button() == Qt.MouseButton.LeftButton):
            self._pan.begin(e.position())
            return True
        return self.active.mouse_press(e)

    def mouse_move(self, e: QMouseEvent) -> bool:
        if self._pan.active:
            self._pan.move(e.position())
            return True
        return self.active.mouse_move(e)

    def mouse_release(self, e: QMouseEvent) -> bool:
        if self._pan.active:
            self._pan.end()
            cursor = Qt.CursorShape.OpenHandCursor if self._space_pan else self.active.cursor
            self.view.canvas.viewport().setCursor(QCursor(cursor))
            return True
        return self.active.mouse_release(e)

    def mouse_double_click(self, e: QMouseEvent) -> bool:
        return self.active.mouse_double_click(e)

    # ------------------------------------------------------------- painting
    def paint(self, painter: QPainter, canvas, slots) -> None:
        if self._active is not None:
            self._active.paint(painter, canvas, slots)

    # ------------------------------------------------------------- keyboard
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        et = event.type()
        if et == QEvent.Type.KeyPress:
            ke: QKeyEvent = event
            if ke.key() == Qt.Key.Key_Space and not ke.isAutoRepeat() and not self._space_pan:
                self._space_pan = True
                self.view.canvas.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            if ke.key() == Qt.Key.Key_Space:
                return True
            if self.active.key_press(ke):
                return True
            if ke.key() == Qt.Key.Key_Escape:
                self.escape()
                return True
        elif et == QEvent.Type.KeyRelease:
            ke = event
            if ke.key() == Qt.Key.Key_Space and not ke.isAutoRepeat():
                self._space_pan = False
                if not self._pan.active:
                    self.view.canvas.viewport().setCursor(QCursor(self.active.cursor))
                return True
        elif et == QEvent.Type.FocusOut and self._space_pan:
            self._space_pan = False
            self.view.canvas.viewport().setCursor(QCursor(self.active.cursor))
        return False

    def cancel(self) -> None:
        if self._active:
            self._active.cancel()

    def escape(self) -> None:
        """Escape: abort a gesture, else drop the selection, else back to Select."""
        if self._active is not None and self._active.cancel():
            return
        if self.view.text_selection is not None:
            self.view.clear_selection()
            return
        for name in ("clear_annotation_selection", "clear_object_selection"):
            clear = getattr(self.view, name, None)
            if clear is not None and clear():
                return
        if self.active_name not in ("select", ""):
            self.set_tool("select")

    def context_menu(self, e) -> None:
        menu = QMenu(self.view)
        view = self.view
        if isinstance(self.active, SelectTool):
            self.active.context_menu(e, menu)
        sel_text = view.selected_text()
        if sel_text:
            menu.addAction("Copy", view.copy_selection)
            menu.addSeparator()
            for cls in MARKUP_TOOLS:
                tool = self.tools[cls.name]
                menu.addAction(icons.icon(cls.icon), cls.label,
                               lambda t=tool: (t.apply(view.text_selection), view.clear_selection()))
            menu.addSeparator()
            edit, redact = self.tools.get("edit_text"), self.tools.get("redact")
            if edit is not None:
                menu.addAction(icons.icon("edit-text"), "Replace Text…",
                               lambda: (edit.apply(view.text_selection), view.clear_selection()))
            if redact is not None:
                menu.addAction(icons.icon("redact"), "Redact",
                               lambda: (redact.apply(view.text_selection), view.clear_selection()))
        if not isinstance(self.active, SelectTool):
            self.active.context_menu(e, menu)
        if sel_text:
            first = sel_text.split("\n")[0]
            short = first[:30] + ("…" if len(first) > 30 else "")
            menu.addSeparator()
            menu.addAction(f"Search for “{short}”", lambda: view.search_for_text(first))
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Select All Text on Page", view.select_all_text)
        menu.exec(e.globalPos())
