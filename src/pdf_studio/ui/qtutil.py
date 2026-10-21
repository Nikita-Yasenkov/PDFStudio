"""Small Qt helpers shared across the UI layer."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

from ..pdf.models import Color, Point, Rect, RenderedImage


def to_qimage(img: RenderedImage) -> QImage:
    """Wrap engine RGB888 pixels in a QImage that owns its buffer."""
    q = QImage(img.data, img.width, img.height, img.stride, QImage.Format.Format_RGB888)
    return q.copy()  # detach from the Python bytes object


def qrectf(r: Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


def rect_from_q(r: QRectF) -> Rect:
    return Rect(r.left(), r.top(), r.right(), r.bottom())


def qpointf(p: Point) -> QPointF:
    return QPointF(p.x, p.y)


def point_from_q(p: QPointF) -> Point:
    return Point(p.x(), p.y())


def qcolor(c: Optional[Color], alpha: float = 1.0) -> QColor:
    if c is None:
        return QColor(0, 0, 0, 0)
    q = QColor.fromRgbF(float(c[0]), float(c[1]), float(c[2]))
    q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def color_from_q(q: QColor) -> Color:
    return (round(q.redF(), 4), round(q.greenF(), 4), round(q.blueF(), 4))


def label(text: str = "", role: Optional[str] = None, wrap: bool = False,
          parent: Optional[QWidget] = None) -> QLabel:
    lbl = QLabel(text, parent)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def hline(parent: Optional[QWidget] = None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("Separator")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def vline(parent: Optional[QWidget] = None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("VSeparator")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def reveal_in_folder(path: Optional[str]) -> None:
    """Show the file selected in Explorer or Finder."""
    if not path:
        return
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":  # pragma: no cover
        subprocess.Popen(["open", "-R", path])
    else:  # pragma: no cover
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def human_size(points: Tuple[float, float]) -> str:
    """Format a page size in points as mm, naming common paper sizes."""
    w_mm = points[0] * 25.4 / 72
    h_mm = points[1] * 25.4 / 72
    names = {(210, 297): "A4", (148, 210): "A5", (297, 420): "A3",
             (216, 279): "Letter", (216, 356): "Legal"}
    a, b = sorted((round(w_mm), round(h_mm)))
    name = ""
    for (nw, nh), n in names.items():
        if abs(a - nw) <= 1 and abs(b - nh) <= 1:
            name = n
            break
    dims = f"{w_mm:.0f} × {h_mm:.0f} mm"
    return f"{name} · {dims}" if name else dims


ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
