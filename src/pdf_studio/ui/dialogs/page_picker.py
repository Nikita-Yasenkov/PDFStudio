"""Page picker: a thumbnail grid with multi-selection and a range field.

Thumbnails render lazily in small batches on the UI thread so even large
documents open the dialog instantly.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from ...core.page_ranges import PageRangeError, format_ranges, parse_ranges
from ..qtutil import to_qimage

THUMB = QSize(96, 128)


def _page_icon(pm: QPixmap) -> QIcon:
    # the Selected mode would otherwise be a tinted copy: selection is shown
    # by the item background, the page itself keeps its real colors
    icon = QIcon(pm)
    icon.addPixmap(pm, QIcon.Mode.Selected)
    return icon


class PagePicker(QWidget):
    selectionChanged = pyqtSignal()

    def __init__(self, page_count: int, render: Callable[[int, int, int], Optional[QImage]],
                 parent: Optional[QWidget] = None, initial: Optional[Sequence[int]] = None):
        super().__init__(parent)
        self._count = page_count
        self._render = render
        self._syncing = False
        self.range_error = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Pages:", self))
        self.range_edit = QLineEdit(self)
        self.range_edit.setPlaceholderText(f"e.g. 1-3, 5, 8-{page_count}")
        self.range_edit.textEdited.connect(self._on_range_edited)
        row.addWidget(self.range_edit, 1)
        all_btn = QPushButton("All", self)
        all_btn.setProperty("variant", "link")
        all_btn.clicked.connect(lambda: self.set_selected(range(self._count)))
        none_btn = QPushButton("None", self)
        none_btn.setProperty("variant", "link")
        none_btn.clicked.connect(lambda: self.set_selected([]))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        lay.addLayout(row)

        self.error = QLabel("", self)
        self.error.setProperty("role", "error")
        self.error.hide()
        lay.addWidget(self.error)

        self.grid = QListWidget(self)
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(THUMB)
        self.grid.setGridSize(QSize(THUMB.width() + 22, THUMB.height() + 34))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(4)
        self.grid.setMinimumSize(520, 300)
        placeholder = self._placeholder()
        for i in range(page_count):
            item = QListWidgetItem(placeholder, str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.grid.addItem(item)
        self.grid.itemSelectionChanged.connect(self._on_grid_changed)
        lay.addWidget(self.grid, 1)

        self.summary = QLabel("", self)
        self.summary.setProperty("role", "muted")
        lay.addWidget(self.summary)

        self._next = 0
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_batch)
        self._timer.start()
        self.set_selected(range(page_count) if initial is None else initial)

    def _placeholder(self) -> QIcon:
        pm = QPixmap(THUMB)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.fillRect(8, 2, THUMB.width() - 16, THUMB.height() - 4, QColor("#FFFFFF"))
        p.setPen(QColor(0, 0, 0, 40))
        p.drawRect(8, 2, THUMB.width() - 17, THUMB.height() - 5)
        p.end()
        return _page_icon(pm)

    def _render_batch(self) -> None:
        # render visible items first, then the rest, a few per tick
        done = 0
        while self._next < self._count and done < 6:
            i = self._next
            self._next += 1
            img = self._render(i, THUMB.width() - 8, THUMB.height() - 4)
            if img is not None:
                pm = QPixmap(THUMB)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                x = (THUMB.width() - img.width()) // 2
                y = (THUMB.height() - img.height()) // 2
                p.fillRect(x, y, img.width(), img.height(), QColor("#FFFFFF"))
                p.drawImage(x, y, img)
                p.setPen(QColor(0, 0, 0, 50))
                p.drawRect(x, y, img.width() - 1, img.height() - 1)
                p.end()
                self.grid.item(i).setIcon(_page_icon(pm))
            done += 1
        if self._next >= self._count:
            self._timer.stop()

    # ------------------------------------------------------------ selection
    def selected(self) -> List[int]:
        return sorted(self.grid.row(i) for i in self.grid.selectedItems())

    def set_selected(self, indices) -> None:
        wanted = set(indices)
        self._syncing = True
        try:
            for i in range(self._count):
                self.grid.item(i).setSelected(i in wanted)
        finally:
            self._syncing = False
        self._on_grid_changed()

    def _on_grid_changed(self) -> None:
        sel = self.selected()
        if not self._syncing:
            text = "" if len(sel) == self._count else format_ranges(sel)
            if not self.range_edit.hasFocus():
                self.range_edit.setText(text if sel else "")
        n = len(sel)
        self.summary.setText(f"{n} of {self._count} page{'s' if self._count != 1 else ''} selected"
                             if n else "No pages selected")
        self.range_error = ""
        self.error.hide()
        self.selectionChanged.emit()

    def _on_range_edited(self, text: str) -> None:
        try:
            idx = parse_ranges(text, self._count) if text.strip() else list(range(self._count))
        except PageRangeError as exc:
            self.range_error = str(exc)
            self.error.setText(self.range_error)
            self.error.show()
            self.selectionChanged.emit()
            return
        self.set_selected(idx)
        if idx:
            self.grid.scrollToItem(self.grid.item(idx[0]))

    def is_valid(self) -> bool:
        return bool(self.selected()) and not self.range_error


def document_renderer(doc) -> Callable[[int, int, int], Optional[QImage]]:
    """Adapter: PdfDocument → render(index, w, h) callable for PagePicker."""

    def render(i: int, w: int, h: int) -> Optional[QImage]:
        try:
            return to_qimage(doc.render_fit(i, w, h))
        except Exception:  # noqa: BLE001
            return None

    return render
