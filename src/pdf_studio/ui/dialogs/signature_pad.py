"""Draw a signature with the mouse/pen and export it as a transparent PNG."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QPushButton,
                             QToolButton, QVBoxLayout, QWidget)

from ...pdf.stamps import trim_transparent

INK_COLORS = (("#1D1D1F", "Black"), ("#1F3B8C", "Blue"), ("#8C1F1F", "Red"))
WIDTHS = ((1.6, "Fine"), (2.6, "Medium"), (4.0, "Bold"))
EXPORT_SCALE = 3.0


def smooth_path(points: List[QPointF]) -> QPainterPath:
    """Quadratic smoothing through stroke midpoints (no jaggies)."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    if len(points) < 3:
        for p in points[1:]:
            path.lineTo(p)
        if len(points) == 1:
            path.lineTo(points[0] + QPointF(0.1, 0.1))
        return path
    for a, b in zip(points[1:-1], points[2:]):
        mid = (a + b) / 2
        path.quadTo(a, mid)
    path.lineTo(points[-1])
    return path


class SignaturePad(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(420, 170)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.strokes: List[List[QPointF]] = []
        self.color = QColor(INK_COLORS[0][0])
        self.width_px = WIDTHS[1][0]
        self._drawing = False

    # ---------------------------------------------------------------- input
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.strokes.append([QPointF(e.position())])
            self._drawing = True
            self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drawing and self.strokes:
            last = self.strokes[-1][-1]
            p = QPointF(e.position())
            if (p - last).manhattanLength() >= 1.0:
                self.strokes[-1].append(p)
                self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drawing:
            self._drawing = False
            self.update()
            self.changed.emit()

    # -------------------------------------------------------------- editing
    def clear(self) -> None:
        self.strokes.clear()
        self.update()
        self.changed.emit()

    def undo_stroke(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self.update()
            self.changed.emit()

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color)
        self.update()

    def set_width(self, width: float) -> None:
        self.width_px = width
        self.update()

    @property
    def is_empty(self) -> bool:
        return not any(self.strokes)

    # -------------------------------------------------------------- drawing
    def _pen(self, scale: float = 1.0) -> QPen:
        pen = QPen(self.color, self.width_px * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#C5CAD3"), 1, Qt.PenStyle.DashLine))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(r, 8, 8)
        base_y = self.height() * 0.72
        p.setPen(QPen(QColor("#DCDFE4"), 1))
        p.drawLine(QPointF(24, base_y), QPointF(self.width() - 24, base_y))
        if self.is_empty:
            p.setPen(QColor("#9AA0AA"))
            p.drawText(QRectF(24, base_y + 4, self.width() - 48, 24),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "✕  Sign here")
        p.setPen(self._pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        for stroke in self.strokes:
            p.drawPath(smooth_path(stroke))
        p.end()

    # --------------------------------------------------------------- export
    def to_image(self, scale: float = EXPORT_SCALE) -> QImage:
        img = QImage(int(self.width() * scale), int(self.height() * scale),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        p.setPen(self._pen())
        for stroke in self.strokes:
            p.drawPath(smooth_path(stroke))
        p.end()
        return img

    def png(self) -> Optional[bytes]:
        """Trimmed transparent PNG of the strokes, or None when empty."""
        if self.is_empty:
            return None
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        self.to_image().save(buf, "PNG")
        buf.close()
        return trim_transparent(bytes(ba), pad=6)


class SignaturePadPanel(QWidget):
    """Pad + ink colour / width choices + clear / undo + "save" option."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.pad = SignaturePad(self)
        self.pad.changed.connect(self._on_changed)
        lay.addWidget(self.pad, 1)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.colors = QButtonGroup(self)
        for i, (hex_, name) in enumerate(INK_COLORS):
            b = QToolButton(self)
            b.setCheckable(True)
            b.setToolTip(f"{name} ink")
            b.setFixedSize(24, 24)
            b.setStyleSheet(f"QToolButton {{ background: {hex_}; border-radius: 12px; border: 2px solid transparent; }}"
                            f" QToolButton:checked {{ border: 2px solid #5B93FF; }}")
            self.colors.addButton(b, i)
            row.addWidget(b)
        self.colors.button(0).setChecked(True)
        self.colors.idClicked.connect(lambda i: self.pad.set_color(QColor(INK_COLORS[i][0])))
        row.addSpacing(10)
        self.widths = QButtonGroup(self)
        for i, (w, name) in enumerate(WIDTHS):
            b = QToolButton(self)
            b.setText(name)
            b.setCheckable(True)
            self.widths.addButton(b, i)
            row.addWidget(b)
        self.widths.button(1).setChecked(True)
        self.widths.idClicked.connect(lambda i: self.pad.set_width(WIDTHS[i][0]))
        row.addStretch(1)
        self.undo_btn = QPushButton("Undo stroke", self)
        self.undo_btn.clicked.connect(self.pad.undo_stroke)
        self.clear_btn = QPushButton("Clear", self)
        self.clear_btn.clicked.connect(self.pad.clear)
        row.addWidget(self.undo_btn)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)
        self.save_box = QCheckBox("Save for next time", self)
        self.save_box.setChecked(True)
        lay.addWidget(self.save_box)
        self._on_changed()

    def _on_changed(self) -> None:
        empty = self.pad.is_empty
        self.undo_btn.setEnabled(not empty)
        self.clear_btn.setEnabled(not empty)
        self.changed.emit()

    def png(self) -> Optional[bytes]:
        return self.pad.png()
