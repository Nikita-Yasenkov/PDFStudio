"""Left icon rail + switchable side panels (pages, bookmarks, search)."""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QStackedWidget,
                             QToolButton, QVBoxLayout, QWidget)

from .icons import icons


class PanelHeader(QWidget):
    """Title row at the top of a side panel."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 8, 6)
        lay.setSpacing(4)
        self.title = QLabel(title, self)
        self.title.setProperty("role", "heading")
        lay.addWidget(self.title)
        lay.addStretch(1)
        self._lay = lay

    def add_button(self, icon: str, tip: str, slot) -> QToolButton:
        b = QToolButton(self)
        b.setIcon(icons.icon(icon))
        b.setProperty("icon_name", icon)
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        self._lay.addWidget(b)
        return b

    def add_widget(self, w: QWidget) -> None:
        self._lay.addWidget(w)

    def refresh_icons(self) -> None:
        for b in self.findChildren(QToolButton):
            name = b.property("icon_name")
            if name:
                b.setIcon(icons.icon(name))


class Rail(QWidget):
    """Narrow vertical strip of panel toggles."""

    panelRequested = pyqtSignal(str)   # panel key, or "" to collapse

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(48)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 8, 0, 8)
        self._lay.setSpacing(2)
        self._lay.addStretch(1)
        self._buttons: Dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)
        self._current = ""

    def add_panel_button(self, key: str, icon: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("RailButton")
        b.setIcon(icons.icon(icon))
        b.setProperty("icon_name", icon)
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setCheckable(True)
        b.setAutoRaise(True)
        b.clicked.connect(lambda _=False, k=key: self._on_clicked(k))
        self._lay.insertWidget(self._lay.count() - 1, b)
        self._buttons[key] = b
        self._group.addButton(b)
        return b

    def _on_clicked(self, key: str) -> None:
        # clicking the active panel collapses the sidebar
        self.panelRequested.emit("" if key == self._current else key)

    def set_current(self, key: str) -> None:
        self._current = key
        for k, b in self._buttons.items():
            b.setChecked(k == key)

    @property
    def current(self) -> str:
        return self._current

    def refresh_icons(self) -> None:
        for b in self._buttons.values():
            b.setIcon(icons.icon(b.property("icon_name")))


class SidePanelHost(QStackedWidget):
    """Holds the side panels; each panel binds to the active DocumentView."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(170)
        self.panels: Dict[str, QWidget] = {}

    def add_panel(self, key: str, panel: QWidget) -> None:
        self.panels[key] = panel
        self.addWidget(panel)

    def show_panel(self, key: str) -> None:
        panel = self.panels.get(key)
        if panel is not None:
            self.setCurrentWidget(panel)

    def bind(self, view) -> None:
        for p in self.panels.values():
            if hasattr(p, "set_view"):
                p.set_view(view)

    def all_panels(self) -> List[QWidget]:
        return list(self.panels.values())
