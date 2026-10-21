"""Inline SVG icon set (24×24, stroke-based, themed at runtime).

Icons are rendered with ``QSvgRenderer`` into pixmaps for the normal,
disabled and active (checked) states, so they follow the current theme.
Shapes follow the Lucide visual language (ISC licensed).
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_SVG_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
             'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">')

# Paths use "currentColor" for strokes and "FILL" for solid accents.
ICONS: Dict[str, str] = {
    "open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "save": '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    "save-as": '<path d="M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h10.2a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V11"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/><path d="M7 21v-6a1 1 0 0 1 1-1h5"/><path d="M21.4 15.6a1.4 1.4 0 0 0-2-2L15 18l-.6 2.6L17 20z"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/>',
    "select": '<path d="M4.04 4.69a.5.5 0 0 1 .65-.65l16 6.5a.5.5 0 0 1-.06.95l-6.13 1.58a2 2 0 0 0-1.43 1.43l-1.58 6.13a.5.5 0 0 1-.95.06z"/>',
    "text-select": '<path d="M17 22h-1a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4h1"/><path d="M7 22h1a4 4 0 0 0 4-4v-1"/><path d="M7 2h1a4 4 0 0 1 4 4v1"/>',
    "hand": '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2"/><path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
    "highlight": '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
    "underline": '<path d="M6 4v6a6 6 0 0 0 12 0V4"/><path d="M4 20h16"/>',
    "strikeout": '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><path d="M4 12h16"/>',
    "note": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 8h8"/><path d="M8 12h5"/>',
    "free-text": '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "ink": '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/><path d="m15 5 4 4"/>',
    "shapes": '<path d="M8.3 10a.7.7 0 0 1-.63-1.08L11.4 3a.7.7 0 0 1 1.2-.04L16.3 8.9a.7.7 0 0 1-.57 1.1Z"/><rect x="3" y="14" width="7" height="7" rx="1"/><circle cx="17.5" cy="17.5" r="3.5"/>',
    "rectangle": '<rect width="18" height="14" x="3" y="5" rx="2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="9.5" ry="7.5"/>',
    "line": '<path d="M5 19 19 5"/>',
    "arrow": '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    "edit-text": '<path d="M4 7V4h12v3"/><path d="M10 4v16"/><path d="M7 20h5"/><path d="M21.4 12.6a1.4 1.4 0 0 0-2-2L14 16l-.6 2.6L16 18z"/>',
    "redact": '<rect x="3" y="9" width="18" height="6" rx="1" fill="{c}"/><path d="M3 5h11"/><path d="M3 19h15"/>',
    "image": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
    "signature": '<path d="m21 17-2.16-1.87a.5.5 0 0 0-.84.37v.5a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1c0-2.55-3.99-3.97-8.5-4a1 1 0 0 0 0 5c4.15 0 4.75-11.3 5.71-13.5a2.5 2.5 0 1 1 3.31 3.28"/><path d="M3 21h18"/>',
    "stamp": '<path d="M5 22h14"/><path d="M19.27 13.73A2.5 2.5 0 0 0 17.5 13h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5c0-.66-.26-1.3-.73-1.77Z"/><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-3-3c-1.69 0-3 1-3 3 0 2 1 2 1 3.5V13"/>',
    "zoom-in": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M11 8v6"/><path d="M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M8 11h6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "fit-width": '<path d="M3 4v16"/><path d="M21 4v16"/><path d="m7 9-3 3 3 3"/><path d="m17 9 3 3-3 3"/><path d="M4 12h16"/>',
    "fit-page": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/><rect x="8" y="7" width="8" height="10" rx="1"/>',
    "actual-size": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 9.5 9.5 8v8"/><path d="M14.5 9.5 16 8v8"/><path d="M12 10.5v.01"/><path d="M12 14v.01"/>',
    "rotate-cw": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    "rotate-ccw": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "delete": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "duplicate": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "page-add": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 18v-6"/>',
    "page-import": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M12 11v6"/><path d="m9 14 3 3 3-3"/>',
    "extract": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h7"/><path d="M14 2v4a2 2 0 0 0 2 2h4v5"/><path d="M15 18h7"/><path d="m19 15 3 3-3 3"/>',
    "split": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "merge": '<path d="M20 7h-3a2 2 0 0 1-2-2V2"/><path d="M9 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7l4 4v10a2 2 0 0 1-2 2Z"/><path d="M3 7.6v12.8A1.6 1.6 0 0 0 4.6 22h9.8"/>',
    "pages": '<rect x="3" y="2" width="8" height="10" rx="1.5"/><rect x="13" y="2" width="8" height="10" rx="1.5"/><rect x="3" y="14" width="8" height="8" rx="1.5"/><rect x="13" y="14" width="8" height="8" rx="1.5"/>',
    "outline": '<path d="M21 12h-8"/><path d="M21 6H8"/><path d="M21 18h-8"/><path d="M3 6v4c0 1.1.9 2 2 2h3"/><path d="M3 10v6c0 1.1.9 2 2 2h3"/>',
    "bookmark": '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "first-page": '<path d="m17 18-6-6 6-6"/><path d="M7 6v12"/>',
    "last-page": '<path d="m7 18 6-6-6-6"/><path d="M17 6v12"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "minus": '<path d="M5 12h14"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "monitor": '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "keyboard": '<path d="M10 8h.01"/><path d="M12 12h.01"/><path d="M14 8h.01"/><path d="M16 12h.01"/><path d="M18 8h.01"/><path d="M6 8h.01"/><path d="M7 16h10"/><path d="M8 12h.01"/><rect width="20" height="16" x="2" y="4" rx="2"/>',
    "view-single": '<rect x="6" y="3" width="12" height="18" rx="1.5"/>',
    "view-continuous": '<rect x="6" y="1.5" width="12" height="9.5" rx="1.5"/><rect x="6" y="13" width="12" height="9.5" rx="1.5"/>',
    "view-two": '<rect x="2" y="4" width="9" height="16" rx="1.5"/><rect x="13" y="4" width="9" height="16" rx="1.5"/>',
    "panel-left": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
    "panel-right": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "eye": '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "eraser": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
    "palette": '<circle cx="13.5" cy="6.5" r=".5" fill="{c}"/><circle cx="17.5" cy="10.5" r=".5" fill="{c}"/><circle cx="8.5" cy="7.5" r=".5" fill="{c}"/><circle cx="6.5" cy="12.5" r=".5" fill="{c}"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.56-2.5 5.56-5.55C21.96 6.01 17.46 2 12 2z"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "fullscreen": '<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>',
    "dot": '<circle cx="12" cy="12" r="4" fill="{c}"/>',
}

# Brand mark (app/window icon): a document with a folded corner on an
# accent tile. Uses fixed colours so it reads on light and dark title bars.
APP_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect x="2" y="2" width="60" height="60" rx="14" fill="#2563EB"/>'
    '<path d="M21 13h15l10 10v26a3 3 0 0 1-3 3H21a3 3 0 0 1-3-3V16a3 3 0 0 1 3-3z" fill="#FFFFFF"/>'
    '<path d="M36 13v8a2 2 0 0 0 2 2h8" fill="#C9D8FB"/>'
    '<path d="M24 32h16M24 38h16M24 44h10" stroke="#2563EB" stroke-width="2.6" stroke-linecap="round"/>'
    '</svg>'
)


class IconFactory:
    """Builds themed QIcons from the SVG set, with caching."""

    def __init__(self) -> None:
        self._normal = QColor("#3A3D44")
        self._disabled = QColor("#A9ACB3")
        self._active = QColor("#2563EB")
        self._cache: Dict[Tuple[str, str, str, str], QIcon] = {}

    def set_colors(self, normal: QColor, disabled: QColor, active: QColor) -> None:
        self._normal, self._disabled, self._active = QColor(normal), QColor(disabled), QColor(active)
        self._cache.clear()

    @staticmethod
    def svg(name: str, color: QColor, stroke_width: float = 1.8) -> bytes:
        body = ICONS[name].replace("{c}", color.name())
        return (_SVG_HEAD.format(c=color.name(), w=stroke_width) + body + "</svg>").encode()

    @staticmethod
    def render(svg: bytes, size: int, dpr: float = 2.0) -> QPixmap:
        px = int(round(size * dpr))
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(svg))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p, QRectF(0, 0, px, px))
        p.end()
        pm.setDevicePixelRatio(dpr)
        return pm

    def pixmap(self, name: str, size: int = 20, color: Optional[QColor] = None,
               dpr: float = 2.0) -> QPixmap:
        return self.render(self.svg(name, color or self._normal), size, dpr)

    def icon(self, name: str, color: Optional[QColor] = None) -> QIcon:
        normal = QColor(color) if color is not None else self._normal
        key = (name, normal.name(), self._disabled.name(), self._active.name())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        icon = QIcon()
        for size in (16, 20, 24, 32):
            icon.addPixmap(self.pixmap(name, size, normal), QIcon.Mode.Normal, QIcon.State.Off)
            icon.addPixmap(self.pixmap(name, size, self._disabled), QIcon.Mode.Disabled, QIcon.State.Off)
            icon.addPixmap(self.pixmap(name, size, self._disabled), QIcon.Mode.Disabled, QIcon.State.On)
            icon.addPixmap(self.pixmap(name, size, self._active), QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(self.pixmap(name, size, self._active), QIcon.Mode.Active, QIcon.State.On)
            icon.addPixmap(self.pixmap(name, size, normal), QIcon.Mode.Active, QIcon.State.Off)
            icon.addPixmap(self.pixmap(name, size, normal), QIcon.Mode.Selected, QIcon.State.Off)
        self._cache[key] = icon
        return icon


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(APP_ICON_SVG.encode())).render(p, QRectF(0, 0, size, size))
        p.end()
        icon.addPixmap(pm)
    return icon


def app_logo_pixmap(size: int, dpr: float = 2.0) -> QPixmap:
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(APP_ICON_SVG.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon_file(name: str, color: str, size: int, stroke_width: float = 1.8) -> str:
    """PNG of an icon on disk, for stylesheet ``image: url(...)`` properties.

    Written once per name/color/size (with an ``@2x`` copy that Qt picks on
    high-DPI screens) to a temp folder; returns a forward-slash path.
    """
    folder = os.path.join(tempfile.gettempdir(), "pdf-studio-icons")
    base = os.path.join(folder, f"{name}-{color.lstrip('#').lower()}-{size}")
    path = base + ".png"
    if not (os.path.exists(path) and os.path.exists(base + "@2x.png")):
        os.makedirs(folder, exist_ok=True)
        svg = QByteArray(IconFactory.svg(name, QColor(color), stroke_width))
        for scale, target in ((1, path), (2, base + "@2x.png")):
            img = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            QSvgRenderer(svg).render(p, QRectF(0, 0, img.width(), img.height()))
            p.end()
            img.save(target)
    return path.replace("\\", "/")


#: Shared factory; ``theme.apply_theme`` recolours it.
icons = IconFactory()


def icon(name: str) -> QIcon:
    return icons.icon(name)


ICON_SIZE = QSize(20, 20)
