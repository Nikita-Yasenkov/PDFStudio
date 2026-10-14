"""Welcome screen shown when no document is open."""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

from .icons import app_logo_pixmap, icons
from .qtutil import hline, label


class _RecentRow(QPushButton):
    """Flat button holding a two-line layout.

    The stylesheet's QPushButton min-height is applied on every polish and
    would squeeze the labels, so the height they need is enforced afterwards.
    """

    MIN_H = 44

    def event(self, e: QEvent) -> bool:
        handled = super().event(e)
        if e.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange,
                        QEvent.Type.FontChange) and self.layout() is not None:
            self.setMinimumHeight(max(self.MIN_H, self.layout().minimumSize().height()))
        return handled


class EmptyState(QWidget):
    openRequested = pyqtSignal()
    openPathRequested = pyqtSignal(str)
    clearRecentRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)

        card = QFrame(self)
        card.setObjectName("Card")
        card.setMaximumWidth(440)
        card.setMinimumWidth(260)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(32, 30, 32, 26)
        lay.setSpacing(6)

        logo = QLabel(card)
        logo.setPixmap(app_logo_pixmap(56))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(logo)
        lay.addSpacing(8)
        title = label("PDF Studio", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)
        sub = label("View, annotate, organize and edit PDF documents.", "muted", wrap=True)
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(sub)
        lay.addSpacing(16)

        self.open_button = QPushButton("  Open a PDF…")
        self.open_button.setProperty("variant", "primary")
        self.open_button.setIcon(icons.icon("open", color=QColor("#FFFFFF")))
        self.open_button.setMinimumHeight(38)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.openRequested)
        lay.addWidget(self.open_button)
        hint = label("or drag and drop a file anywhere in this window  ·  Ctrl+O", "faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(2)
        lay.addWidget(hint)

        # Recent files
        self._recent_box = QWidget(card)
        rlay = QVBoxLayout(self._recent_box)
        rlay.setContentsMargins(0, 14, 0, 0)
        rlay.setSpacing(2)
        rlay.addWidget(hline())
        head = QHBoxLayout()
        head.setContentsMargins(0, 10, 0, 4)
        head.addWidget(label("RECENT", "section"))
        head.addStretch(1)
        clear = QPushButton("Clear")
        clear.setProperty("variant", "link")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clearRecentRequested)
        head.addWidget(clear)
        rlay.addLayout(head)
        self._recent_list = QVBoxLayout()
        self._recent_list.setSpacing(0)
        rlay.addLayout(self._recent_list)
        lay.addWidget(self._recent_box)
        self._recent_box.hide()

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 100)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

    def set_recent_files(self, paths: List[str]) -> None:
        while self._recent_list.count():
            item = self._recent_list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        shown = [p for p in paths if p][:6]
        for path in shown:
            self._recent_list.addWidget(self._recent_row(path))
        self._recent_box.setVisible(bool(shown))

    def _recent_row(self, path: str) -> QPushButton:
        """Two-line row: file name, then its folder (muted)."""
        btn = _RecentRow()
        btn.setProperty("variant", "flat")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(btn)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(10)
        icon = QLabel(btn)
        icon.setPixmap(icons.pixmap("file-text", 20))
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        exists = os.path.exists(path)
        name = label(os.path.basename(path) + ("" if exists else "  (missing)"))
        folder = os.path.dirname(path)
        if len(folder) > 52:
            folder = "…" + folder[-51:]
        where = label(folder, "faint")
        for w in (name, where):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            col.addWidget(w)
        row.addLayout(col, 1)
        btn.setToolTip(path if exists else f"{path}\n(file not found)")
        btn.setEnabled(exists)
        btn.clicked.connect(lambda _=False, p=path: self.openPathRequested.emit(p))
        return btn

    def refresh_icons(self) -> None:
        from . import theme
        self.open_button.setIcon(icons.icon("open", color=QColor(theme.current().accent_text)))
