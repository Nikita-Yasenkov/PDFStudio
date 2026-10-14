"""Application themes: design tokens → QPalette + stylesheet.

Neutral greys with a single blue accent. The page canvas stays paper-white
in both themes; only the chrome around it changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication

from .icons import icon_file, icons

LIGHT = "light"
DARK = "dark"
SYSTEM = "system"
THEME_CHOICES = (SYSTEM, LIGHT, DARK)


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    surface: str
    surface_alt: str
    surface_sunken: str
    canvas: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_soft: str
    accent_text: str
    danger: str
    success: str
    icon: str
    icon_disabled: str
    tooltip_bg: str
    tooltip_text: str
    page_shadow: str
    # overlays drawn on pages (RGBA)
    text_selection: str
    search_hit: str
    search_current: str
    annot_select: str

    def qcolor(self, token: str) -> QColor:
        return QColor(getattr(self, token))

    @property
    def is_dark(self) -> bool:
        return self.name == DARK


LIGHT_THEME = Theme(
    name=LIGHT,
    window="#F3F4F6", surface="#FFFFFF", surface_alt="#ECEEF2", surface_sunken="#F7F8FA",
    canvas="#E3E5E9", border="#DCDFE4", border_strong="#C5CAD3",
    text="#1D2027", text_muted="#5F6672", text_faint="#9AA0AA",
    accent="#2563EB", accent_hover="#1D4ED8", accent_soft="#E1EAFD", accent_text="#FFFFFF",
    danger="#D92D20", success="#16A34A",
    icon="#3B3F47", icon_disabled="#B4B8BF",
    tooltip_bg="#1D2027", tooltip_text="#F3F4F6",
    page_shadow="#000000",
    text_selection="#4D2563EB", search_hit="#66FACC15", search_current="#99F97316",
    annot_select="#2563EB",
)

DARK_THEME = Theme(
    name=DARK,
    window="#1B1C20", surface="#232429", surface_alt="#2D2F35", surface_sunken="#1F2024",
    canvas="#121316", border="#33363D", border_strong="#464A53",
    text="#E7E9ED", text_muted="#A1A7B1", text_faint="#6E7480",
    accent="#5B93FF", accent_hover="#7AA7FF", accent_soft="#22324F", accent_text="#0E1420",
    danger="#F26B5B", success="#4ADE80",
    icon="#D3D6DC", icon_disabled="#5D626B",
    tooltip_bg="#E7E9ED", tooltip_text="#1B1C20",
    page_shadow="#000000",
    text_selection="#595B93FF", search_hit="#66FACC15", search_current="#99F97316",
    annot_select="#5B93FF",
)

THEMES = {LIGHT: LIGHT_THEME, DARK: DARK_THEME}


def system_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return False


def resolve(choice: str) -> Theme:
    if choice == SYSTEM:
        return DARK_THEME if system_prefers_dark() else LIGHT_THEME
    return THEMES.get(choice, LIGHT_THEME)


def _palette(t: Theme) -> QPalette:
    p = QPalette()
    c = QColor
    roles = QPalette.ColorRole
    p.setColor(roles.Window, c(t.window))
    p.setColor(roles.WindowText, c(t.text))
    p.setColor(roles.Base, c(t.surface))
    p.setColor(roles.AlternateBase, c(t.surface_sunken))
    p.setColor(roles.ToolTipBase, c(t.tooltip_bg))
    p.setColor(roles.ToolTipText, c(t.tooltip_text))
    p.setColor(roles.PlaceholderText, c(t.text_faint))
    p.setColor(roles.Text, c(t.text))
    p.setColor(roles.Button, c(t.surface))
    p.setColor(roles.ButtonText, c(t.text))
    p.setColor(roles.BrightText, c("#FFFFFF"))
    p.setColor(roles.Highlight, c(t.accent))
    p.setColor(roles.HighlightedText, c(t.accent_text))
    p.setColor(roles.Link, c(t.accent))
    p.setColor(roles.Light, c(t.surface))
    p.setColor(roles.Midlight, c(t.surface_alt))
    p.setColor(roles.Mid, c(t.border_strong))
    p.setColor(roles.Dark, c(t.border_strong))
    p.setColor(roles.Shadow, c("#000000"))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c(t.text_faint))
    p.setColor(QPalette.ColorGroup.Disabled, roles.Base, c(t.surface_sunken))
    return p


def stylesheet(t: Theme) -> str:
    check = icon_file("check", t.accent_text, 12, stroke_width=3.2)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QWidget#Chrome {{ background: {t.window}; }}
QWidget#Surface, QWidget#SidePanel, QWidget#PropertiesPanel {{ background: {t.surface}; }}
QWidget#SidePanel {{ border-right: 1px solid {t.border}; }}
QWidget#PropertiesPanel {{ border-left: 1px solid {t.border}; }}
QWidget#Rail {{ background: {t.window}; border-right: 1px solid {t.border}; }}
QWidget#EmptyState {{ background: {t.canvas}; }}
QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#Separator {{ background: {t.border}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#VSeparator {{ background: {t.border}; max-width: 1px; min-width: 1px; border: none; }}

QLabel {{ color: {t.text}; background: transparent; }}
QLabel[role="muted"] {{ color: {t.text_muted}; }}
QLabel[role="faint"] {{ color: {t.text_faint}; }}
QLabel[role="empty-title"] {{ color: {t.text}; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="section"] {{ color: {t.text_muted}; font-size: 11px; font-weight: 600; }}
QLabel[role="error"] {{ color: {t.danger}; }}
QLabel[role="pill"] {{ background: {t.surface_alt}; color: {t.text_muted}; border-radius: 9px;
    padding: 1px 8px; font-size: 11px; }}

QMenuBar {{ background: {t.window}; color: {t.text}; border: none; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {t.surface_alt}; }}
QMenuBar::item:pressed {{ background: {t.accent_soft}; }}
QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    padding: 5px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 26px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QMenu::item:disabled {{ color: {t.text_faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 6px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}

QToolBar {{ background: {t.window}; border: none; spacing: 2px; padding: 4px 8px; }}
QToolBar#MainToolbar {{ border-bottom: 1px solid {t.border}; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: 6px 6px; }}
QToolButton {{ background: transparent; color: {t.text}; border: 1px solid transparent;
    border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {t.surface_alt}; }}
QToolButton:pressed {{ background: {t.border}; }}
QToolButton:checked {{ background: {t.accent_soft}; border-color: transparent; }}
QToolButton:disabled {{ color: {t.text_faint}; }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}
QToolButton::menu-button {{ border: none; width: 12px; }}
QToolButton::menu-arrow {{ width: 8px; height: 8px; }}
QToolButton#RailButton {{ border-radius: 8px; padding: 7px; margin: 2px 4px; }}
QToolButton#RailButton:checked {{ background: {t.accent_soft}; }}
QToolButton#TextButton {{ padding: 4px 8px; }}

QTabBar#DocTabs {{ background: {t.window}; }}
QTabBar#DocTabs::tab {{ background: transparent; color: {t.text_muted}; border: 1px solid transparent;
    border-bottom: none; padding: 6px 8px 6px 12px; margin: 4px 1px 0 1px; min-width: 120px;
    max-width: 240px; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar#DocTabs::tab:hover {{ background: {t.surface_alt}; color: {t.text}; }}
QTabBar#DocTabs::tab:selected {{ background: {t.surface}; color: {t.text}; border-color: {t.border}; }}
QTabBar#DocTabs::close-button {{ subcontrol-position: right; }}
QToolButton#TabClose {{ padding: 2px; border-radius: 4px; margin-left: 4px; }}
QToolButton#TabClose:hover {{ background: {t.border}; }}
QWidget#TabStrip {{ background: {t.window}; border-bottom: 1px solid {t.border}; }}

QStatusBar {{ background: {t.window}; color: {t.text_muted}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.text_muted}; padding: 0 8px; }}

QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {t.border_strong}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: 6px; padding: 5px 8px; selection-background-color: {t.accent};
    selection-color: {t.accent_text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {t.text_faint};
    background: {t.surface_sunken}; }}
QLineEdit#SearchField {{ padding: 5px 4px; border-radius: 8px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    selection-background-color: {t.accent_soft}; selection-color: {t.text}; outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ width: 16px; border: none; }}

QPushButton {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: 7px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t.surface_alt}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.text_faint}; border-color: {t.border}; }}
QPushButton:default, QPushButton[variant="primary"] {{ background: {t.accent}; color: {t.accent_text};
    border-color: {t.accent}; font-weight: 600; }}
QPushButton:default:hover, QPushButton[variant="primary"]:hover {{ background: {t.accent_hover};
    border-color: {t.accent_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {t.border}; border-color: {t.border};
    color: {t.text_faint}; }}
QPushButton[variant="danger"] {{ color: {t.danger}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {t.accent};
    padding: 2px 4px; text-align: left; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton[variant="flat"] {{ background: transparent; border: none; text-align: left;
    padding: 7px 10px; border-radius: 7px; }}
QPushButton[variant="flat"]:hover {{ background: {t.surface_alt}; }}

QCheckBox, QRadioButton {{ color: {t.text}; spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t.border_strong};
    border-radius: 4px; background: {t.surface}; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent};
    image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {t.surface_alt}; border-color: {t.border}; }}
QCheckBox::indicator:checked:disabled {{ background: {t.border_strong}; border-color: {t.border_strong}; }}
QGroupBox {{ color: {t.text}; border: 1px solid {t.border}; border-radius: 8px; margin-top: 14px;
    padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.text_muted}; }}

QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background: {t.surface}; color: {t.text}; border: none; outline: 0; }}
QTreeView::item, QListView::item {{ padding: 4px 4px; border-radius: 5px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {t.surface_alt}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QHeaderView::section {{ background: {t.surface}; color: {t.text_muted}; border: none;
    border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600; }}
QTableView {{ gridline-color: {t.border}; }}

QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.surface}; border: 1px solid {t.border_strong};
    width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ border-color: {t.accent}; }}

QProgressBar {{ background: {t.surface_alt}; border: none; border-radius: 2px; max-height: 3px;
    color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 2px; }}

QToolTip {{ background: {t.tooltip_bg}; color: {t.tooltip_text}; border: none; padding: 5px 8px;
    border-radius: 6px; }}

QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 8px; top: -1px; background: {t.surface}; }}
QTabWidget > QTabBar::tab {{ background: transparent; color: {t.text_muted}; padding: 7px 14px;
    border: none; border-bottom: 2px solid transparent; }}
QTabWidget > QTabBar::tab:selected {{ color: {t.text}; border-bottom-color: {t.accent}; }}
QTabWidget > QTabBar::tab:hover {{ color: {t.text}; }}

QWidget#FloatingBar {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
QWidget#FloatingBar QToolButton {{ padding: 3px; border-radius: 6px; }}
QWidget#FloatingBar QLineEdit {{ border: 1px solid {t.border}; padding: 2px 4px; border-radius: 5px;
    background: {t.surface_sunken}; }}
QFrame#Inspector {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#Inspector QToolButton {{ border-radius: 11px; }}
QWidget#Toast {{ background: {t.tooltip_bg}; border-radius: 8px; }}
QWidget#Toast QLabel {{ color: {t.tooltip_text}; }}
QWidget#Toast QPushButton {{ background: transparent; border: none; color: {t.accent if t.is_dark else '#8AB4FF'};
    font-weight: 600; padding: 2px 6px; }}
QWidget#DropOverlay {{ background: transparent; }}
"""


class ThemeManager(QObject):
    """Applies a theme choice and follows the OS when the choice is ``system``."""

    changed = pyqtSignal(object)  # Theme

    def __init__(self, app: QApplication, choice: str = SYSTEM):
        super().__init__(app)
        self._app = app
        self._choice = choice if choice in THEME_CHOICES else SYSTEM
        self._theme: Optional[Theme] = None
        self._applying = False
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_change)
        except AttributeError:  # pragma: no cover - Qt < 6.5
            pass

    @property
    def choice(self) -> str:
        return self._choice

    @property
    def theme(self) -> Theme:
        return self._theme or resolve(self._choice)

    def set_choice(self, choice: str) -> None:
        self._choice = choice if choice in THEME_CHOICES else SYSTEM
        self.apply()

    def apply(self) -> Theme:
        if self._applying:
            return self.theme
        self._applying = True
        try:
            hints = QGuiApplication.styleHints()
            if self._choice == SYSTEM:
                _set_color_scheme(hints, None)  # follow the OS again
            t = resolve(self._choice)
            if self._choice != SYSTEM:
                # also gives native dark/light title bars on Windows
                _set_color_scheme(hints, Qt.ColorScheme.Dark if t.is_dark else Qt.ColorScheme.Light)
            self._theme = t
            apply_theme(self._app, t)
        finally:
            self._applying = False
        self.changed.emit(t)
        return t

    def _on_system_change(self, *_args) -> None:
        if self._choice == SYSTEM and not self._applying:
            self.apply()


def _set_color_scheme(hints, scheme) -> None:
    """Qt ≥ 6.8: pin (or with None, release) the application colour scheme."""
    try:
        if scheme is None:
            if hasattr(hints, "unsetColorScheme"):
                hints.unsetColorScheme()
            else:
                hints.setColorScheme(Qt.ColorScheme.Unknown)
        else:
            hints.setColorScheme(scheme)
    except AttributeError:  # pragma: no cover - older Qt
        pass


_current: Theme = LIGHT_THEME


def current() -> Theme:
    return _current


def apply_theme(app: QApplication, t: Theme) -> None:
    global _current
    _current = t
    app.setStyle("Fusion")
    app.setPalette(_palette(t))
    icons.set_colors(QColor(t.icon), QColor(t.icon_disabled), QColor(t.accent))
    app.setStyleSheet(stylesheet(t))
