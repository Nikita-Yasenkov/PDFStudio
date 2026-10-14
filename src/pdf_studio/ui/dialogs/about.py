"""About dialog and the keyboard-shortcut cheat sheet."""

from __future__ import annotations

import platform
from typing import Dict, Iterable, List, Sequence, Tuple

from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ... import APP_NAME, __version__
from ...pdf import library_versions
from ..icons import app_logo_pixmap
from ..qtutil import label


def version_info() -> Dict[str, str]:
    libs = library_versions()
    return {
        APP_NAME: __version__,
        "PyQt6": PYQT_VERSION_STR,
        "Qt": QT_VERSION_STR,
        "PyMuPDF": libs.get("PyMuPDF", "?"),
        "MuPDF": libs.get("MuPDF", "?"),
        "Python": platform.python_version(),
    }


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 18)
        lay.setSpacing(8)
        logo = QLabel(self)
        logo.setPixmap(app_logo_pixmap(64))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(logo)
        title = label(APP_NAME, "title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)
        sub = label(f"Version {__version__}", "muted")
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(sub)
        blurb = label("View, annotate, organize, edit and sign PDF documents.", "muted", wrap=True)
        blurb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(blurb)
        lay.addSpacing(10)
        self.versions = version_info()
        grid = QVBoxLayout()
        grid.setSpacing(3)
        for name, ver in self.versions.items():
            if name == APP_NAME:
                continue
            row = QHBoxLayout()
            row.addWidget(label(name, "muted"))
            row.addStretch(1)
            v = QLabel(ver, self)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(v)
            grid.addLayout(row)
        lay.addLayout(grid)
        lay.addSpacing(8)
        note = label("Signatures are visual (image stamps); certificate-based signing is not "
                     "supported. Redaction removes text under the box.", "faint", wrap=True)
        lay.addWidget(note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        lay.addWidget(box)

    def text(self) -> str:
        return "\n".join(f"{k} {v}" for k, v in self.versions.items())


class ShortcutsDialog(QDialog):
    """Searchable table of every action with a shortcut, grouped by menu."""

    def __init__(self, parent: QWidget, rows: Sequence[Tuple[str, str, str]]):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(560, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Keyboard shortcuts", "heading"))
        lay.addWidget(label("Single-letter tool keys work when the page has focus. "
                            "Hold Space to pan with any tool.", "muted", wrap=True))
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter by action or key…")
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Menu", "Action", "Shortcut"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for group, action, keys in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, text in enumerate((group, action, keys)):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(self.palette().placeholderText())
                self.table.setItem(r, c, item)
        lay.addWidget(self.table, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _apply_filter(self, text: str) -> None:
        t = text.lower().strip()
        for r in range(self.table.rowCount()):
            hay = " ".join(self.table.item(r, c).text().lower() for c in range(3))
            self.table.setRowHidden(r, bool(t) and t not in hay)

    def visible_rows(self) -> int:
        return sum(1 for r in range(self.table.rowCount()) if not self.table.isRowHidden(r))


def shortcut_text(seqs: Iterable[QKeySequence]) -> str:
    return "  or  ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in seqs
                         if not s.isEmpty())
