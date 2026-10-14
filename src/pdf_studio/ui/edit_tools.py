"""Content-editing tools: Edit Text (redact & replace) and Redact.

Both work on a text selection (drag over words) or on a free rectangle
(drag anywhere else). Picking the tool while text is selected applies it
to that selection once.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from ..core.edit_commands import PlaceImageCommand, RedactCommand, ReplaceTextCommand
from ..core.selection import TextSelection
from ..pdf.models import Point, Rect
from . import theme
from .dialogs import prompts
from .tools import TextSelectGesture, Tool

MIN_AREA = 4.0  # pt


class AreaGesture:
    """Drag a rectangle on one page (PDF coordinates, clamped to the page)."""

    def __init__(self, tool: Tool):
        self.tool = tool
        self.page: Optional[int] = None
        self.a: Optional[Point] = None
        self.b: Optional[Point] = None

    @property
    def active(self) -> bool:
        return self.page is not None

    def begin(self, page: int, p: Point) -> None:
        self.page, self.a, self.b = page, p, p

    def update(self, wp: QPointF) -> None:
        if self.page is None:
            return
        p = self.tool.canvas.widget_to_pdf(self.page, wp)
        w, h = self.tool.session.page_size(self.page)
        self.b = Point(min(max(p.x, 0.0), w), min(max(p.y, 0.0), h))
        self.tool.canvas.viewport().update()

    def rect(self) -> Optional[Rect]:
        if self.page is None:
            return None
        return Rect.from_points(self.a, self.b)

    def end(self) -> Optional[Tuple[int, Rect]]:
        page, r = self.page, self.rect()
        self.page = None
        self.tool.canvas.viewport().update()
        if page is None or r is None or r.width < MIN_AREA or r.height < MIN_AREA:
            return None
        return page, r

    def cancel(self) -> bool:
        if self.page is None:
            return False
        self.page = None
        self.tool.canvas.viewport().update()
        return True

    def paint(self, p: QPainter, canvas, fill: QColor, line: QColor) -> None:
        r = self.rect()
        if r is None:
            return
        wr = canvas.pdf_rect_to_widget(self.page, r)
        if wr is None:
            return
        p.save()
        p.setBrush(fill)
        p.setPen(QPen(line, 1.2, Qt.PenStyle.DashLine))
        p.drawRect(wr)
        p.restore()


class ContentTool(Tool):
    cursor = Qt.CursorShape.CrossCursor
    keeps_text_selection = True
    applies_to_selection = True

    def __init__(self, controller):
        super().__init__(controller)
        self.text = TextSelectGesture(self)
        self.area = AreaGesture(self)

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        if self.text.begin(hit[0], hit[1]):
            return True
        self.view.clear_selection()
        self.area.begin(hit[0], hit[1])
        return True

    def mouse_move(self, e):
        if self.text.active:
            self.text.update(e.position())
            return True
        if self.area.active:
            self.area.update(e.position())
            return True
        return False

    def mouse_release(self, e):
        if self.text.active:
            sel = self.text.end()
            moved = self.text.moved
            if sel is not None and moved:
                self.apply(sel)
            self.view.clear_selection()
            return True
        if self.area.active:
            res = self.area.end()
            if res is not None:
                self.apply_rect(*res)
            return True
        return False

    def cancel(self) -> bool:
        return self.text.cancel() or self.area.cancel()

    # subclasses
    def apply(self, sel: TextSelection) -> bool:
        raise NotImplementedError

    def apply_rect(self, page: int, rect: Rect) -> bool:
        raise NotImplementedError


class EditTextTool(ContentTool):
    name = "edit_text"
    label = "Edit Text"
    icon = "edit-text"
    shortcut = "E"
    hint = ("Drag over text to replace it (the original is removed). Drag a box elsewhere to "
            "add or erase text in that area.")

    def apply(self, sel: TextSelection) -> bool:
        if sel is None or sel.is_empty:
            return False
        bounds = sel.bounds.inflated(0.5)
        return self.apply_rect(sel.page_index, bounds, sel.text)

    def apply_rect(self, page: int, rect: Rect, original: Optional[str] = None) -> bool:
        style = self.session.probe_text_style(page, rect)
        original = style.text if original is None else original
        result = prompts.ask_replace_text(self.view, original, style)
        if result is None:
            return False
        text, new_style, align = result
        cmd = ReplaceTextCommand(self.session.page_id(page), rect, text, new_style,
                                 fill=style.background, align=align,
                                 label="Replace text" if text.strip() else "Erase text")
        ok = self.view.execute(cmd)
        if ok and cmd.shrunk:
            self.view.toast(f"Text reduced to {cmd.font_size_used:g} pt to fit the box",
                            "Undo", self.view.undo)
        return ok

    def paint(self, p, canvas, slots) -> None:
        t = theme.current()
        fill = QColor(t.accent)
        fill.setAlpha(28)
        self.area.paint(p, canvas, fill, QColor(t.accent))


class RedactTool(ContentTool):
    name = "redact"
    label = "Redact"
    icon = "redact"
    shortcut = "X"
    hint = ("Drag over text or draw a box to black it out. The text underneath is removed; "
            "it's permanent once you save.")

    def apply(self, sel: TextSelection) -> bool:
        if sel is None or sel.is_empty:
            return False
        rects = [r.inflated(0.6) for r in sel.line_rects]
        return self._redact(sel.page_index, rects)

    def apply_rect(self, page: int, rect: Rect) -> bool:
        return self._redact(page, [rect])

    def _redact(self, page: int, rects) -> bool:
        n = len(rects)
        ok = self.view.execute(RedactCommand(self.session.page_id(page), rects,
                                             label=f"Redact {'area' if n == 1 else f'{n} lines'}"))
        if ok:
            self.view.toast("Redacted — the text is removed permanently when you save",
                            "Undo", self.view.undo, msec=4500)
        return ok

    def paint(self, p, canvas, slots) -> None:
        self.area.paint(p, canvas, QColor(0, 0, 0, 120), QColor("#D92D20"))


EDIT_TOOLS = (EditTextTool, RedactTool)


class ImageTool(ContentTool):
    """Replace an image (click it) or place one in a dragged box."""

    name = "image"
    label = "Image"
    icon = "image"
    shortcut = "G"
    hint = ("Click an image on the page to replace it, or drag a box to place a new image. "
            "What's under the box is covered.")
    applies_to_selection = False
    keeps_text_selection = False

    def __init__(self, controller):
        super().__init__(controller)
        self.hover: Optional[Tuple[int, Rect]] = None

    def _image_at(self, wp: QPointF) -> Optional[Tuple[int, Rect]]:
        hit = self.canvas.page_at(wp)
        if hit is None:
            return None
        page, p = hit
        for info in reversed(self.session.image_rects(page)):
            if info.rect.contains(p):
                return page, info.rect
        return None

    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        self.area.begin(hit[0], hit[1])
        return True

    def mouse_move(self, e):
        if self.area.active:
            self.area.update(e.position())
            return True
        if not e.buttons():
            hover = self._image_at(e.position())
            if hover != self.hover:
                self.hover = hover
                self.canvas.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hover
                                                 else self.cursor)
                self.canvas.viewport().update()
        return False

    def mouse_release(self, e):
        if not self.area.active:
            return False
        res = self.area.end()
        if res is None:  # a click: replace the image under the cursor, if any
            target = self._image_at(e.position())
            if target is not None:
                self.apply_rect(*target)
            return True
        self.apply_rect(*res)
        return True

    def apply_rect(self, page: int, rect: Rect) -> bool:
        from ..pdf.editing import load_image_bytes
        from ..pdf.errors import EditError
        path = prompts.ask_open_image(self.view, "", "Choose an image")
        if not path:
            return False
        try:
            data = load_image_bytes(path)
        except EditError as exc:
            prompts.show_error(self.view, "Can't use this image", str(exc))
            return False
        ok = self.view.execute(PlaceImageCommand(self.session.page_id(page), rect, data))
        if ok:
            self.hover = None
            self.view.toast("Image placed", "Undo", self.view.undo)
        return ok

    def apply(self, sel) -> bool:
        return False

    def deactivate(self) -> None:
        super().deactivate()
        self.hover = None

    def paint(self, p, canvas, slots) -> None:
        t = theme.current()
        if self.hover is not None and not self.area.active:
            page, r = self.hover
            wr = canvas.pdf_rect_to_widget(page, r)
            if wr is not None:
                p.save()
                fill = QColor(t.accent)
                fill.setAlpha(36)
                p.setBrush(fill)
                p.setPen(QPen(QColor(t.accent), 2))
                p.drawRect(wr)
                p.restore()
        fill = QColor(t.accent)
        fill.setAlpha(28)
        self.area.paint(p, canvas, fill, QColor(t.accent))


EDIT_TOOLS = (EditTextTool, RedactTool, ImageTool)
