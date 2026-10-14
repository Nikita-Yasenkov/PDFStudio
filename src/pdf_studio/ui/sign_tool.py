"""The Sign tool: place a visual signature or a rubber stamp."""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen

from ..core.annotation_commands import AddAnnotationCommand
from ..pdf.errors import EditError
from ..pdf.models import Point, Rect
from ..pdf.stamps import (NAMED_STAMP_RATIO, STAMP_LABELS, default_rect, fit_into,
                          image_size, signature_model, stamp_model)
from . import theme
from .dialogs import prompts
from .signatures import SignatureChoice
from .tools import Tool

MIN_DRAG = 6.0  # pt


class SignatureTool(Tool):
    name = "signature"
    label = "Sign"
    icon = "signature"
    shortcut = "S"
    hint = "Click to place, or drag to size it. Esc cancels. Placed signatures can be moved and resized."
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, controller):
        super().__init__(controller)
        self.choice: Optional[SignatureChoice] = None
        self._preview: Optional[QImage] = None
        self._aspect = NAMED_STAMP_RATIO
        self.hover: Optional[QPointF] = None
        self.drag: Optional[Tuple[int, Point, Point]] = None

    # ----------------------------------------------------------- arming
    def arm(self, choice: SignatureChoice) -> None:
        self.choice = choice
        if choice.image is not None:
            self._preview = QImage.fromData(choice.image)
            w, h = image_size(choice.image)
            self._aspect = w / h
        else:
            self._preview = None
            self._aspect = NAMED_STAMP_RATIO

    def activate(self) -> None:
        super().activate()
        if self.choice is None:
            choice = prompts.ask_signature(self.view, self.ctl.defaults.signature_store)
            if choice is None:
                # nothing chosen: go back to selecting
                self.ctl.set_tool("select")
                return
            self.arm(choice)

    def deactivate(self) -> None:
        self.hover = None
        self.drag = None
        self.choice = None  # next time, ask again (unless armed from the menu)
        self.canvas.viewport().update()

    # ------------------------------------------------------------ mouse
    def mouse_press(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.choice is None:
            return e.button() == Qt.MouseButton.LeftButton
        hit = self.canvas.page_at(e.position())
        if hit is None:
            return True
        self.drag = (hit[0], hit[1], hit[1])
        return True

    def mouse_move(self, e):
        self.hover = QPointF(e.position())
        if self.drag is not None:
            page, a, _b = self.drag
            p = self.canvas.widget_to_pdf(page, e.position())
            w, h = self.session.page_size(page)
            self.drag = (page, a, Point(min(max(p.x, 0.0), w), min(max(p.y, 0.0), h)))
        self.canvas.viewport().update()
        return self.drag is not None

    def mouse_release(self, e):
        if self.drag is None:
            return False
        page, a, b = self.drag
        self.drag = None
        rect = self._target_rect(page, a, b)
        self.place(page, rect)
        return True

    def _target_rect(self, page: int, a: Point, b: Point) -> Rect:
        pw, ph = self.session.page_size(page)
        box = Rect.from_points(a, b)
        if box.width >= MIN_DRAG and box.height >= MIN_DRAG:
            return fit_into(box, self._aspect)
        c = self.choice
        return default_rect(pw, ph, a, image=c.image if c else None,
                            stamp_name=c.stamp_name if c else None)

    def place(self, page: int, rect: Rect) -> bool:
        c = self.choice
        if c is None:
            return False
        author = self.ctl.defaults.author
        try:
            model = (stamp_model(rect, c.stamp_name, author) if c.is_stamp
                     else signature_model(rect, c.image, author))
        except EditError as exc:
            prompts.show_error(self.view, "Can't place this", str(exc))
            return False
        cmd = AddAnnotationCommand(self.session.page_id(page), model,
                                   label="Add stamp" if c.is_stamp else "Add signature")
        if not self.view.execute(cmd):
            return False
        what = "Stamp" if c.is_stamp else "Signature"
        self.ctl.set_tool("select")
        self.view.select_annotation(page, cmd.annot_id)
        self.view.toast(f"{what} placed — drag to move, corners to resize", "Undo", self.view.undo)
        return True

    def cancel(self) -> bool:
        if self.drag is not None:
            self.drag = None
            self.canvas.viewport().update()
            return True
        return False

    # ------------------------------------------------------------ preview
    def paint(self, p: QPainter, canvas, slots) -> None:
        if self.choice is None:
            return
        if self.drag is not None:
            page, a, b = self.drag
            rect = self._target_rect(page, a, b)
        elif self.hover is not None:
            hit = canvas.page_at(self.hover)
            if hit is None:
                return
            page, pt = hit
            pw, ph = self.session.page_size(page)
            c = self.choice
            rect = default_rect(pw, ph, pt, image=c.image, stamp_name=c.stamp_name)
        else:
            return
        wr = canvas.pdf_rect_to_widget(page, rect)
        if wr is None:
            return
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._preview is not None and not self._preview.isNull():
            p.setOpacity(0.75)
            p.drawImage(wr, self._preview)
            p.setOpacity(1.0)
        else:
            color = QColor("#16A34A") if self.choice.stamp_name in ("Approved", "Final") else QColor("#D92D20")
            p.setPen(QPen(color, 2))
            fill = QColor(color)
            fill.setAlpha(25)
            p.setBrush(fill)
            p.drawRoundedRect(wr, 6, 6)
            f = p.font()
            f.setBold(True)
            f.setPixelSize(max(8, int(wr.height() * 0.42)))
            p.setFont(f)
            p.drawText(wr, Qt.AlignmentFlag.AlignCenter,
                       STAMP_LABELS.get(self.choice.stamp_name, self.choice.stamp_name).upper())
        p.setPen(QPen(QColor(t.accent), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(wr)
        p.restore()
