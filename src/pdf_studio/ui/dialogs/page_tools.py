"""Extract / Split / Combine dialogs."""

from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog,
                             QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QRadioButton, QSpinBox, QVBoxLayout,
                             QWidget)

from ...core.page_ranges import PageRangeError, chunk, parse_ranges, split_before
from ..icons import icons
from ..qtutil import label
from .page_picker import PagePicker, document_renderer


def _buttons(dialog: QDialog, ok_text: str) -> Tuple[QDialogButtonBox, QPushButton]:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                           QDialogButtonBox.StandardButton.Cancel, dialog)
    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(ok_text)
    ok.setProperty("variant", "primary")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box, ok


# ----------------------------------------------------------------- extract
class ExtractDialog(QDialog):
    def __init__(self, parent: QWidget, doc, name: str, initial: Sequence[int]):
        super().__init__(parent)
        self.setWindowTitle("Extract Pages")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Extract pages to a new PDF", "heading"))
        lay.addWidget(label(f"From “{name}”. The original stays unchanged unless you choose "
                            "to delete the extracted pages.", "muted", wrap=True))
        self.picker = PagePicker(doc.page_count, document_renderer(doc), self, initial)
        lay.addWidget(self.picker, 1)
        self.open_after = QCheckBox("Open the new file in a tab", self)
        self.open_after.setChecked(True)
        self.delete_after = QCheckBox("Delete the extracted pages from this document", self)
        lay.addWidget(self.open_after)
        lay.addWidget(self.delete_after)
        box, self.ok = _buttons(self, "Extract…")
        lay.addWidget(box)
        self.picker.selectionChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        n = len(self.picker.selected())
        self.ok.setEnabled(self.picker.is_valid())
        self.ok.setText(f"Extract {n} page{'s' if n != 1 else ''}…" if n else "Extract…")
        # can't delete every page of a document
        self.delete_after.setEnabled(0 < n < self.picker._count)

    def values(self) -> Tuple[List[int], bool, bool]:
        return (self.picker.selected(), self.open_after.isChecked(),
                self.delete_after.isChecked() and self.delete_after.isEnabled())


# ------------------------------------------------------------------- split
class SplitDialog(QDialog):
    EVERY, BEFORE, BOOKMARKS = range(3)

    def __init__(self, parent: QWidget, page_count: int, name: str, out_dir: str,
                 bookmark_groups: List[List[int]]):
        super().__init__(parent)
        self.setWindowTitle("Split Document")
        self.setMinimumWidth(520)
        self.page_count = page_count
        self.base = os.path.splitext(name)[0]
        self.bookmarks = bookmark_groups
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Split into several PDFs", "heading"))
        lay.addWidget(label(f"“{name}” has {page_count} pages. The original is not changed.",
                            "muted", wrap=True))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.group = QButtonGroup(self)
        self.r_every = QRadioButton("Every", self)
        self.every = QSpinBox(self)
        self.every.setRange(1, max(1, page_count))
        self.every.setValue(min(max(1, page_count // 2), 10) if page_count > 1 else 1)
        self.every.setSuffix(" pages")
        self.r_before = QRadioButton("Before pages", self)
        self.before = QLineEdit(self)
        self.before.setPlaceholderText("e.g. 4, 9  — each starts a new file")
        self.r_bookmarks = QRadioButton("At each top-level bookmark", self)
        self.r_bookmarks.setEnabled(bool(bookmark_groups))
        if not bookmark_groups:
            self.r_bookmarks.setToolTip("This document has no bookmarks")
        for i, rb in enumerate((self.r_every, self.r_before, self.r_bookmarks)):
            self.group.addButton(rb, i)
        grid.addWidget(self.r_every, 0, 0)
        grid.addWidget(self.every, 0, 1)
        grid.addWidget(self.r_before, 1, 0)
        grid.addWidget(self.before, 1, 1)
        grid.addWidget(self.r_bookmarks, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.r_every.setChecked(True)

        lay.addSpacing(4)
        lay.addWidget(label("SAVE TO", "section"))
        row = QHBoxLayout()
        self.folder = QLineEdit(out_dir, self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.preview = label("", "muted", wrap=True)
        lay.addWidget(self.preview)
        self.error = label("", "error")
        lay.addWidget(self.error)

        box, self.ok = _buttons(self, "Split")
        lay.addWidget(box)
        for sig in (self.group.idClicked, self.every.valueChanged, self.before.textChanged,
                    self.folder.textChanged):
            sig.connect(lambda *_: self._update())
        self.every.valueChanged.connect(lambda *_: self.r_every.setChecked(True))
        self.before.textEdited.connect(lambda *_: self.r_before.setChecked(True))
        self._update()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def groups(self) -> List[List[int]]:
        mode = self.group.checkedId()
        if mode == self.EVERY:
            return chunk(self.page_count, self.every.value())
        if mode == self.BEFORE:
            starts = parse_ranges(self.before.text(), self.page_count) if self.before.text().strip() else []
            return split_before(self.page_count, starts)
        return self.bookmarks

    def _update(self) -> None:
        self.error.setText("")
        try:
            groups = self.groups()
        except PageRangeError as exc:
            self.error.setText(str(exc))
            self.ok.setEnabled(False)
            self.preview.setText("")
            return
        n = len(groups)
        folder_ok = os.path.isdir(self.folder.text())
        if not folder_ok:
            self.error.setText("The folder does not exist.")
        self.ok.setEnabled(n > 1 and folder_ok)
        self.ok.setText(f"Split into {n} files" if n > 1 else "Split")
        if n <= 1:
            self.preview.setText("Choose where to split: this would produce a single file.")
        else:
            w = len(str(n))
            names = [f"{self.base}-part{str(k).zfill(w)}.pdf" for k in range(1, n + 1)]
            sizes = ", ".join(str(len(g)) for g in groups[:6]) + ("…" if n > 6 else "")
            shown = names[0] + (f" … {names[-1]}" if n > 1 else "")
            self.preview.setText(f"{n} files: {shown}  (pages per file: {sizes})")

    def values(self) -> Tuple[List[List[int]], str]:
        return self.groups(), self.folder.text()


# ----------------------------------------------------------------- combine
class CombineDialog(QDialog):
    NEW, APPEND = range(2)

    def __init__(self, parent: QWidget, initial: Sequence[str], current_name: Optional[str],
                 start_dir: str, page_counter=None):
        super().__init__(parent)
        self.setWindowTitle("Combine Files")
        self.resize(560, 460)
        self.start_dir = start_dir
        self.page_counter = page_counter
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Combine PDFs", "heading"))
        lay.addWidget(label("Add files and drag them into order.", "muted"))
        row = QHBoxLayout()
        self.list = QListWidget(self)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(False)
        row.addWidget(self.list, 1)
        col = QVBoxLayout()
        add = QPushButton("Add Files…", self)
        add.setIcon(icons.icon("plus"))
        add.clicked.connect(self._add)
        self.remove = QPushButton("Remove", self)
        self.remove.clicked.connect(self._remove)
        up = QPushButton("Move Up", self)
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move Down", self)
        down.clicked.connect(lambda: self._move(1))
        for b in (add, self.remove, up, down):
            col.addWidget(b)
        col.addStretch(1)
        row.addLayout(col)
        lay.addLayout(row, 1)

        self.mode = QButtonGroup(self)
        self.r_new = QRadioButton("Create a new document", self)
        self.r_append = QRadioButton(f"Append to “{current_name}”" if current_name
                                     else "Append to the current document", self)
        self.r_append.setEnabled(current_name is not None)
        self.mode.addButton(self.r_new, self.NEW)
        self.mode.addButton(self.r_append, self.APPEND)
        self.r_new.setChecked(True)
        lay.addWidget(self.r_new)
        lay.addWidget(self.r_append)
        box, self.ok = _buttons(self, "Combine")
        lay.addWidget(box)
        self.mode.idClicked.connect(lambda *_: self._update())
        self.list.model().rowsInserted.connect(lambda *_: self._update())
        self.list.model().rowsRemoved.connect(lambda *_: self._update())
        for p in initial:
            self.add_path(p)
        self._update()

    def add_path(self, path: str) -> None:
        name = os.path.basename(path)
        extra = ""
        if self.page_counter is not None:
            n = self.page_counter(path)
            extra = f"   ·   {n} page{'s' if n != 1 else ''}" if n is not None else "   ·   locked"
        item = QListWidgetItem(icons.icon("file-text"), f"{name}{extra}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.list.addItem(item)

    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs", self.start_dir,
                                                "PDF documents (*.pdf)")
        for p in paths:
            self.add_path(p)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(new, item)
        self.list.setCurrentRow(new)

    def paths(self) -> List[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _update(self) -> None:
        n = self.list.count()
        need = 1 if self.mode.checkedId() == self.APPEND else 2
        self.ok.setEnabled(n >= need)
        self.ok.setText(f"Combine {n} file{'s' if n != 1 else ''}" if n else "Combine")

    def values(self) -> Tuple[List[str], int]:
        return self.paths(), self.mode.checkedId()
