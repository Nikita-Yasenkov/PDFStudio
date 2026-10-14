"""Password prompt for encrypted PDFs."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QLineEdit, QVBoxLayout, QWidget)

from ..icons import icons
from ..qtutil import label


class PasswordDialog(QDialog):
    def __init__(self, parent: QWidget, name: str, wrong: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Password required")
        self.setModal(True)
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel(self)
        icon.setPixmap(icons.pixmap("lock", 28))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("This document is protected", "heading"))
        col.addWidget(label(f"Enter the password to open “{name}”.", "muted", wrap=True))
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("Password")
        self._reveal = QAction(icons.icon("eye"), "Show password", self.edit)
        self._reveal.setCheckable(True)
        self.edit.addAction(self._reveal, QLineEdit.ActionPosition.TrailingPosition)
        self._reveal.toggled.connect(
            lambda on: self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on
                                             else QLineEdit.EchoMode.Password))
        lay.addWidget(self.edit)
        self.error = label("Incorrect password. Please try again.", "error")
        self.error.setVisible(wrong)
        lay.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Unlock")
        ok.setProperty("variant", "primary")
        ok.setEnabled(False)
        self.edit.textChanged.connect(lambda t: ok.setEnabled(bool(t)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.edit.setFocus()

    def password(self) -> str:
        return self.edit.text()
