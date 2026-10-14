"""Bookmarks (document outline) side panel."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QLabel, QStackedWidget,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.session import EventKind, SessionEvent
from ..pdf.models import OutlineEntry
from .icons import icons
from .sidebar import PanelHeader

if TYPE_CHECKING:  # pragma: no cover
    from .document_view import DocumentView

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1


class OutlinePanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.view: Optional["DocumentView"] = None
        self._items: List[QTreeWidgetItem] = []
        self._syncing = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PanelHeader("Bookmarks", self)
        self.expand_btn = self.header.add_button("chevron-down", "Expand all", self._expand_all)
        self.collapse_btn = self.header.add_button("chevron-up", "Collapse all", self._collapse_all)
        lay.addWidget(self.header)

        self.stack = QStackedWidget(self)
        self.tree = QTreeWidget(self.stack)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setMouseTracking(True)
        self.tree.itemClicked.connect(self._on_item_activated)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.stack.addWidget(self.tree)

        self.empty = QWidget(self.stack)
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(16, 28, 16, 16)
        el.setSpacing(8)
        self.empty_icon = QLabel(self.empty)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(self.empty_icon)
        self.empty_text = QLabel("This document has no bookmarks.", self.empty)
        self.empty_text.setProperty("role", "muted")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_text.setWordWrap(True)
        el.addWidget(self.empty_text)
        el.addStretch(1)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)
        self.refresh_icons()
        self._rebuild()

    # ---------------------------------------------------------------- binding
    def set_view(self, view: Optional["DocumentView"]) -> None:
        if view is self.view:
            return
        if self.view is not None:
            self.view.session.unsubscribe(self._on_session_event)
            try:
                self.view.currentPageChanged.disconnect(self._sync_to_page)
            except TypeError:
                pass
        self.view = view
        if view is not None:
            view.session.subscribe(self._on_session_event)
            view.currentPageChanged.connect(self._sync_to_page)
        self._rebuild()

    def _on_session_event(self, e: SessionEvent) -> None:
        if e.kind == EventKind.OUTLINE:
            self._rebuild()

    # --------------------------------------------------------------- content
    def _rebuild(self) -> None:
        self.tree.clear()
        self._items = []
        entries: List[OutlineEntry] = []
        if self.view is not None and not self.view.session.doc.closed:
            entries = self.view.session.outline()
        parents: List[QTreeWidgetItem] = []
        for e in entries:
            item = QTreeWidgetItem([e.title])
            item.setData(0, ENTRY_ROLE, e)
            tip = e.title
            if e.page_index >= 0:
                tip += f"\nPage {e.page_index + 1}"
            elif e.uri:
                tip += f"\n{e.uri}"
            item.setToolTip(0, tip)
            if e.page_index < 0 and not e.uri:
                item.setForeground(0, self.palette().placeholderText())
            level = max(1, e.level)
            while len(parents) >= level:
                parents.pop()
            if parents:
                parents[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents.append(item)
            self._items.append(item)
        # expand the first level for orientation
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.childCount() <= 12:
                top.setExpanded(True)
        has = bool(self._items)
        self.stack.setCurrentWidget(self.tree if has else self.empty)
        self.expand_btn.setEnabled(has)
        self.collapse_btn.setEnabled(has)
        if self.view is None:
            self.empty_text.setText("Open a document to see its bookmarks.")
        else:
            self.empty_text.setText("This document has no bookmarks.")
        if self.view is not None:
            self._sync_to_page(self.view.current_page)

    def _on_item_activated(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        if self._syncing or self.view is None:
            return
        e: OutlineEntry = item.data(0, ENTRY_ROLE)
        if e is None:
            return
        if e.page_index >= 0:
            self.view.go_to_page(e.page_index, e.y)
            self.view.canvas.setFocus()
        elif e.uri:
            QDesktopServices.openUrl(QUrl(e.uri))

    def _sync_to_page(self, page: int) -> None:
        """Select the deepest bookmark at or before the current page."""
        best = None
        for item in self._items:
            e: OutlineEntry = item.data(0, ENTRY_ROLE)
            if 0 <= e.page_index <= page:
                if best is None or e.page_index >= best[0]:
                    best = (e.page_index, item)
        self._syncing = True
        try:
            if best is not None:
                item = best[1]
                parent = item.parent()
                visible = item
                while parent is not None:
                    if not parent.isExpanded():
                        visible = parent
                    parent = parent.parent()
                self.tree.setCurrentItem(visible)
            else:
                self.tree.clearSelection()
        finally:
            self._syncing = False

    def _expand_all(self) -> None:
        self.tree.expandAll()

    def _collapse_all(self) -> None:
        self.tree.collapseAll()

    @property
    def entry_count(self) -> int:
        return len(self._items)

    def refresh_icons(self) -> None:
        self.header.refresh_icons()
        self.empty_icon.setPixmap(icons.pixmap("bookmark", 32))
