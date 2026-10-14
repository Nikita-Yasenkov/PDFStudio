"""Choose a signature or stamp to place: saved signatures, import an image,
built-in stamps (and draw — see ``SignaturePad``)."""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ...pdf.errors import EditError
from ...pdf.stamps import STAMP_LABELS, remove_white_background, trim_transparent
from ..qtutil import label
from ..signatures import SignatureChoice, SignatureStore

PREVIEW = QSize(220, 80)


def png_icon(png: bytes, size: QSize = PREVIEW) -> QIcon:
    img = QImage.fromData(png)
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    if not img.isNull():
        scaled = img.scaled(size - QSize(12, 12), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        p = QPainter(pm)
        p.drawImage((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2,
                    scaled)
        p.end()
    icon = QIcon(pm)
    icon.addPixmap(pm, QIcon.Mode.Selected)  # no selection tint on the ink
    return icon


class SignatureDialog(QDialog):
    TAB_SAVED, TAB_DRAW, TAB_IMPORT, TAB_STAMPS = range(4)

    def __init__(self, parent: QWidget, store: SignatureStore, start_tab: Optional[int] = None):
        super().__init__(parent)
        self.setWindowTitle("Sign or Stamp")
        self.resize(620, 470)
        self.store = store
        self.choice: Optional[SignatureChoice] = None
        self._import_png: Optional[bytes] = None
        self._import_raw: Optional[bytes] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Add a signature or stamp", "heading"))
        lay.addWidget(label("Visual signature only: it is placed as an image annotation, "
                            "not a certificate-based digital signature.", "muted", wrap=True))
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._saved_tab(), "Saved")
        self.tabs.addTab(self._draw_tab(), "Draw")
        self.tabs.addTab(self._import_tab(), "Image")
        self.tabs.addTab(self._stamps_tab(), "Stamps")
        lay.addWidget(self.tabs, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Place on page")
        self.ok.setProperty("variant", "primary")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.tabs.currentChanged.connect(lambda *_: self._update_ok())
        if start_tab is None:
            start_tab = self.TAB_SAVED if self.saved.count() else self.TAB_DRAW
        self.tabs.setCurrentIndex(start_tab)
        self._update_ok()

    # ------------------------------------------------------------------ tabs
    def _saved_tab(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        self.saved = QListWidget(w)
        self.saved.setViewMode(QListWidget.ViewMode.IconMode)
        self.saved.setIconSize(PREVIEW)
        self.saved.setGridSize(PREVIEW + QSize(20, 16))
        self.saved.setMovement(QListWidget.Movement.Static)
        self.saved.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.saved.itemSelectionChanged.connect(self._update_ok)
        self.saved.itemDoubleClicked.connect(lambda *_: self._accept())
        lay.addWidget(self.saved, 1)
        row = QHBoxLayout()
        self.saved_empty = label("No saved signatures yet — draw one or import an image.", "muted")
        row.addWidget(self.saved_empty, 1)
        self.remove_btn = QPushButton("Remove", w)
        self.remove_btn.setProperty("variant", "danger")
        self.remove_btn.clicked.connect(self._remove_saved)
        row.addWidget(self.remove_btn)
        lay.addLayout(row)
        self._reload_saved()
        return w

    def _reload_saved(self) -> None:
        self.saved.clear()
        for path in self.store.list():
            data = self.store.read(path)
            if not data:
                continue
            item = QListWidgetItem(png_icon(data), "")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(os.path.basename(path))
            self.saved.addItem(item)
        has = self.saved.count() > 0
        self.saved_empty.setVisible(not has)
        self.remove_btn.setEnabled(has)
        if has:
            self.saved.setCurrentRow(0)

    def _remove_saved(self) -> None:
        item = self.saved.currentItem()
        if item is not None:
            self.store.remove(item.data(Qt.ItemDataRole.UserRole))
            self._reload_saved()

    def _draw_tab(self) -> QWidget:
        from .signature_pad import SignaturePadPanel
        self.pad_panel = SignaturePadPanel(self)
        self.pad_panel.changed.connect(self._update_ok)
        return self.pad_panel

    def _import_tab(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        row = QHBoxLayout()
        choose = QPushButton("Choose image…", w)
        choose.clicked.connect(self._choose_image)
        row.addWidget(choose)
        self.import_name = label("PNG with transparency works best.", "muted")
        row.addWidget(self.import_name, 1)
        lay.addLayout(row)
        self.import_preview = QLabel(w)
        self.import_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.import_preview.setMinimumHeight(170)
        self.import_preview.setStyleSheet("background: #FFFFFF; border: 1px dashed #C5CAD3;"
                                          " border-radius: 8px;")
        lay.addWidget(self.import_preview, 1)
        self.remove_bg = QCheckBox("Remove white background", w)
        self.remove_bg.setChecked(True)
        self.remove_bg.toggled.connect(lambda *_: self._process_import())
        lay.addWidget(self.remove_bg)
        self.save_import = QCheckBox("Save for next time", w)
        self.save_import.setChecked(True)
        lay.addWidget(self.save_import)
        return w

    def _choose_image(self) -> None:
        from .prompts import IMAGE_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Choose signature image", "", IMAGE_FILTER)
        if path:
            self.load_image_file(path)

    def load_image_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as f:
                self._import_raw = f.read()
        except OSError:
            self._import_raw = None
            return False
        self.import_name.setText(os.path.basename(path))
        self._process_import()
        return self._import_png is not None

    def _process_import(self) -> None:
        if not self._import_raw:
            return
        try:
            data = self._import_raw
            if self.remove_bg.isChecked():
                data = remove_white_background(data)
            self._import_png = trim_transparent(data)
        except EditError as exc:
            self._import_png = None
            self.import_name.setText(str(exc))
        if self._import_png:
            pm = png_icon(self._import_png, QSize(420, 160)).pixmap(QSize(420, 160))
            self.import_preview.setPixmap(pm)
        self._update_ok()

    def _stamps_tab(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        self.stamps = QListWidget(w)
        for name, text in STAMP_LABELS.items():
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.stamps.addItem(item)
        self.stamps.setCurrentRow(0)
        self.stamps.itemDoubleClicked.connect(lambda *_: self._accept())
        lay.addWidget(self.stamps)
        return w

    # ----------------------------------------------------------------- result
    def current_choice(self) -> Optional[SignatureChoice]:
        tab = self.tabs.currentIndex()
        if tab == self.TAB_SAVED:
            item = self.saved.currentItem()
            data = self.store.read(item.data(Qt.ItemDataRole.UserRole)) if item else None
            return SignatureChoice(image=data, label="Signature") if data else None
        if tab == self.TAB_DRAW:
            data = self.pad_panel.png()
            return SignatureChoice(image=data, label="Signature") if data else None
        if tab == self.TAB_IMPORT:
            return SignatureChoice(image=self._import_png, label="Signature") \
                if self._import_png else None
        item = self.stamps.currentItem()
        if item is None:
            return None
        return SignatureChoice(stamp_name=item.data(Qt.ItemDataRole.UserRole), label=item.text())

    def _update_ok(self) -> None:
        if not hasattr(self, "ok"):  # signals can fire while the tabs are being built
            return
        self.ok.setEnabled(self.current_choice() is not None)

    def _accept(self) -> None:
        choice = self.current_choice()
        if choice is None:
            return
        tab = self.tabs.currentIndex()
        if choice.image is not None and (
                (tab == self.TAB_DRAW and self.pad_panel.save_box.isChecked()) or
                (tab == self.TAB_IMPORT and self.save_import.isChecked())):
            self.store.add(choice.image)
        self.choice = choice
        self.accept()
