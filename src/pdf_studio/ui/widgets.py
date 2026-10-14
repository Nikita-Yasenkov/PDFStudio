"""Small reusable widgets: toast notifications, colour swatches."""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt,
                          QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QColorDialog, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QPushButton, QToolButton, QWidget)


class Toast(QWidget):
    """Transient message shown at the bottom of a parent widget.

    Optionally carries one action button (e.g. "Undo" or "Open").
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        self.label = QLabel(self)
        lay.addWidget(self.label)
        self.button = QPushButton(self)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.hide()
        lay.addWidget(self.button)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.fade_out)
        self._callback: Optional[Callable[[], None]] = None
        self.button.clicked.connect(self._on_button)
        self.bottom_offset = 72
        self.hide()

    def show_message(self, text: str, action: Optional[str] = None,
                     callback: Optional[Callable[[], None]] = None, msec: int = 3200) -> None:
        self.label.setText(text)
        self._callback = callback
        if action and callback:
            self.button.setText(action)
            self.button.show()
        else:
            self.button.hide()
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.start()
        self._timer.start(msec)

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        x = (p.width() - self.width()) // 2
        y = p.height() - self.height() - self.bottom_offset
        self.move(max(8, x), max(8, y))

    def fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def _on_button(self) -> None:
        cb = self._callback
        self.fade_out()
        if cb is not None:
            cb()

    @property
    def text(self) -> str:
        return self.label.text() if self.isVisible() else ""


PRESET_COLORS: Sequence[str] = (
    "#FFD60A",  # yellow
    "#34C759",  # green
    "#32ADE6",  # cyan
    "#007AFF",  # blue
    "#AF52DE",  # violet
    "#FF2D55",  # pink
    "#FF3B30",  # red
    "#FF9500",  # orange
    "#1D1D1F",  # black
)


class ColorSwatch(QToolButton):
    """Round colour chip; checkable within a row."""

    def __init__(self, color: QColor, parent: Optional[QWidget] = None, size: int = 22):
        super().__init__(parent)
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.color.name().upper())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked() or self.underMouse():
            from . import theme
            ring = QColor(theme.current().accent if self.isChecked() else theme.current().border_strong)
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        from . import theme
        outline = QColor(255, 255, 255, 70) if theme.current().is_dark else QColor(0, 0, 0, 50)
        p.setPen(QPen(outline, 1))
        p.setBrush(self.color)
        p.drawEllipse(r)
        p.end()


class ColorRow(QWidget):
    """A row of preset swatches plus a "custom…" button."""

    colorChosen = pyqtSignal(QColor)

    def __init__(self, colors: Sequence[str] = PRESET_COLORS, parent: Optional[QWidget] = None,
                 allow_none: bool = False):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._swatches: List[ColorSwatch] = []
        self._none: Optional[QToolButton] = None
        if allow_none:
            none = QToolButton(self)
            none.setText("∅")
            none.setToolTip("No color")
            none.setCheckable(True)
            none.setFixedSize(22, 22)
            none.clicked.connect(lambda: self._choose(None))
            lay.addWidget(none)
            self._none = none
        for c in colors:
            sw = ColorSwatch(QColor(c), self)
            sw.clicked.connect(lambda _=False, s=sw: self._choose(s.color))
            lay.addWidget(sw)
            self._swatches.append(sw)
        self.more = QToolButton(self)
        self.more.setText("+")
        self.more.setToolTip("Custom color…")
        self.more.setFixedSize(22, 22)
        self.more.clicked.connect(self._custom)
        lay.addWidget(self.more)
        lay.addStretch(1)
        self._current: Optional[QColor] = None

    def _custom(self) -> None:
        c = QColorDialog.getColor(self._current or QColor("#FFD60A"), self, "Choose color")
        if c.isValid():
            self._choose(c)

    def _choose(self, c: Optional[QColor]) -> None:
        self.set_color(c)
        self.colorChosen.emit(QColor(c) if c is not None else QColor())

    def set_color(self, c: Optional[QColor]) -> None:
        self._current = QColor(c) if c is not None and c.isValid() else None
        for sw in self._swatches:
            sw.setChecked(self._current is not None and sw.color.name() == self._current.name())
        if self._none is not None:
            self._none.setChecked(self._current is None)
