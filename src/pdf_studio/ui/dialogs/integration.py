"""Install PDF Studio for this user and make it the default PDF app."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from ... import APP_NAME
from ...integration import windows as win
from ..icons import icons
from ..qtutil import hline, label


class IntegrationDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} on this computer")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Install and open PDFs with PDF Studio", "heading"))
        lay.addWidget(label("Installs for your user account only (no administrator rights): "
                            "the app goes to your Programs folder, with shortcuts on the Desktop "
                            "and in the Start menu.", "muted", wrap=True))
        lay.addWidget(hline(self))
        self._status_slot = QVBoxLayout()
        lay.addLayout(self._status_slot)
        self.status_box: Optional[QWidget] = None
        lay.addWidget(hline(self))

        buttons = QHBoxLayout()
        self.install_btn = QPushButton("Install for this user", self)
        self.install_btn.setProperty("variant", "primary")
        self.install_btn.clicked.connect(self.do_install)
        self.default_btn = QPushButton("Make default PDF app…", self)
        self.default_btn.clicked.connect(self.do_default)
        self.uninstall_btn = QPushButton("Uninstall", self)
        self.uninstall_btn.setProperty("variant", "danger")
        self.uninstall_btn.clicked.connect(self.do_uninstall)
        for b in (self.install_btn, self.default_btn, self.uninstall_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.message = label("", "muted", wrap=True)
        lay.addWidget(self.message)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.refresh()

    # --------------------------------------------------------------- status
    def _row(self, ok: bool, text: str, detail: str = "") -> None:
        row = QHBoxLayout()
        icon = QLabel(self.status_box)
        icon.setPixmap(icons.pixmap("check" if ok else "dot", 18))
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(text))
        if detail:
            col.addWidget(label(detail, "faint", wrap=True))
        row.addLayout(col, 1)
        self.status_box.layout().addLayout(row)

    def refresh(self) -> None:
        if self.status_box is not None:
            self.status_box.deleteLater()
        self.status_box = QWidget(self)
        QVBoxLayout(self.status_box).setContentsMargins(0, 0, 0, 0)
        self._status_slot.addWidget(self.status_box)
        frozen = win.is_frozen()
        installed = win.is_installed()
        registered = win.IS_WINDOWS and win.is_registered()
        default = win.IS_WINDOWS and win.is_default_pdf_app()
        self._row(installed, "Installed" if installed else "Not installed",
                  win.default_install_dir() if installed else
                  ("Running from " + os.path.dirname(win.current_exe())) if frozen else
                  "Running from source — build the app to install it "
                  "(scripts\\build_pdf_studio.ps1).")
        self._row(registered, "Registered to open PDF files" if registered else
                  "Not registered for PDF files",
                  "PDF Studio appears in “Open with” and in Windows Default apps." if registered else "")
        self._row(default, "Default app for PDF files" if default else "Not the default PDF app",
                  "" if default else "Windows asks you to confirm this once in Settings.")
        self.install_btn.setVisible(frozen and not installed)
        self.default_btn.setEnabled(win.IS_WINDOWS and (frozen or registered) and not default)
        self.uninstall_btn.setVisible(installed)

    # -------------------------------------------------------------- actions
    def do_install(self) -> None:
        source = os.path.dirname(win.current_exe())
        try:
            result = win.install(source)
        except OSError as exc:
            self.message.setText(f"Installation failed: {exc}")
            return
        self.message.setText(f"Installed to {result.install_dir}. A shortcut is on your Desktop. "
                             "PDF Studio will restart from there.")
        # continue in the installed copy
        subprocess.Popen([result.exe], close_fds=True)
        app = self.parent().window() if self.parent() else None
        if app is not None:
            app.close()
        self.accept()

    def do_default(self) -> None:
        if not win.is_registered() and win.is_frozen():
            win.register(win.current_exe())
        win.open_default_apps_settings()
        self.message.setText("In Settings, choose PDF Studio for “.pdf” "
                             "(Windows 11: “Set default”). Then come back here.")

    def do_uninstall(self) -> None:
        from .prompts import confirm
        if not confirm(self, "Uninstall PDF Studio",
                       "Remove PDF Studio, its shortcuts and its file association?",
                       "Uninstall", destructive=True):
            return
        win.uninstall()
        self.message.setText("Uninstalled. The program folder is removed when PDF Studio closes.")
        app = self.parent().window() if self.parent() else None
        if app is not None:
            app.close()
        self.accept()
