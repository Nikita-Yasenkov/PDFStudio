"""Tools that create annotations by clicking or dragging on a page:
sticky note, text box, freehand ink and shapes (rectangle, ellipse, line,
arrow)."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from ..core import geometry as geo
from ..core.annotation_commands import (AddAnnotationCommand,
                                        DeleteAnnotationCommand,
                                        UpdateAnnotationCommand)
from ..pdf.models import AnnotationKind as K
from ..pdf.models import AnnotationModel, Point, Rect
from . import theme
from .annotation_editors import FreeTextEditor, NotePopup, free_text_size
from .qtutil import qcolor
from .tools import Tool

NOTE_SIZE = 18.0   # pt; icon box used for placement
MIN_DRAG = 4.0     # pt; smaller drags count as clicks


def _page_point(tool: Tool, wp: QPointF, page: int) -> Point:
    """Widget point → PDF point on ``page``, clamped to the page."""
    p = tool.canvas.widget_to_pdf(page, wp)
    w, h = tool.session.page_size(page)
    return Point(min(max(p.x, 0.0), w), min(max(p.y, 0.0), h))


class CreateTool(Tool):
    cursor = Qt.CursorShape.CrossCursor
    #: stay active after creating one annotation
    sticky = True
    style_key = ""

    def style(self) -> dict:
        return self.ctl.defaults.style(self.style_key or self.name)

    def finish(self) -> None:
        if not self.sticky and not self.ctl._switching and self.ctl.active is self:
            self.ctl.set_tool("select")


# ------------------------------------------------------------- sticky note
def open_note_editor(view, page: int, model: Optional[AnnotationModel], rect: Rect,
                     on_done=None) -> NotePopup:
    """Edit an existing note (model) or create a new one at ``rect``."""
    session = view.session
    pid = session.page_id(page)
    author = (model.author if model else "") or view.tools.defaults.author

    def commit(text: str) -> None:
        if model is None:
            if text:
                spec = AnnotationModel(K.NOTE, rect, contents=text,
                                       **view.tools.defaults.style("note"))
                view.execute(AddAnnotationCommand(pid, spec, label="Add note"))
        elif text != model.contents:
            view.execute(UpdateAnnotationCommand(pid, model.id, model.with_changes(contents=text),
                                                 "Edit note"))
        if on_done:
            on_done()

    def cancel() -> None:
        if on_done:
            on_done()

    def delete() -> None:
        if model is not None:
            view.execute(DeleteAnnotationCommand(pid, model.id, "Delete note"))
        if on_done:
            on_done()

    return NotePopup(view, page, rect, model.contents if model else "", author, commit, cancel,
                     on_delete=delete if model is not None else None)


class NoteTool(CreateTool):
    name = "note"
    label = "Sticky Note"
    icon = "note"
    shortcut = "N"
    hint = "Click on the page to place a sticky note and type a comment."
    sticky = False

    def __init__(self, controller):
        super().__init__(controller)
        self.pending: Optional[Tuple[int, Rect]] = None
        self.popup: Optional[NotePopup] = None

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        if self.popup is not None:
            self.popup.commit()
            return True
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        page, p = hit
        w, h = self.session.page_size(page)
        x0 = min(max(0.0, p.x - NOTE_SIZE / 2), w - NOTE_SIZE)
        y0 = min(max(0.0, p.y - NOTE_SIZE / 2), h - NOTE_SIZE)
        rect = Rect(x0, y0, x0 + NOTE_SIZE, y0 + NOTE_SIZE)
        self.pending = (page, rect)
        self.canvas.viewport().update()
        self.popup = open_note_editor(self.view, page, None, rect, on_done=self._done)
        return True

    def _done(self) -> None:
        self.pending = None
        self.popup = None
        self.canvas.viewport().update()
        self.finish()

    def deactivate(self) -> None:
        if self.popup is not None:
            self.popup.commit()  # keep what was typed

    def cancel(self) -> bool:
        if self.popup is not None:
            self.popup.cancel()
            return True
        return False

    def paint(self, p: QPainter, canvas, slots) -> None:
        if self.pending is None:
            return
        page, rect = self.pending
        wr = canvas.pdf_rect_to_widget(page, rect)
        if wr is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#B08900"), 1.2))
        p.setBrush(qcolor(self.style().get("color") or (1, 0.84, 0.04)))
        p.drawRoundedRect(wr, 3, 3)
        p.setPen(QPen(QColor("#6B5300"), 1.2))
        for i in range(3):
            y = wr.top() + wr.height() * (0.3 + 0.2 * i)
            p.drawLine(QPointF(wr.left() + wr.width() * 0.22, y),
                       QPointF(wr.right() - wr.width() * 0.22, y))
        p.restore()


# --------------------------------------------------------------- free text
def open_free_text_editor(view, page: int, model: Optional[AnnotationModel], rect: Rect,
                          auto_width: bool, on_done=None) -> FreeTextEditor:
    session = view.session
    pid = session.page_id(page)
    style = view.tools.defaults.style("free_text")
    font_size = model.font_size if model else style.get("font_size", 12.0)
    color = model.text_color if model else (style.get("text_color") or (0, 0, 0))
    fill = model.fill if model else style.get("fill")

    def commit(text: str, final: Rect) -> None:
        if model is None:
            if text.strip():
                spec = AnnotationModel(K.FREE_TEXT, final, contents=text, **style)
                view.execute(AddAnnotationCommand(pid, spec, label="Add text box"))
        elif not text.strip():
            view.execute(DeleteAnnotationCommand(pid, model.id, "Delete text box"))
        elif text != model.contents or final != model.rect:
            view.execute(UpdateAnnotationCommand(
                pid, model.id, model.with_changes(contents=text, rect=final), "Edit text box"))
        if on_done:
            on_done()

    def cancel() -> None:
        if on_done:
            on_done()

    return FreeTextEditor(view, page, rect, model.contents if model else "", font_size,
                          color, fill, commit, cancel, auto_width=auto_width)


class FreeTextTool(CreateTool):
    name = "free_text"
    label = "Text Box"
    icon = "free-text"
    shortcut = "T"
    hint = "Click to type on the page, or drag to set the text box width."
    sticky = False
    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, controller):
        super().__init__(controller)
        self.drag: Optional[Tuple[int, Point, Point]] = None
        self.editor: Optional[FreeTextEditor] = None

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        if self.editor is not None:
            self.editor.commit()
            return True
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        self.drag = (hit[0], hit[1], hit[1])
        return True

    def mouse_move(self, e):
        if self.drag is None:
            return False
        page, a, _b = self.drag
        self.drag = (page, a, _page_point(self, e.position(), page))
        self.canvas.viewport().update()
        return True

    def mouse_release(self, e):
        if self.drag is None:
            return False
        page, a, b = self.drag
        self.drag = None
        style = self.style()
        fs = style.get("font_size", 12.0)
        r = Rect.from_points(a, b)
        auto = r.width < MIN_DRAG * 3
        if auto:
            w, h = free_text_size("", fs, min_width=60)
            r = Rect(a.x, a.y - fs * 0.6, a.x + w, a.y - fs * 0.6 + h)
        else:
            _w, h = free_text_size("", fs)
            r = Rect(r.x0, r.y0, r.x1, r.y0 + max(r.height, h))
        self.canvas.viewport().update()
        self.editor = open_free_text_editor(self.view, page, None, r, auto, on_done=self._done)
        return True

    def _done(self) -> None:
        self.editor = None
        self.finish()

    def deactivate(self) -> None:
        if self.editor is not None:
            self.editor.commit()  # keep what was typed
        self.drag = None

    def cancel(self) -> bool:
        if self.editor is not None:
            self.editor.cancel()
            return True
        if self.drag is not None:
            self.drag = None
            self.canvas.viewport().update()
            return True
        return False

    def paint(self, p, canvas, slots) -> None:
        if self.drag is None:
            return
        page, a, b = self.drag
        wr = canvas.pdf_rect_to_widget(page, Rect.from_points(a, b))
        if wr is None or wr.width() < 2:
            return
        p.save()
        pen = QPen(QColor(theme.current().accent), 1.2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(wr)
        p.restore()


# -------------------------------------------------------------------- ink
class InkTool(CreateTool):
    name = "ink"
    label = "Draw"
    icon = "ink"
    shortcut = "P"
    hint = "Draw freehand on the page. Each stroke becomes an annotation."
    sticky = True

    def __init__(self, controller):
        super().__init__(controller)
        self.page: Optional[int] = None
        self.points: List[Point] = []

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        self.page = hit[0]
        self.points = [hit[1]]
        return True

    def mouse_move(self, e):
        if self.page is None:
            return False
        p = _page_point(self, e.position(), self.page)
        last = self.points[-1]
        min_step = 0.6 / max(0.1, self.canvas.layout_.scale)
        if abs(p.x - last.x) + abs(p.y - last.y) >= min_step:
            self.points.append(p)
            self.canvas.viewport().update()
        return True

    def mouse_release(self, e):
        if self.page is None:
            return False
        page, pts = self.page, geo.simplify_polyline(self.points, 0.3)
        self.page, self.points = None, []
        self.canvas.viewport().update()
        if len(pts) == 1:
            p = pts[0]
            pts = [p, Point(p.x + 0.5, p.y + 0.5)]  # a dot
        if len(pts) >= 2:
            bounds = Rect.union_all(Rect(q.x, q.y, q.x, q.y) for q in pts)
            model = AnnotationModel(K.INK, bounds, strokes=(tuple(pts),), **self.style())
            self.view.execute(AddAnnotationCommand(self.session.page_id(page), model,
                                                   label="Draw"))
        return True

    def cancel(self) -> bool:
        if self.page is not None:
            self.page, self.points = None, []
            self.canvas.viewport().update()
            return True
        return False

    def paint(self, p, canvas, slots) -> None:
        if self.page is None or len(self.points) < 2:
            return
        style = self.style()
        scale = canvas.layout_.scale
        path = QPainterPath()
        first = canvas.pdf_to_widget(self.page, self.points[0])
        path.moveTo(first)
        for q in self.points[1:]:
            path.lineTo(canvas.pdf_to_widget(self.page, q))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qcolor(style.get("color"), style.get("opacity", 1.0)),
                   max(1.0, style.get("width", 2.0) * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()


# ------------------------------------------------------------------ shapes
class ShapeTool(CreateTool):
    kind = K.RECTANGLE
    sticky = True

    def __init__(self, controller):
        super().__init__(controller)
        self.drag: Optional[Tuple[int, Point, Point]] = None

    def _end(self, page: int, a: Point, wp: QPointF, shift: bool) -> Point:
        b = _page_point(self, wp, page)
        if shift:
            if self.kind in (K.LINE, K.ARROW):
                b = geo.constrain_angle(a, b)
            else:
                b = geo.square_from(a, b)
        return b

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        self.drag = (hit[0], hit[1], hit[1])
        return True

    def mouse_move(self, e):
        if self.drag is None:
            return False
        page, a, _ = self.drag
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.drag = (page, a, self._end(page, a, e.position(), shift))
        self.canvas.viewport().update()
        return True

    def mouse_release(self, e):
        if self.drag is None:
            return False
        page, a, b = self.drag
        self.drag = None
        self.canvas.viewport().update()
        if math.hypot(b.x - a.x, b.y - a.y) < MIN_DRAG:
            return True
        style = self.style()
        if self.kind in (K.LINE, K.ARROW):
            model = AnnotationModel(self.kind, Rect.from_points(a, b), strokes=((a, b),), **style)
        else:
            model = AnnotationModel(self.kind, Rect.from_points(a, b), **style)
        self.view.execute(AddAnnotationCommand(self.session.page_id(page), model,
                                               label=f"Add {self.kind.label.lower()}"))
        self.finish()
        return True

    def cancel(self) -> bool:
        if self.drag is not None:
            self.drag = None
            self.canvas.viewport().update()
            return True
        return False

    def paint(self, p, canvas, slots) -> None:
        if self.drag is None:
            return
        page, a, b = self.drag
        style = self.style()
        scale = canvas.layout_.scale
        pa, pb = canvas.pdf_to_widget(page, a), canvas.pdf_to_widget(page, b)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qcolor(style.get("color"), style.get("opacity", 1.0)),
                   max(1.0, style.get("width", 2.0) * scale))
        p.setPen(pen)
        fill = style.get("fill")
        p.setBrush(qcolor(fill, style.get("opacity", 1.0)) if fill else Qt.BrushStyle.NoBrush)
        if self.kind == K.RECTANGLE:
            p.drawRect(QRectF(pa, pb).normalized())
        elif self.kind == K.ELLIPSE:
            p.drawEllipse(QRectF(pa, pb).normalized())
        else:
            p.drawLine(pa, pb)
            if self.kind == K.ARROW:
                ang = math.atan2(pb.y() - pa.y(), pb.x() - pa.x())
                size = max(8.0, style.get("width", 2.0) * scale * 4)
                for d in (math.radians(150), math.radians(-150)):
                    q = QPointF(pb.x() + size * math.cos(ang + d), pb.y() + size * math.sin(ang + d))
                    p.drawLine(pb, q)
        p.restore()


class RectangleTool(ShapeTool):
    name = "rectangle"
    label = "Rectangle"
    icon = "rectangle"
    shortcut = "R"
    hint = "Drag to draw a rectangle. Hold Shift for a square."
    kind = K.RECTANGLE


class EllipseTool(ShapeTool):
    name = "ellipse"
    label = "Ellipse"
    icon = "ellipse"
    shortcut = "O"
    hint = "Drag to draw an ellipse. Hold Shift for a circle."
    kind = K.ELLIPSE


class LineTool(ShapeTool):
    name = "line"
    label = "Line"
    icon = "line"
    shortcut = "L"
    hint = "Drag to draw a line. Hold Shift to snap to 45°."
    kind = K.LINE


class ArrowTool(ShapeTool):
    name = "arrow"
    label = "Arrow"
    icon = "arrow"
    shortcut = "A"
    hint = "Drag to draw an arrow. Hold Shift to snap to 45°."
    kind = K.ARROW


ANNOTATION_TOOLS = (NoteTool, FreeTextTool, InkTool, RectangleTool, EllipseTool,
                    LineTool, ArrowTool)
SHAPE_TOOLS = (RectangleTool, EllipseTool, LineTool, ArrowTool)
