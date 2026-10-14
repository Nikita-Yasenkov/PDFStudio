"""Per-tool default styles (colour, opacity, width…), shared by all tabs and
persisted in settings."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor

from ..pdf.models import Color
from .settings import AppSettings

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "highlight": {"color": "#FFD60A", "opacity": 1.0},
    "underline": {"color": "#007AFF", "opacity": 1.0},
    "strikeout": {"color": "#FF3B30", "opacity": 1.0},
    "note": {"color": "#FFD60A", "opacity": 1.0, "icon": "Comment"},
    "free_text": {"color": "", "text_color": "#1D1D1F", "font_size": 12.0, "fill": "",
                  "opacity": 1.0},
    "ink": {"color": "#007AFF", "width": 2.0, "opacity": 1.0},
    "rectangle": {"color": "#FF3B30", "width": 2.0, "fill": "", "opacity": 1.0},
    "ellipse": {"color": "#FF3B30", "width": 2.0, "fill": "", "opacity": 1.0},
    "line": {"color": "#1D1D1F", "width": 2.0, "opacity": 1.0},
    "arrow": {"color": "#FF3B30", "width": 2.0, "opacity": 1.0},
}

_COLOR_KEYS = ("color", "fill", "text_color")


def to_rgb(value: Any) -> Optional[Color]:
    if not value:
        return None
    c = QColor(value) if not isinstance(value, QColor) else value
    if not c.isValid():
        return None
    return (round(c.redF(), 4), round(c.greenF(), 4), round(c.blueF(), 4))


def to_hex(color: Optional[Color]) -> str:
    if color is None:
        return ""
    return QColor.fromRgbF(*[max(0.0, min(1.0, float(v))) for v in color]).name()


class ToolDefaults(QObject):
    changed = pyqtSignal(str)

    def __init__(self, settings: Optional[AppSettings] = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._signature_store = None
        self._values = copy.deepcopy(DEFAULTS)
        if settings is not None:
            for tool, props in self._values.items():
                for key, default in list(props.items()):
                    v = settings.tool_value(tool, key, None)
                    if v is None:
                        continue
                    if isinstance(default, float):
                        try:
                            v = float(v)
                        except (TypeError, ValueError):
                            continue
                    props[key] = v

    def get(self, tool: str, key: str, default: Any = None) -> Any:
        return self._values.get(tool, {}).get(key, default)

    def set(self, tool: str, key: str, value: Any) -> None:
        if key in _COLOR_KEYS and not isinstance(value, str):
            value = to_hex(value) if value is not None else ""
        self._values.setdefault(tool, {})[key] = value
        if self.settings is not None:
            self.settings.set_tool_value(tool, key, value)
        self.changed.emit(tool)

    def style(self, tool: str) -> Dict[str, Any]:
        """Keyword arguments for AnnotationModel."""
        props = self._values.get(tool, {})
        out: Dict[str, Any] = {}
        for key, value in props.items():
            if key in _COLOR_KEYS:
                out[key] = to_rgb(value)
            elif key == "icon":
                out[key] = value
            else:
                out[key] = float(value)
        if self.settings is not None:
            out["author"] = self.settings.author
        return out

    @property
    def signature_store(self):
        if self._signature_store is None:
            from .signatures import SignatureStore
            self._signature_store = SignatureStore()
        return self._signature_store

    @signature_store.setter
    def signature_store(self, store) -> None:
        self._signature_store = store

    @property
    def author(self) -> str:
        return self.settings.author if self.settings is not None else ""
