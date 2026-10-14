"""Insert pages from another PDF: choose pages and position."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QVBoxLayout, QWidget)

from ...pdf import PdfDocument
from ..qtutil import label
from .page_picker import PagePicker, document_renderer

POS_BEFORE, POS_AFTER, POS_START, POS_END = range(4)


class InsertPagesDialog(QDialog):
    def __init__(self, parent: QWidget, source: PdfDocument, source_name: str,
                 target_count: int, current_page: int, default_position: int = POS_AFTER):
        super().__init__(parent)
        self.setWindowTitle("Insert Pages")
        self.setModal(True)
        self.resize(720, 560)
        self.target_count = target_count
        self.current_page = current_page

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label(f"Insert pages from “{source_name}”", "heading"))
        n = source.page_count
        lay.addWidget(label(f"{n} page{'s' if n != 1 else ''} · click thumbnails or type a range",
                            "muted"))
        self.picker = PagePicker(n, document_renderer(source), self)
        lay.addWidget(self.picker, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Insert:", self))
        self.position = QComboBox(self)
        p = current_page + 1
        self.position.addItems([f"Before page {p} (current)", f"After page {p} (current)",
                                "At the beginning", "At the end"])
        self.position.setCurrentIndex(default_position)
        row.addWidget(self.position, 1)
        lay.addLayout(row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setProperty("variant", "primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.picker.selectionChanged.connect(self._update_ok)
        self._update_ok()

    def _update_ok(self) -> None:
        n = len(self.picker.selected())
        self.ok.setEnabled(self.picker.is_valid())
        self.ok.setText(f"Insert {n} page{'s' if n != 1 else ''}" if n else "Insert")

    def insert_index(self) -> int:
        pos = self.position.currentIndex()
        if pos == POS_BEFORE:
            return self.current_page
        if pos == POS_AFTER:
            return self.current_page + 1
        if pos == POS_START:
            return 0
        return self.target_count

    def result_values(self) -> Tuple[List[int], int]:
        return self.picker.selected(), self.insert_index()
