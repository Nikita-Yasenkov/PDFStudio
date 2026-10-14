"""Main application window."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Optional

from PyQt6.QtCore import QEvent, QItemSelectionModel, QPoint, QSize, Qt
from PyQt6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu,
                             QSizePolicy, QSplitter, QStackedWidget, QTabBar,
                             QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import zoom as zm
from ..core.layout import ViewMode
from ..core.session import DocumentSession
from ..pdf import PdfError, PdfOpenError
from .dialogs import prompts
from .document_view import DocumentView
from .empty_state import EmptyState
from .icons import ICON_SIZE, icons
from .qtutil import human_size
from .render_service import RenderService
from ..core.page_commands import (DeletePagesCommand, DuplicatePagesCommand,
                                  InsertBlankPageCommand, InsertPdfCommand,
                                  MovePagesCommand, RotatePagesCommand)
from ..core.commands import CompositeCommand
from ..core.page_ranges import format_ranges
from ..pdf import PdfDocument
from ..pdf.documents import bookmark_groups, merge_files, safe_filename
from ..pdf.documents import extract_pages as extract_document_pages
from ..pdf.documents import split_document as split_document_pages
from .open_helpers import open_pdf_interactive, read_pdf_bytes
from .outline_panel import OutlinePanel
from .properties_panel import PropertiesPanel
from .search import SearchPanel
from .sidebar import Rail, SidePanelHost
from .settings import AppSettings
from .theme import DARK, LIGHT, SYSTEM, ThemeManager
from .thumbnails import ThumbnailPanel
from .tool_defaults import ToolDefaults
from .edit_tools import EditTextTool, ImageTool, RedactTool
from .sign_tool import SignatureTool
from .signatures import SignatureChoice
from .annotation_tools import (ArrowTool, EllipseTool, FreeTextTool, InkTool,
                               LineTool, NoteTool, RectangleTool, SHAPE_TOOLS)
from .tools import (HandTool, HighlightTool, SelectTool, StrikeoutTool,
                    UnderlineTool)

TOOL_ORDER = [SelectTool, HandTool, HighlightTool, UnderlineTool, StrikeoutTool,
              NoteTool, FreeTextTool, InkTool, RectangleTool, EllipseTool, LineTool, ArrowTool,
              EditTextTool, RedactTool, ImageTool, SignatureTool]
#: tools shown as toolbar groups (separator between groups); "shapes" is a
#: split button holding the shape tools
TOOL_GROUPS = [["select", "hand"], ["highlight", "underline", "strikeout"],
               ["note", "free_text", "ink", "shapes"], ["edit_text", "redact", "image"],
               ["sign"]]
SHAPE_NAMES = [cls.name for cls in SHAPE_TOOLS]


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[AppSettings] = None,
                 theme_manager: Optional[ThemeManager] = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings()
        self.theme_manager = theme_manager or ThemeManager(QApplication.instance(),
                                                           self.settings.theme)
        self.theme_manager.changed.connect(self._on_theme_changed)
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.setMinimumSize(720, 480)
        self.setAcceptDrops(True)
        self.actions: Dict[str, QAction] = {}
        self._tool_name = "select"
        self.render_service = RenderService(parent=self)
        self.tool_defaults = ToolDefaults(self.settings, self)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._restore_settings()
        self._sync_theme_actions()
        self._update_ui()

    # ------------------------------------------------------------- settings
    @property
    def _last_dir(self) -> str:
        return self.settings.last_dir

    def _restore_settings(self) -> None:
        s = self.settings
        geo = s.window_geometry
        if geo is not None:
            self.restoreGeometry(geo)
        state = s.window_state
        if state is not None:
            self.restoreState(state)
        self._side_width = max(170, min(480, s.sidebar_width))
        self.splitter.setSizes([self._side_width, max(400, self.width() - self._side_width)])
        panel = s.sidebar_panel
        self.show_side_panel(panel if panel in self.side.panels or panel == "" else "pages")
        self._refresh_recent()

    def _save_settings(self) -> None:
        s = self.settings
        s.window_geometry = self.saveGeometry()
        s.window_state = self.saveState()
        if self.side.isVisible() and self.side.width() > 0:
            self._side_width = self.side.width()
        s.sidebar_width = self._side_width
        s.sidebar_panel = self.rail.current if self.tabs.count() else self._side_key_saved()
        s.sync()

    def _side_key_saved(self) -> str:
        return self.rail.current or ("" if not self.side.isVisible() else self._side_key)

    def _on_splitter_moved(self, *_args) -> None:
        if self.side.isVisible():
            self._side_width = self.side.width()

    # ---------------------------------------------------------------- recent
    def _refresh_recent(self) -> None:
        self.empty.set_recent_files(self.settings.recent_files)

    def _build_recent_menu(self) -> None:
        m = self.recent_menu
        m.clear()
        files = self.settings.recent_files
        if not files:
            a = m.addAction("No recent files")
            a.setEnabled(False)
        for i, path in enumerate(files):
            name = os.path.basename(path)
            label = f"&{i + 1}  {name}" if i < 9 else f"{i + 1}  {name}"
            a = m.addAction(icons.icon("file-text"), label)
            a.setToolTip(path)
            a.setStatusTip(path)
            if not os.path.exists(path):
                a.setText(label + "  (missing)")
            a.triggered.connect(lambda _=False, p=path: self.open_recent(p))
        m.addSeparator()
        clear = m.addAction("Clear Recent Files", self.clear_recent)
        clear.setEnabled(bool(files))

    def open_recent(self, path: str) -> bool:
        if not os.path.exists(path):
            self.settings.remove_recent(path)
            self._refresh_recent()
            prompts.show_error(self, "File not found",
                               f"“{os.path.basename(path)}” could not be found.",
                               f"It was removed from the recent files list.\n\n{path}")
            return False
        return self.open_path(path)

    def clear_recent(self) -> None:
        self.settings.clear_recent()
        self._refresh_recent()

    # ------------------------------------------------------------- building
    def _act(self, key: str, text: str, slot, shortcut=None, icon: Optional[str] = None,
             tip: str = "", checkable: bool = False) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon))
            a.setProperty("icon_name", icon)
        shortcuts: List[QKeySequence] = []
        if isinstance(shortcut, QKeySequence.StandardKey):
            # all platform bindings (e.g. Close = Ctrl+W and Ctrl+F4 on Windows)
            shortcuts = list(QKeySequence.keyBindings(shortcut))
        elif isinstance(shortcut, (list, tuple)):
            shortcuts = [QKeySequence(s) for s in shortcut]
        elif shortcut is not None:
            shortcuts = [QKeySequence(shortcut)]
        if shortcuts:
            a.setShortcuts(shortcuts)
        tooltip = text.replace("&", "").rstrip("…")
        if shortcuts and not shortcuts[0].isEmpty():
            tooltip += f"  ({shortcuts[0].toString(QKeySequence.SequenceFormat.NativeText)})"
        a.setToolTip(tooltip)
        if tip:
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.actions[key] = a
        self.addAction(a)
        return a

    def _view_call(self, method: str):
        def call(*_args):
            v = self.view
            if v is not None:
                getattr(v, method)()
        return call

    def _build_actions(self) -> None:
        A = self._act
        # File
        A("open", "&Open…", self.open_dialog, QKeySequence.StandardKey.Open, "open",
          "Open a PDF document")
        A("save", "&Save", lambda: self.save(), QKeySequence.StandardKey.Save, "save",
          "Save changes to the current document")
        A("save_as", "Save &As…", lambda: self.save_as(), "Ctrl+Shift+S", "save-as",
          "Save the document under a new name")
        A("close", "&Close Tab", lambda: self.close_view(), QKeySequence.StandardKey.Close,
          "close", "Close the current document")
        A("close_all", "Close &All", self.close_all, "Ctrl+Shift+W", None,
          "Close all documents")
        A("exit", "E&xit", self.close, "Ctrl+Q", None, "Quit PDF Studio")
        # View — zoom
        A("zoom_in", "Zoom &In", self._view_call("zoom_in"), ["Ctrl++", "Ctrl+="], "zoom-in",
          "Increase magnification")
        A("zoom_out", "Zoom &Out", self._view_call("zoom_out"), "Ctrl+-", "zoom-out",
          "Decrease magnification")
        A("actual_size", "&Actual Size", self._view_call("actual_size"), "Ctrl+1",
          "actual-size", "Show the page at 100 %")
        A("fit_page", "Fit &Page", self._view_call("fit_page"), "Ctrl+0", "fit-page",
          "Fit the whole page in the window")
        A("fit_width", "Fit &Width", self._view_call("fit_width"), "Ctrl+2", "fit-width",
          "Fit the page width to the window")
        A("zoom_auto", "Automatic &Zoom",
          lambda: self.view and self.view.set_zoom_mode(zm.ZoomMode.AUTO),
          "Ctrl+3", None, "Fit width, up to 125 %")
        # View — navigation
        A("next_page", "&Next Page", self._view_call("next_page"), "PgDown", "chevron-right",
          "Go to the next page")
        A("prev_page", "&Previous Page", self._view_call("prev_page"), "PgUp", "chevron-left",
          "Go to the previous page")
        A("first_page", "&First Page", self._view_call("first_page"), "Home", "first-page",
          "Go to the first page")
        A("last_page", "&Last Page", self._view_call("last_page"), "End", "last-page",
          "Go to the last page")
        A("go_to_page", "&Go to Page…", self.go_to_page_dialog, "Ctrl+G", None,
          "Jump to a page number")
        self.view_mode_group = QActionGroup(self)
        for key, mode, icon_name, sc in (("mode_continuous", ViewMode.CONTINUOUS, "view-continuous", "Ctrl+4"),
                                         ("mode_single", ViewMode.SINGLE, "view-single", "Ctrl+5"),
                                         ("mode_two", ViewMode.TWO_PAGE, "view-two", "Ctrl+6")):
            a = A(key, mode.label, lambda _=False, m=mode: self.set_view_mode(m), sc, icon_name,
                  f"Show pages: {mode.label.lower()}", checkable=True)
            self.view_mode_group.addAction(a)
        self.theme_group = QActionGroup(self)
        for key, choice, text, icon_name in (("theme_system", SYSTEM, "Match &System", "monitor"),
                                             ("theme_light", LIGHT, "&Light", "sun"),
                                             ("theme_dark", DARK, "&Dark", "moon")):
            a = A(key, text, lambda _=False, c=choice: self.set_theme(c), None, icon_name,
                  f"Use the {text.replace('&', '').lower()} theme", checkable=True)
            self.theme_group.addAction(a)
        A("toggle_theme", "Toggle Light/Dark", self.toggle_theme, "Ctrl+Shift+L", "moon",
          "Switch between light and dark")
        A("toggle_sidebar", "Show &Sidebar", self.toggle_side_panel, "F4", "panel-left",
          "Show or hide the side panel", checkable=True)
        A("show_pages", "&Page Thumbnails", lambda: self.show_side_panel("pages"), None,
          "pages", "Show page thumbnails")
        A("show_outline", "&Bookmarks", lambda: self.show_side_panel("outline"), None,
          "bookmark", "Show the document outline")
        A("show_search", "&Search Panel", lambda: self.show_side_panel("search"), None,
          "search", "Show search results")
        # Edit — history
        A("undo", "&Undo", self._view_call("undo"), QKeySequence.StandardKey.Undo, "undo",
          "Undo the last change")
        A("redo", "&Redo", self._view_call("redo"), ["Ctrl+Shift+Z", "Ctrl+Y"], "redo",
          "Redo the last undone change")
        A("delete", "&Delete", self.delete_selection, QKeySequence.StandardKey.Delete, "delete",
          "Delete the selected annotation")
        A("properties", "&Properties", self._show_properties, "Ctrl+I", "settings",
          "Show the properties of the selected annotation or tool")
        A("copy", "&Copy", self._view_call("copy_selection"), QKeySequence.StandardKey.Copy,
          "copy", "Copy the selected text")
        A("select_all", "Select &All Text", self._view_call("select_all_text"),
          QKeySequence.StandardKey.SelectAll, None, "Select all text on the current page")
        # Edit — find
        A("find", "&Find…", self.find, QKeySequence.StandardKey.Find, "search",
          "Search the document")
        A("find_next", "Find &Next", lambda: self._search_step(+1), "F3", None,
          "Go to the next search result")
        A("find_prev", "Find Pre&vious", lambda: self._search_step(-1), "Shift+F3", None,
          "Go to the previous search result")
        # Pages
        A("rotate_right", "Rotate &Right", lambda: self.rotate_pages(90), "Ctrl+R", "rotate-cw",
          "Rotate the selected pages 90° clockwise")
        A("rotate_left", "Rotate &Left", lambda: self.rotate_pages(-90), "Ctrl+Shift+R",
          "rotate-ccw", "Rotate the selected pages 90° counter-clockwise")
        A("duplicate_page", "D&uplicate Page", self.duplicate_pages, "Ctrl+D", "duplicate",
          "Duplicate the selected pages")
        A("delete_page", "&Delete Page", self.delete_pages, "Ctrl+Shift+D", "delete",
          "Delete the selected pages")
        A("insert_blank_after", "Insert &Blank Page After", lambda: self.insert_blank(after=True),
          "Ctrl+Shift+N", "page-add", "Insert an empty page after the current page")
        A("insert_blank_before", "Insert Blank Page &Before", lambda: self.insert_blank(after=False),
          None, None, "Insert an empty page before the current page")
        A("insert_pdf", "Insert Pages from &PDF…", lambda: self.insert_from_pdf(),
          "Ctrl+Shift+I", "page-import", "Insert pages from another PDF document")
        A("extract_pages", "E&xtract Pages…", lambda: self.extract_pages(), "Ctrl+Shift+E",
          "extract", "Save selected pages as a new PDF")
        A("split_document", "&Split Document…", lambda: self.split_document(), None, "split",
          "Split this document into several PDFs")
        A("combine_files", "Co&mbine Files…", lambda: self.combine_files(), "Ctrl+Shift+M",
          "merge", "Combine several PDFs into one")
        A("insert_blank_end", "Insert Blank Page at &End",
          lambda: self.insert_blank(at_end=True), None, None,
          "Append an empty page to the document")
        # Help
        A("shortcuts", "&Keyboard Shortcuts", self.show_shortcuts, "F1", "keyboard",
          "Show all keyboard shortcuts")
        A("about", f"&About {APP_NAME}", lambda: prompts.show_about(self), None, "info",
          "Version and library information")
        A("integration", f"{APP_NAME} on This &Computer…", self.show_integration, None,
          "monitor", "Install PDF Studio and make it the default PDF app")
        # Window / tabs
        A("next_tab", "Next Tab", lambda: self._cycle_tab(+1), "Ctrl+Tab", None,
          "Switch to the next document")
        A("prev_tab", "Previous Tab", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab", None,
          "Switch to the previous document")
        # Tools
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        for cls in TOOL_ORDER:
            a = A(f"tool_{cls.name}", cls.label, lambda _=False, n=cls.name: self.set_tool(n),
                  cls.shortcut or None, cls.icon, cls.hint, checkable=True)
            self.tool_group.addAction(a)
        self.actions["tool_select"].setChecked(True)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.actions["open"])
        self.recent_menu = m.addMenu(icons.icon("clock"), "Open &Recent")
        self.recent_menu.aboutToShow.connect(self._build_recent_menu)
        m.addAction(self.actions["combine_files"])
        m.addSeparator()
        m.addAction(self.actions["save"])
        m.addAction(self.actions["save_as"])
        m.addSeparator()
        m.addAction(self.actions["close"])
        m.addAction(self.actions["close_all"])
        m.addSeparator()
        m.addAction(self.actions["exit"])

        m = mb.addMenu("&Edit")
        for key in ("undo", "redo", None, "copy", "select_all", "delete", None, "find",
                    "find_next", "find_prev", None, "properties"):
            if key is None:
                m.addSeparator()
            else:
                m.addAction(self.actions[key])
        self.edit_menu = m

        m = mb.addMenu("&View")
        theme_menu = m.addMenu(icons.icon("palette"), "&Theme")
        for key in ("theme_system", "theme_light", "theme_dark"):
            theme_menu.addAction(self.actions[key])
        self.theme_menu = theme_menu
        m.addSeparator()
        for key in ("mode_continuous", "mode_single", "mode_two", None,
                    "toggle_sidebar", "show_pages", "show_outline", "show_search", None,
                    "zoom_in", "zoom_out", None, "actual_size", "fit_page", "fit_width",
                    "zoom_auto", None, "prev_page", "next_page", "first_page", "last_page",
                    "go_to_page", None, "next_tab", "prev_tab"):
            if key is None:
                m.addSeparator()
            else:
                m.addAction(self.actions[key])

        m = mb.addMenu("&Pages")
        for key in ("rotate_left", "rotate_right", None, "duplicate_page", "delete_page", None,
                    "insert_blank_before", "insert_blank_after", "insert_blank_end", None,
                    "insert_pdf", None, "extract_pages", "split_document", "combine_files"):
            if key is None:
                m.addSeparator()
            else:
                m.addAction(self.actions[key])
        self.pages_menu = m

        m = mb.addMenu("&Tools")
        for i, group in enumerate(TOOL_GROUPS):
            if i:
                m.addSeparator()
            for name in group:
                if name == "shapes":
                    sub = m.addMenu(icons.icon("shapes"), "&Shapes")
                    for shape in SHAPE_NAMES:
                        sub.addAction(self.actions[f"tool_{shape}"])
                elif name == "sign":
                    m.addAction(self.actions["tool_signature"])
                    self.stamps_menu = m.addMenu(icons.icon("stamp"), "S&tamps")
                    self.stamps_menu.aboutToShow.connect(
                        lambda: self._fill_stamps_menu(self.stamps_menu))
                else:
                    m.addAction(self.actions[f"tool_{name}"])
        self.tools_menu = m

        m = mb.addMenu("&Help")
        m.addAction(self.actions["shortcuts"])
        m.addSeparator()
        m.addAction(self.actions["integration"])
        m.addSeparator()
        m.addAction(self.actions["about"])
        self.help_menu = m

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(ICON_SIZE)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.addAction(self.actions["open"])
        tb.addAction(self.actions["save"])
        tb.addSeparator()
        tb.addAction(self.actions["undo"])
        tb.addAction(self.actions["redo"])
        tb.addSeparator()
        for i, group in enumerate(TOOL_GROUPS):
            if i:
                tb.addSeparator()
            for name in group:
                if name == "shapes":
                    tb.addWidget(self._build_shapes_button(tb))
                elif name == "sign":
                    tb.addWidget(self._build_sign_button(tb))
                else:
                    tb.addAction(self.actions[f"tool_{name}"])
        spacer = QWidget(tb)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.actions["toggle_theme"])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

    def _build_shapes_button(self, parent) -> QToolButton:
        b = QToolButton(parent)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        b.setCheckable(True)
        b.setAutoRaise(True)
        menu = QMenu(b)
        for name in SHAPE_NAMES:
            menu.addAction(self.actions[f"tool_{name}"])
        b.setMenu(menu)
        self._last_shape = SHAPE_NAMES[0]
        b.setDefaultAction(self.actions[f"tool_{self._last_shape}"])
        self.shapes_button = b
        return b

    def _build_sign_button(self, parent) -> QToolButton:
        b = QToolButton(parent)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        b.setAutoRaise(True)
        b.setDefaultAction(self.actions["tool_signature"])
        menu = QMenu(b)
        menu.aboutToShow.connect(lambda: self._fill_sign_menu(menu))
        b.setMenu(menu)
        self.sign_button = b
        return b

    def _fill_sign_menu(self, menu: QMenu) -> None:
        from .dialogs.signature import png_icon
        menu.clear()
        store = self.tool_defaults.signature_store
        saved = store.list()[:5]
        if saved:
            head = menu.addAction("Saved signatures")
            head.setEnabled(False)
            for path in saved:
                data = store.read(path)
                if not data:
                    continue
                a = menu.addAction(png_icon(data, QSize(120, 36)), "")
                a.setToolTip("Place this signature")
                a.triggered.connect(lambda _=False, d=data: self.arm_signature(
                    SignatureChoice(image=d, label="Signature")))
            menu.addSeparator()
        menu.addAction(icons.icon("signature"), "New Signature…",
                       lambda: self._new_signature())
        stamps = menu.addMenu(icons.icon("stamp"), "Stamps")
        self._fill_stamps_menu(stamps)
        enabled = self.view is not None
        for a in menu.actions():
            if a.text() != "Saved signatures":
                a.setEnabled(enabled)

    def _fill_stamps_menu(self, menu: QMenu) -> None:
        from ..pdf.stamps import STAMP_LABELS
        menu.clear()
        for name, text in STAMP_LABELS.items():
            a = menu.addAction(text)
            a.triggered.connect(lambda _=False, n=name, t=text: self.arm_signature(
                SignatureChoice(stamp_name=n, label=t)))
            a.setEnabled(self.view is not None)

    def _new_signature(self) -> None:
        v = self.view
        if v is None:
            return
        choice = prompts.ask_signature(self, self.tool_defaults.signature_store)
        if choice is not None:
            self.arm_signature(choice)

    def arm_signature(self, choice: SignatureChoice) -> None:
        """Arm the Sign tool with a chosen signature/stamp, ready to place."""
        v = self.view
        if v is None:
            return
        v.tools.tools["signature"].arm(choice)
        self.set_tool("signature")
        v.canvas.setFocus()

    def _sync_shapes_button(self, name: str) -> None:
        b = getattr(self, "shapes_button", None)
        if b is None:
            return
        if name in SHAPE_NAMES and name != self._last_shape:
            self._last_shape = name
            b.setDefaultAction(self.actions[f"tool_{name}"])
        b.setChecked(name in SHAPE_NAMES)

    def _build_central(self) -> None:
        central = QWidget(self)
        central.setObjectName("Chrome")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # tab strip
        self.tab_strip = QWidget(central)
        self.tab_strip.setObjectName("TabStrip")
        self.tab_strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ts = QHBoxLayout(self.tab_strip)
        ts.setContentsMargins(8, 0, 8, 0)
        ts.setSpacing(0)
        self.tabs = QTabBar(self.tab_strip)
        self.tabs.setObjectName("DocTabs")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(lambda i: self.close_view(self._view_at(i)))
        self.tabs.tabMoved.connect(lambda *_: None)
        self.tabs.customContextMenuRequested.connect(self._tab_context_menu)
        self.tabs.tabBarClicked.connect(self._on_tab_clicked)
        ts.addWidget(self.tabs)
        ts.addStretch(1)
        outer.addWidget(self.tab_strip)

        # body: rail | splitter(side panels | documents)
        self.body = QWidget(central)
        body = QHBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.rail = Rail(self.body)
        self.rail.add_panel_button("pages", "pages", "Page thumbnails (F4)")
        self.rail.add_panel_button("outline", "bookmark", "Bookmarks")
        self.rail.add_panel_button("search", "search", "Search (Ctrl+F)")
        self.rail.panelRequested.connect(self.show_side_panel)
        body.addWidget(self.rail)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.body)
        self.splitter.setChildrenCollapsible(False)
        self.side = SidePanelHost(self.splitter)
        self.thumbnails = ThumbnailPanel(self.render_service, self.side)
        for key in ("rotate_left", "rotate_right", "delete_page"):
            self.thumbnails.add_header_action(self.actions[key])
        self.thumbnails.selectionChanged.connect(self._update_page_actions)
        self.thumbnails.moveRequested.connect(self.move_pages)
        self.thumbnails.deleteRequested.connect(self.delete_pages)
        self.thumbnails.contextMenuRequested.connect(self._page_context_menu)
        self.thumbnails.filesDropped.connect(self.insert_files)
        self.side.add_panel("pages", self.thumbnails)
        self.outline_panel = OutlinePanel(self.side)
        self.side.add_panel("outline", self.outline_panel)
        self.search_panel = SearchPanel(self.side)
        self.side.add_panel("search", self.search_panel)
        self.stack = QStackedWidget(self.splitter)
        self.empty = EmptyState(self.stack)
        self.empty.openRequested.connect(self.open_dialog)
        self.empty.openPathRequested.connect(self.open_recent)
        self.empty.clearRecentRequested.connect(self.clear_recent)
        self.stack.addWidget(self.empty)
        self.inspector = PropertiesPanel(self.stack)
        self.inspector.set_defaults(self.tool_defaults)
        self.stack.installEventFilter(self)
        self.splitter.addWidget(self.side)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([230, 1000])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self._side_width = 230
        body.addWidget(self.splitter, 1)
        outer.addWidget(self.body, 1)
        self.setCentralWidget(central)
        self._side_key = "pages"
        self.show_side_panel("pages")

    # ------------------------------------------------------------ side panel
    def show_side_panel(self, key: str) -> None:
        """Show panel ``key`` (or collapse the sidebar when key is empty)."""
        if key:
            was_hidden = not self.side.isVisible()
            self.side.show_panel(key)
            self.side.show()
            self._side_key = key
            if was_hidden and hasattr(self, "_side_width"):
                total = sum(self.splitter.sizes()) or self.width()
                self.splitter.setSizes([self._side_width, max(200, total - self._side_width)])
        else:
            if self.side.isVisible() and self.side.width() > 0:
                self._side_width = self.side.width()
            self.side.hide()
        self.rail.set_current(key)
        a = self.actions.get("toggle_sidebar")
        if a is not None:
            a.setChecked(bool(key))

    def toggle_side_panel(self) -> None:
        self.show_side_panel("" if self.side.isVisible() else (self._side_key or "pages"))

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_hint = QLabel("")
        sb.addWidget(self.status_hint, 1)
        self.status_page = QLabel("")
        self.status_size = QLabel("")
        self.status_zoom = QLabel("")
        for w in (self.status_page, self.status_size, self.status_zoom):
            sb.addPermanentWidget(w)

    # ------------------------------------------------------------------ tabs
    @property
    def view(self) -> Optional[DocumentView]:
        w = self.stack.currentWidget()
        return w if isinstance(w, DocumentView) else None

    def views(self) -> List[DocumentView]:
        return [self._view_at(i) for i in range(self.tabs.count())]

    def _view_at(self, index: int) -> DocumentView:
        return self.tabs.tabData(index)

    def _index_of(self, view: DocumentView) -> int:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) is view:
                return i
        return -1

    def _on_tab_changed(self, index: int) -> None:
        if index < 0:
            self.stack.setCurrentWidget(self.empty)
            self.side.bind(None)
            self.inspector.set_view(None)
        else:
            view = self._view_at(index)
            self.stack.setCurrentWidget(view)
            self.side.bind(view)
            self.inspector.set_view(view)
            # one-shot tools never carry over to another tab
            view.tools.set_tool(self._tool_name if self._tool_name != "signature" else "select")
            view.canvas.setFocus()
        self._update_ui()

    def _on_tab_clicked(self, index: int) -> None:
        if index >= 0 and QGuiApplication.mouseButtons() & Qt.MouseButton.MiddleButton:
            self.close_view(self._view_at(index))

    def _cycle_tab(self, step: int) -> None:
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def activate_view(self, view: DocumentView) -> None:
        i = self._index_of(view)
        if i >= 0:
            self.tabs.setCurrentIndex(i)

    def _tab_context_menu(self, pos: QPoint) -> None:
        index = self.tabs.tabAt(pos)
        if index < 0:
            return
        view = self._view_at(index)
        m = QMenu(self)
        m.addAction("Close", lambda: self.close_view(view))
        m.addAction("Close Others", lambda: self._close_others(view)).setEnabled(self.tabs.count() > 1)
        m.addAction("Close All", self.close_all)
        m.addSeparator()
        path = view.session.path
        m.addAction("Copy Path", lambda: QGuiApplication.clipboard().setText(path or "")).setEnabled(bool(path))
        m.addAction("Show in Folder", lambda: self._reveal(path)).setEnabled(bool(path))
        m.exec(self.tabs.mapToGlobal(pos))

    def _close_others(self, keep: DocumentView) -> None:
        for v in self.views():
            if v is not keep and not self.close_view(v):
                return

    @staticmethod
    def _reveal(path: Optional[str]) -> None:
        if not path:
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:  # pragma: no cover
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _refresh_tab(self, view: DocumentView) -> None:
        i = self._index_of(view)
        if i < 0:
            return
        self.tabs.setTabText(i, view.title)
        self.tabs.setTabToolTip(i, view.session.path or view.title)
        self.tabs.setTabIcon(i, icons.icon("lock" if view.session.is_encrypted else "file-text"))
        if view is self.view:
            self._update_title()

    # ---------------------------------------------------------------- files
    def open_dialog(self) -> None:
        for path in prompts.ask_open_pdfs(self, self._last_dir):
            self.open_path(path)

    def find_view(self, path: str) -> Optional[DocumentView]:
        norm = os.path.normcase(os.path.abspath(path))
        for v in self.views():
            if v.session.path and os.path.normcase(v.session.path) == norm:
                return v
        return None

    def open_path(self, path: str) -> bool:
        existing = self.find_view(path)
        if existing is not None:
            self.activate_view(existing)
            return True
        # prompts for a password when needed; never leaves a half-open document
        doc = open_pdf_interactive(self, path, "Could not open file")
        if doc is None:
            return False
        session = DocumentSession(doc)
        self.settings.last_dir = os.path.dirname(os.path.abspath(path))
        self.settings.add_recent(path)
        self._refresh_recent()
        view = self.add_session(session)
        self._maybe_suggest_default(view)
        return True

    def add_session(self, session: DocumentSession) -> DocumentView:
        view = DocumentView(session, self.render_service, self.stack,
                            tool_defaults=self.tool_defaults, view_mode=self._preferred_mode())
        view.viewModeChanged.connect(lambda m, v=view: self._on_view_mode_changed(v, m))
        view.currentPageChanged.connect(lambda *_a, v=view: self._on_view_status(v))
        view.zoomChanged.connect(lambda *_a, v=view: self._on_view_status(v))
        view.titleChanged.connect(lambda v=view: self._refresh_tab(v))
        view.historyChanged.connect(lambda v=view: v is self.view and self._update_history())
        view.selectionChanged.connect(lambda v=view: v is self.view and self._update_selection())
        view.annotationSelectionChanged.connect(
            lambda v=view: v is self.view and self._update_selection())
        view.searchRequested.connect(lambda text, v=view: v is self.view and self._find_text(text))
        view.errorOccurred.connect(lambda title, msg: prompts.show_error(self, title, msg))
        view.tools.hintChanged.connect(lambda text, v=view: v is self.view and self.status_hint.setText(text))
        view.tools.toolChanged.connect(lambda name, v=view: v is self.view and self._on_view_tool_changed(name))
        self.stack.addWidget(view)
        self.tabs.blockSignals(True)
        index = self.tabs.addTab(view.title)
        self.tabs.setTabData(index, view)
        self.tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, self._tab_close_button(view))
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
        self._refresh_tab(view)
        self._on_tab_changed(index)
        return view

    def _tab_close_button(self, view: DocumentView) -> QToolButton:
        b = QToolButton(self.tabs)
        b.setObjectName("TabClose")
        b.setIcon(icons.icon("close"))
        b.setIconSize(QSize(12, 12))
        b.setFixedSize(18, 18)
        b.setAutoRaise(True)
        b.setToolTip("Close (Ctrl+W)")
        b.setCursor(Qt.CursorShape.ArrowCursor)
        b.clicked.connect(lambda: self.close_view(view))
        return b

    def close_view(self, view: Optional[DocumentView] = None) -> bool:
        view = view or self.view
        if view is None:
            return True
        if view.session.dirty:
            self.activate_view(view)
            choice = prompts.ask_close_choice(self, view.session.display_name)
            if choice == prompts.CloseChoice.CANCEL:
                return False
            if choice == prompts.CloseChoice.SAVE and not self.save(view):
                return False
        index = self._index_of(view)
        if index >= 0:
            self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.side.bind(None)
            self.inspector.set_view(None)
        self.stack.removeWidget(view)
        view.close_document()
        view.deleteLater()
        if self.tabs.count() == 0:
            self.stack.setCurrentWidget(self.empty)
        self._update_ui()
        return True

    def close_all(self) -> bool:
        for v in list(self.views()):
            if not self.close_view(v):
                return False
        return True

    # backwards-compatible name used by early tests
    def close_document(self) -> bool:
        return self.close_view()

    # ---------------------------------------------------------------- pages
    def target_page_ids(self) -> List[int]:
        """Pages an action applies to: the thumbnail selection when the pages
        panel is showing one, otherwise the current page."""
        v = self.view
        if v is None:
            return []
        if self.side.isVisible() and self.side.currentWidget() is self.thumbnails:
            rows = self.thumbnails.selected_rows()
            if len(rows) > 1:
                return [v.session.page_id(r) for r in rows if r < v.page_count]
        return [v.session.page_id(v.current_page)]

    def _update_page_actions(self) -> None:
        v = self.view
        if v is None:
            self.actions["delete_page"].setEnabled(False)
            return
        n = len(self.target_page_ids())
        self.actions["delete_page"].setEnabled(v.page_count > n)
        suffix = "s" if n > 1 else ""
        self.actions["delete_page"].setText(f"&Delete Page{suffix}" + (f" ({n})" if n > 1 else ""))
        self.actions["duplicate_page"].setText(f"D&uplicate Page{suffix}" + (f" ({n})" if n > 1 else ""))

    def rotate_pages(self, delta: int, page_ids: Optional[List[int]] = None) -> bool:
        v = self.view
        ids = page_ids or self.target_page_ids()
        if v is None or not ids:
            return False
        return v.execute(RotatePagesCommand(ids, delta))

    def duplicate_pages(self, page_ids: Optional[List[int]] = None) -> bool:
        v = self.view
        ids = page_ids or self.target_page_ids()
        if v is None or not ids:
            return False
        cmd = DuplicatePagesCommand(ids)
        ok = v.execute(cmd)
        if ok and len(ids) == 1:
            v.go_to_page(v.session.index_of(cmd.new_ids[0]))
        return ok

    def delete_pages(self, page_ids: Optional[List[int]] = None) -> bool:
        v = self.view
        ids = page_ids or self.target_page_ids()
        if v is None or not ids:
            return False
        if len(ids) >= v.page_count:
            v.toast("A document must keep at least one page")
            return False
        ok = v.execute(DeletePagesCommand(ids))
        if ok:
            n = len(ids)
            v.toast(f"Deleted {n} page{'s' if n != 1 else ''}", "Undo", v.undo)
            self.thumbnails.list.clearSelection()
        return ok

    def move_pages(self, page_ids: List[int], target: int) -> bool:
        v = self.view
        if v is None or not page_ids:
            return False
        ok = v.execute(MovePagesCommand(page_ids, target))
        if ok:
            # keep the moved pages selected and show where they landed
            rows = sorted(v.session.index_of(pid) for pid in page_ids)
            sm = self.thumbnails.list.selectionModel()
            sm.clearSelection()
            for r in rows:
                sm.select(self.thumbnails.model.index(r), QItemSelectionModel.SelectionFlag.Select)
            v.go_to_page(rows[0])
            n = len(page_ids)
            v.toast(f"Moved {n} page{'s' if n != 1 else ''}", "Undo", v.undo, msec=2500)
        return ok

    def _page_context_menu(self, page_ids: List[int], global_pos) -> None:
        v = self.view
        if v is None:
            return
        n = len(page_ids)
        many = n > 1
        m = QMenu(self)
        if not many and page_ids:
            idx = v.session.index_of(page_ids[0])
            m.addAction(f"Go to Page {idx + 1}", lambda: v.go_to_page(idx))
            m.addSeparator()
        m.addAction(icons.icon("rotate-ccw"), "Rotate Left", lambda: self.rotate_pages(-90, page_ids))
        m.addAction(icons.icon("rotate-cw"), "Rotate Right", lambda: self.rotate_pages(90, page_ids))
        m.addSeparator()
        m.addAction(icons.icon("duplicate"), f"Duplicate{f' {n} Pages' if many else ''}",
                    lambda: self.duplicate_pages(page_ids))
        delete = m.addAction(icons.icon("delete"), f"Delete{f' {n} Pages' if many else ''}",
                             lambda: self.delete_pages(page_ids))
        delete.setEnabled(n < v.page_count)
        m.addSeparator()
        if page_ids:
            first = min(v.session.index_of(pid) for pid in page_ids)
            last = max(v.session.index_of(pid) for pid in page_ids)
            m.addAction(icons.icon("page-add"), "Insert Blank Page Before",
                        lambda: self.insert_blank(after=False, index=first))
            m.addAction("Insert Blank Page After", lambda: self.insert_blank(after=True, index=last))
            self._extend_page_menu(m, page_ids, first, last)
        m.exec(global_pos)

    def _extend_page_menu(self, menu: QMenu, page_ids: List[int], first: int, last: int) -> None:
        menu.addAction(icons.icon("page-import"), "Insert Pages from PDF…",
                       lambda: self.insert_from_pdf(at=last + 1))
        menu.addSeparator()
        n = len(page_ids)
        menu.addAction(icons.icon("extract"), f"Extract {'These ' + str(n) + ' Pages' if n > 1 else 'Page'}…",
                       lambda: self.extract_pages([self.view.session.index_of(p) for p in page_ids]))

    # ------------------------------------------------- extract / split / combine
    def _doc_folder(self, v: DocumentView) -> str:
        return os.path.dirname(v.session.path) if v.session.path else self._last_dir

    def extract_pages(self, indices: Optional[List[int]] = None) -> bool:
        v = self.view
        if v is None:
            return False
        if indices is None:
            indices = [v.session.index_of(pid) for pid in self.target_page_ids()]
        choice = prompts.ask_extract(self, v.session.doc, v.session.display_name, indices)
        if choice is None:
            return False
        pages, open_after, delete_after = choice
        stem = os.path.splitext(v.session.display_name)[0]
        suggested = os.path.join(self._doc_folder(v),
                                 safe_filename(f"{stem} (pages {format_ranges(pages)}).pdf"))
        out = prompts.ask_save_pdf(self, suggested, "Save Extracted Pages")
        if not out:
            return False
        if self.find_view(out) is not None:
            prompts.show_error(self, "Cannot extract", "That file is open in a tab.",
                               "Close it first or choose another name.")
            return False
        try:
            path = extract_document_pages(v.session.doc, pages, out)
        except PdfError as exc:
            prompts.show_error(self, "Could not extract pages", str(exc))
            return False
        if delete_after:
            v.execute(DeletePagesCommand([v.session.page_id(i) for i in pages]))
        n = len(pages)
        msg = f"Extracted {n} page{'s' if n != 1 else ''} to “{os.path.basename(path)}”"
        if open_after:
            self.open_path(path)
            if self.view is not None:
                self.view.toast(msg)
        else:
            v.toast(msg, "Open", lambda p=path: self.open_path(p))
        return True

    def split_document(self) -> bool:
        v = self.view
        if v is None:
            return False
        groups = bookmark_groups(v.session.doc)
        choice = prompts.ask_split(self, v.page_count, v.session.display_name,
                                   self._doc_folder(v), groups)
        if choice is None:
            return False
        groups, folder = choice
        stem = os.path.splitext(v.session.display_name)[0]
        try:
            paths = split_document_pages(v.session.doc, groups, folder, stem)
        except PdfError as exc:
            prompts.show_error(self, "Could not split the document", str(exc))
            return False
        v.toast(f"Split into {len(paths)} files", "Show",
                lambda p=paths[0]: self._reveal(p), msec=5000)
        return True

    def _page_count_of(self, path: str) -> Optional[int]:
        try:
            doc = PdfDocument.open(path)
        except PdfError:
            return None
        n = doc.page_count
        doc.close()
        return n

    def combine_files(self, initial: Optional[List[str]] = None) -> bool:
        v = self.view
        if initial is None:
            initial = [v.session.path] if v is not None and v.session.path else []
        choice = prompts.ask_combine(self, initial, v.session.display_name if v else None,
                                     self._last_dir, self._page_count_of)
        if choice is None:
            return False
        paths, mode = choice
        # gather sources: open tabs contribute their current (possibly unsaved) state
        sources = []
        for p in paths:
            open_view = self.find_view(p)
            if open_view is not None:
                sources.append((open_view.session.doc.page_bytes(), open_view.session.doc.password))
                continue
            doc = open_pdf_interactive(self, p, "Could not combine files")
            if doc is None:
                return False
            sources.append((p, doc.password))
            doc.close()
        titles = [os.path.splitext(os.path.basename(p))[0] for p in paths]
        if mode == 1 and v is not None:
            cmds = []
            for (src, pw), title in zip(sources, titles):
                if isinstance(src, bytes):
                    data = src
                else:
                    with open(src, "rb") as fh:
                        data = fh.read()
                cmds.append(InsertPdfCommand(data, 10 ** 9, None, pw, label=f"Append {title}"))
            # each command appends after the previous one
            ok = v.execute(CompositeCommand(f"Append {len(cmds)} file{'s' if len(cmds) != 1 else ''}",
                                            cmds))
            if ok:
                v.toast(f"Appended {len(cmds)} file{'s' if len(cmds) != 1 else ''}", "Undo", v.undo)
            return ok
        try:
            data = merge_files(sources, titles)
        except PdfError as exc:
            prompts.show_error(self, "Could not combine files", str(exc))
            return False
        session = DocumentSession(PdfDocument.from_bytes(data, "Combined.pdf"))
        session.stack.mark_unsaved()
        view = self.add_session(session)
        view.toast(f"Combined {len(paths)} files — use Save As to keep the result", msec=4500)
        return True

    def insert_from_pdf(self, path: Optional[str] = None, at: Optional[int] = None) -> bool:
        """Pick a PDF, choose pages and position, insert (undoable)."""
        v = self.view
        if v is None:
            return False
        path = path or prompts.ask_open_pdf(self, self._last_dir, "Insert pages from")
        if not path:
            return False
        opened = read_pdf_bytes(self, path)
        if opened is None:
            return False
        data, src = opened
        try:
            default_pos = 1
            current = v.current_page
            if at is not None:
                current, default_pos = max(0, at - 1), 1
            choice = prompts.ask_insert_pages(self, src, os.path.basename(path), v.page_count,
                                              current, default_pos)
            password = src.password
        finally:
            src.close()
        if choice is None:
            return False
        pages, index = choice
        cmd = InsertPdfCommand(data, index, pages, password,
                               label=f"Insert {len(pages)} page{'s' if len(pages) != 1 else ''}")
        ok = v.execute(cmd)
        if ok:
            v.go_to_page(v.session.index_of(cmd.new_ids[0]))
            v.toast(f"Inserted {len(pages)} page{'s' if len(pages) != 1 else ''} from "
                    f"“{os.path.basename(path)}”", "Undo", v.undo)
        return ok

    def insert_files(self, paths: List[str], at: int) -> None:
        """Insert all pages of dropped PDF files at a thumbnail position."""
        v = self.view
        if v is None:
            return
        total = 0
        first_id = None
        for path in paths:
            opened = read_pdf_bytes(self, path)
            if opened is None:
                continue
            data, src = opened
            password = src.password
            src.close()
            cmd = InsertPdfCommand(data, at, None, password,
                                   label=f"Insert “{os.path.basename(path)}”")
            if v.execute(cmd):
                at += len(cmd.new_ids)
                total += len(cmd.new_ids)
                first_id = first_id or cmd.new_ids[0]
        if total:
            v.go_to_page(v.session.index_of(first_id))
            v.toast(f"Inserted {total} page{'s' if total != 1 else ''}", "Undo", v.undo)

    def insert_blank(self, after: bool = True, at_end: bool = False,
                     index: Optional[int] = None) -> bool:
        v = self.view
        if v is None:
            return False
        ref = v.current_page if index is None else index
        size = v.session.page_size(ref)
        if at_end:
            at = v.page_count
        else:
            at = ref + 1 if after else ref
        cmd = InsertBlankPageCommand(at, size)
        ok = v.execute(cmd)
        if ok:
            v.go_to_page(v.session.index_of(cmd.page_id))
        return ok

    # ----------------------------------------------------------------- save
    def save(self, view: Optional[DocumentView] = None) -> bool:
        view = view or self.view
        if view is None:
            return False
        if not view.session.path:
            return self.save_as(view)
        return self._do_save(view, None)

    def save_as(self, view: Optional[DocumentView] = None) -> bool:
        view = view or self.view
        if view is None:
            return False
        suggested = view.session.path or os.path.join(self._last_dir, view.session.display_name)
        path = prompts.ask_save_pdf(self, suggested)
        if not path:
            return False
        other = self.find_view(path)
        if other is not None and other is not view:
            prompts.show_error(self, "Cannot save",
                               f"“{os.path.basename(path)}” is open in another tab.",
                               "Close that tab first or choose a different name.")
            return False
        return self._do_save(view, path)

    def _do_save(self, view: DocumentView, path: Optional[str]) -> bool:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = view.session.save(path)
        except PdfError as exc:
            QApplication.restoreOverrideCursor()
            prompts.show_error(self, "Could not save", str(exc))
            return False
        QApplication.restoreOverrideCursor()
        self.settings.add_recent(result.path)
        self.settings.last_dir = os.path.dirname(result.path)
        self._refresh_recent()
        self._refresh_tab(view)
        self._update_history()
        view.toast(f"Saved “{os.path.basename(result.path)}”")
        return True

    # ---------------------------------------------------------------- tools
    def set_tool(self, name: str) -> None:
        v = self.view
        if v is not None:
            v.tools.set_tool(name)
            name = v.tools.active_name  # one-shot tools may keep the previous tool
        self._tool_name = name
        a = self.actions.get(f"tool_{name}")
        if a is not None and not a.isChecked():
            a.setChecked(True)
        self._sync_shapes_button(name)

    def _on_view_tool_changed(self, name: str) -> None:
        self._tool_name = name
        a = self.actions.get(f"tool_{name}")
        if a is not None and not a.isChecked():
            a.setChecked(True)
        self._sync_shapes_button(name)

    # --------------------------------------------------------------- search
    def find(self) -> None:
        v = self.view
        if v is None:
            return
        self.show_side_panel("search")
        text = v.selected_text() if hasattr(v, "selected_text") else ""
        self.search_panel.focus_field(text.strip().splitlines()[0][:120] if text.strip() else None)

    def _find_text(self, text: str) -> None:
        self.show_side_panel("search")
        self.search_panel.focus_field(text)

    def _search_step(self, step: int) -> None:
        v = self.view
        if v is None:
            return
        if v.search.options.is_empty:
            self.find()
            return
        v.search.next() if step > 0 else v.search.prev()

    # ----------------------------------------------------------------- help
    CANVAS_KEYS = [
        ("Canvas", "Pan with any tool", "Hold Space + drag  or  middle-drag"),
        ("Canvas", "Zoom around the pointer", "Ctrl + mouse wheel  or  pinch"),
        ("Canvas", "Cancel / deselect / back to Select", "Esc"),
        ("Canvas", "Delete the selected annotation", "Delete"),
        ("Canvas", "Nudge the selected annotation (10 pt with Shift)", "Arrow keys"),
        ("Canvas", "Edit the selected annotation", "Enter  or  double-click"),
        ("Canvas", "Square / circle / 45° lines while drawing", "Shift + drag"),
        ("Thumbnails", "Delete selected pages", "Delete"),
        ("Thumbnails", "Select several pages", "Ctrl+click  or  Shift+click"),
        ("Text boxes & notes", "Finish editing", "Ctrl+Enter  or  Esc"),
    ]

    def shortcut_rows(self) -> List[tuple]:
        """(menu, action, keys) for every menu action with a shortcut."""
        from .dialogs.about import shortcut_text
        rows: List[tuple] = []

        def walk(menu: QMenu, group: str) -> None:
            for a in menu.actions():
                if a.menu() is not None:
                    walk(a.menu(), group)
                    continue
                keys = shortcut_text(a.shortcuts())
                if keys:
                    rows.append((group, a.text().replace("&", "").split("\t")[0].rstrip("…"),
                                 keys))

        for top in self.menuBar().actions():
            if top.menu() is not None:
                walk(top.menu(), top.text().replace("&", ""))
        return rows + list(self.CANVAS_KEYS)

    def show_shortcuts(self) -> None:
        prompts.show_shortcuts(self, self.shortcut_rows())

    def show_integration(self) -> None:
        prompts.show_integration(self)

    def receive_files(self, paths: List[str]) -> None:
        """Files sent by a second launch (e.g. double-clicked in Explorer)."""
        for p in paths:
            if os.path.isfile(p):
                self.open_path(p)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _maybe_suggest_default(self, view: DocumentView) -> None:
        """Once, in the installed app: offer to become the default PDF app."""
        from ..integration import windows as win
        if not (win.IS_WINDOWS and win.is_installed()) or self.settings.value("integration/nudged"):
            return
        if win.is_default_pdf_app():
            return
        self.settings.set_value("integration/nudged", True)
        view.toast("Open PDF files with PDF Studio by default?", "Set default",
                   self.show_integration, msec=8000)

    # ---------------------------------------------------------------- theme
    def set_theme(self, choice: str) -> None:
        self.settings.theme = choice
        self.theme_manager.set_choice(choice)

    def toggle_theme(self) -> None:
        self.set_theme(LIGHT if self.theme_manager.theme.is_dark else DARK)

    def _sync_theme_actions(self) -> None:
        key = {SYSTEM: "theme_system", LIGHT: "theme_light", DARK: "theme_dark"}[
            self.theme_manager.choice]
        self.actions[key].setChecked(True)
        dark = self.theme_manager.theme.is_dark
        a = self.actions["toggle_theme"]
        a.setProperty("icon_name", "sun" if dark else "moon")
        a.setToolTip(f"Switch to {'light' if dark else 'dark'} theme  (Ctrl+Shift+L)")

    def _on_theme_changed(self, _theme=None) -> None:
        """Icons are pre-rendered in theme colours: refresh all of them."""
        self._sync_theme_actions()
        for a in self.actions.values():
            name = a.property("icon_name")
            if name:
                a.setIcon(icons.icon(name))
        self.recent_menu.setIcon(icons.icon("clock"))
        self.theme_menu.setIcon(icons.icon("palette"))
        for w in (self.rail, self.thumbnails, self.outline_panel, self.search_panel, self.empty,
                  self.inspector):
            if hasattr(w, "refresh_icons"):
                w.refresh_icons()
        for v in self.views():
            v.refresh_icons()
            self._refresh_tab(v)
            v.canvas.viewport().update()
        for i in range(self.tabs.count()):
            b = self.tabs.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if b is not None:
                b.setIcon(icons.icon("close"))
        self.thumbnails.list.viewport().update()
        self.update()

    # ------------------------------------------------------------ view mode
    def _preferred_mode(self) -> ViewMode:
        try:
            return ViewMode(self.settings.view_mode)
        except ValueError:
            return ViewMode.CONTINUOUS

    def set_view_mode(self, mode: ViewMode) -> None:
        v = self.view
        if v is not None:
            v.set_view_mode(mode)
        self.settings.view_mode = mode.value
        self._sync_mode_actions(mode)

    def _on_view_mode_changed(self, view: DocumentView, mode: ViewMode) -> None:
        self.settings.view_mode = mode.value
        if view is self.view:
            self._sync_mode_actions(mode)
            self._update_status()

    def _sync_mode_actions(self, mode: ViewMode) -> None:
        key = {ViewMode.CONTINUOUS: "mode_continuous", ViewMode.SINGLE: "mode_single",
               ViewMode.TWO_PAGE: "mode_two"}[mode]
        if not self.actions[key].isChecked():
            self.actions[key].setChecked(True)

    # ----------------------------------------------------------- navigation
    def go_to_page_dialog(self) -> None:
        v = self.view
        if v is not None:
            v.bar.focus_page_field()

    # ------------------------------------------------------------------- ui
    def _on_view_status(self, view: DocumentView) -> None:
        if view is self.view:
            self._update_status()

    def _update_title(self) -> None:
        v = self.view
        self.setWindowTitle(f"{v.title} — {APP_NAME}" if v else APP_NAME)

    def _update_ui(self) -> None:
        v = self.view
        has_doc = v is not None
        for key in ("close", "close_all", "zoom_in", "zoom_out", "actual_size", "fit_page",
                    "fit_width", "zoom_auto", "next_page", "prev_page", "first_page",
                    "last_page", "go_to_page"):
            self.actions[key].setEnabled(has_doc)
        for key in ("next_tab", "prev_tab"):
            self.actions[key].setEnabled(self.tabs.count() > 1)
        for a in self.tool_group.actions():
            a.setEnabled(has_doc)
        for key in ("mode_continuous", "mode_single", "mode_two"):
            self.actions[key].setEnabled(has_doc)
        if has_doc:
            self._sync_mode_actions(v.view_mode)
        for key in ("toggle_sidebar", "show_pages", "show_outline", "show_search", "find",
                    "find_next", "find_prev", "rotate_right", "rotate_left", "duplicate_page",
                    "insert_blank_after", "insert_blank_before", "insert_blank_end",
                    "insert_pdf", "extract_pages", "split_document"):
            self.actions[key].setEnabled(has_doc)
        self._update_page_actions()
        self.tab_strip.setVisible(self.tabs.count() > 0)
        self.rail.setVisible(has_doc)
        self.side.setVisible(has_doc and bool(self.rail.current))
        self.status_hint.setText(v.tools.active.hint if v else "")
        self._update_title()
        self._update_status()
        self._update_history()
        self._update_selection()

    def _update_selection(self) -> None:
        v = self.view
        self.actions["copy"].setEnabled(v is not None and v.text_selection is not None)
        self.actions["select_all"].setEnabled(v is not None)
        self.actions["delete"].setEnabled(v is not None and v.selected_annot is not None)
        self.actions["properties"].setEnabled(v is not None)

    def delete_selection(self) -> None:
        v = self.view
        if v is None:
            return
        if self.thumbnails.list.hasFocus() and self.thumbnails.selected_rows():
            self.delete_pages([v.session.page_id(r) for r in self.thumbnails.selected_rows()])
        else:
            v.delete_selected_annotation()

    def _show_properties(self) -> None:
        if self.view is not None:
            self.inspector.reveal()
            if not self.inspector.isVisible():
                self.view.toast("Select an annotation or an annotation tool to see its properties")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.stack and event.type() == QEvent.Type.Resize:
            self.inspector.reposition()
        return super().eventFilter(obj, event)

    def _update_history(self) -> None:
        v = self.view
        self.actions["save"].setEnabled(v is not None and (v.session.dirty or not v.session.path))
        self.actions["save_as"].setEnabled(v is not None)
        stack = v.session.stack if v is not None else None
        can_undo = stack is not None and stack.can_undo
        can_redo = stack is not None and stack.can_redo
        undo, redo = self.actions["undo"], self.actions["redo"]
        undo.setEnabled(can_undo)
        redo.setEnabled(can_redo)
        undo.setText(f"&Undo {stack.undo_label}" if can_undo else "&Undo")
        redo.setText(f"&Redo {stack.redo_label}" if can_redo else "&Redo")
        undo.setToolTip(f"Undo {stack.undo_label}  (Ctrl+Z)" if can_undo else "Undo  (Ctrl+Z)")
        redo.setToolTip(f"Redo {stack.redo_label}  (Ctrl+Shift+Z)" if can_redo
                        else "Redo  (Ctrl+Shift+Z)")

    def _update_status(self, *_args) -> None:
        v = self.view
        if v is None:
            for w in (self.status_page, self.status_size, self.status_zoom):
                w.setText("")
            return
        idx, n = v.current_page, v.page_count
        first, last = v.visible_page_range()
        if last > first:
            self.status_page.setText(f"Pages {first + 1}–{last + 1} of {n}")
        else:
            self.status_page.setText(f"Page {idx + 1} of {n}")
        self.status_size.setText(human_size(v.session.page_size(idx)))
        self.status_zoom.setText(zm.format_percent(v.zoom))
        self._update_page_actions()
        self.actions["prev_page"].setEnabled(idx > 0)
        self.actions["first_page"].setEnabled(idx > 0)
        self.actions["next_page"].setEnabled(idx < n - 1)
        self.actions["last_page"].setEnabled(idx < n - 1)

    def report_unexpected_error(self, exc: BaseException) -> None:
        """Non-blocking notice for bugs caught by the global exception hook."""
        v = self.view
        msg = f"Something went wrong: {exc}"
        if v is not None:
            v.toast(msg[:140], msec=6000)
        else:
            self.statusBar().showMessage(msg[:140], 6000)

    # ----------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf") and os.path.isfile(p):
                self.open_path(p)
        e.acceptProposedAction()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_settings()
        if not self.close_all():
            event.ignore()
            return
        self.render_service.shutdown()
        super().closeEvent(event)
