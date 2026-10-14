"""Floating page-navigation + zoom pill shown over the canvas."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIntValidator
from PyQt6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QToolButton, QWidget)

from ..core.layout import ViewMode
from ..core.zoom import PRESETS, ZoomMode, format_percent, parse_percent
from .icons import icons
from .qtutil import vline


class PageZoomBar(QWidget):
    prevRequested = pyqtSignal()
    nextRequested = pyqtSignal()
    pageRequested = pyqtSignal(int)          # 0-based
    zoomInRequested = pyqtSignal()
    zoomOutRequested = pyqtSignal()
    zoomModeRequested = pyqtSignal(object)   # ZoomMode
    zoomValueRequested = pyqtSignal(float)
    viewModeRequested = pyqtSignal(object)   # ViewMode

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("FloatingBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        self.prev_btn = self._button("chevron-left", "Previous page (Page Up)", self.prevRequested)
        lay.addWidget(self.prev_btn)
        self.page_edit = QLineEdit(self)
        self.page_edit.setFixedWidth(42)
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setToolTip("Current page — type a number and press Enter (Ctrl+G)")
        self.page_edit.setValidator(QIntValidator(1, 999999, self))
        self.page_edit.returnPressed.connect(self._on_page_entered)
        lay.addWidget(self.page_edit)
        self.count_label = QLabel("/ 0", self)
        self.count_label.setProperty("role", "muted")
        self.count_label.setMinimumWidth(34)
        lay.addWidget(self.count_label)
        self.next_btn = self._button("chevron-right", "Next page (Page Down)", self.nextRequested)
        lay.addWidget(self.next_btn)

        sep = vline(self)
        sep.setFixedHeight(18)
        lay.addSpacing(4)
        lay.addWidget(sep)
        lay.addSpacing(4)

        self.zoom_out_btn = self._button("zoom-out", "Zoom out (Ctrl+−)", self.zoomOutRequested)
        lay.addWidget(self.zoom_out_btn)
        self.zoom_btn = QToolButton(self)
        self.zoom_btn.setObjectName("TextButton")
        self.zoom_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.zoom_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.zoom_btn.setMinimumWidth(62)
        self.zoom_btn.setToolTip("Zoom level")
        self.zoom_btn.setMenu(self._zoom_menu())
        lay.addWidget(self.zoom_btn)
        self.zoom_in_btn = self._button("zoom-in", "Zoom in (Ctrl++)", self.zoomInRequested)
        lay.addWidget(self.zoom_in_btn)
        self.fit_btn = self._button("fit-width", "Fit width (Ctrl+2)",
                                    lambda: self.zoomModeRequested.emit(self._next_fit))
        self._next_fit = ZoomMode.FIT_WIDTH
        lay.addWidget(self.fit_btn)
        self.mode_btn = QToolButton(self)
        self.mode_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mode_btn.setToolTip("Page layout")
        self.mode_btn.setAutoRaise(True)
        mode_menu = QMenu(self)
        for mode, icon_name, shortcut in ((ViewMode.CONTINUOUS, "view-continuous", "Ctrl+4"),
                                          (ViewMode.SINGLE, "view-single", "Ctrl+5"),
                                          (ViewMode.TWO_PAGE, "view-two", "Ctrl+6")):
            a = mode_menu.addAction(icons.icon(icon_name), f"{mode.label}\t{shortcut}",
                                    lambda m=mode: self.viewModeRequested.emit(m))
        self.mode_btn.setMenu(mode_menu)
        lay.addWidget(self.mode_btn)
        self._mode = ViewMode.CONTINUOUS
        self.set_view_mode(ViewMode.CONTINUOUS)
        self._count = 0

    def _button(self, icon: str, tip: str, slot) -> QToolButton:
        b = QToolButton(self)
        b.setIcon(icons.icon(icon))
        b.setProperty("icon_name", icon)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        return b

    def _zoom_menu(self) -> QMenu:
        m = QMenu(self)
        for mode in (ZoomMode.AUTO, ZoomMode.FIT_WIDTH, ZoomMode.FIT_PAGE, ZoomMode.ACTUAL):
            m.addAction(mode.label, lambda md=mode: self.zoomModeRequested.emit(md))
        m.addSeparator()
        for z in PRESETS:
            if z in (0.1, 0.33, 0.67, 0.9, 1.1, 1.75, 5.0, 6.0):
                continue
            m.addAction(format_percent(z), lambda v=z: self.zoomValueRequested.emit(v))
        m.addSeparator()
        m.addAction("Custom…", self._ask_custom)
        return m

    def _ask_custom(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Zoom", "Zoom level (%):", text=self.zoom_btn.text())
        if ok:
            v = parse_percent(text)
            if v is not None:
                self.zoomValueRequested.emit(v)

    def _on_page_entered(self) -> None:
        try:
            n = int(self.page_edit.text())
        except ValueError:
            return
        self.pageRequested.emit(max(1, min(n, self._count)) - 1)
        self.page_edit.clearFocus()

    # ----------------------------------------------------------- updates
    def set_page(self, index: int, count: int) -> None:
        self._count = count
        if not self.page_edit.hasFocus():
            self.page_edit.setText(str(index + 1))
        self.count_label.setText(f"/ {count}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < count - 1)

    def set_zoom(self, zoom: float, mode: ZoomMode) -> None:
        self.zoom_btn.setText(format_percent(zoom))
        # the fit button toggles between fit width and fit page
        if mode == ZoomMode.FIT_WIDTH:
            self._next_fit = ZoomMode.FIT_PAGE
            self.fit_btn.setIcon(icons.icon("fit-page"))
            self.fit_btn.setToolTip("Fit page (Ctrl+0)")
        else:
            self._next_fit = ZoomMode.FIT_WIDTH
            self.fit_btn.setIcon(icons.icon("fit-width"))
            self.fit_btn.setToolTip("Fit width (Ctrl+2)")

    def set_view_mode(self, mode: ViewMode) -> None:
        self._mode = mode
        name = {ViewMode.CONTINUOUS: "view-continuous", ViewMode.SINGLE: "view-single",
                ViewMode.TWO_PAGE: "view-two"}[mode]
        self.mode_btn.setIcon(icons.icon(name))
        self.mode_btn.setProperty("icon_name", name)
        self.mode_btn.setToolTip(f"Page layout: {mode.label}")

    def focus_page_field(self) -> None:
        self.page_edit.setFocus()
        self.page_edit.selectAll()

    def refresh_icons(self) -> None:
        for b in (self.prev_btn, self.next_btn, self.zoom_out_btn, self.zoom_in_btn):
            b.setIcon(icons.icon(b.property("icon_name")))
        self.fit_btn.setIcon(icons.icon("fit-page" if self._next_fit == ZoomMode.FIT_PAGE
                                        else "fit-width"))
        self.set_view_mode(self._mode)
