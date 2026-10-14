"""Typed wrapper around QSettings for user preferences."""

from __future__ import annotations

import os
from typing import Any, List, Optional

from PyQt6.QtCore import QByteArray, QSettings

from .. import APP_ID, ORG_NAME

MAX_RECENT = 12


class AppSettings:
    def __init__(self, qsettings: Optional[QSettings] = None):
        self.q = qsettings or QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                        ORG_NAME, APP_ID)

    @classmethod
    def from_file(cls, path: str) -> "AppSettings":
        return cls(QSettings(path, QSettings.Format.IniFormat))

    def sync(self) -> None:
        self.q.sync()

    @property
    def file_name(self) -> str:
        return self.q.fileName()

    # ------------------------------------------------------------ helpers
    def _get(self, key: str, default: Any = None, type_: Optional[type] = None) -> Any:
        if not self.q.contains(key):
            return default
        try:
            if type_ is not None:
                return self.q.value(key, default, type=type_)
            return self.q.value(key, default)
        except (TypeError, ValueError):
            return default

    def _set(self, key: str, value: Any) -> None:
        self.q.setValue(key, value)

    # ------------------------------------------------------------- recent
    @property
    def recent_files(self) -> List[str]:
        value = self._get("files/recent", [])
        if isinstance(value, str):
            value = [value] if value else []
        return [str(v) for v in (value or []) if v]

    def add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        norm = os.path.normcase(path)
        items = [p for p in self.recent_files if os.path.normcase(p) != norm]
        items.insert(0, path)
        self._set("files/recent", items[:MAX_RECENT])

    def remove_recent(self, path: str) -> None:
        norm = os.path.normcase(os.path.abspath(path))
        self._set("files/recent", [p for p in self.recent_files
                                   if os.path.normcase(p) != norm])

    def clear_recent(self) -> None:
        self._set("files/recent", [])

    @property
    def last_dir(self) -> str:
        d = self._get("files/last_dir", "", str)
        return d if d and os.path.isdir(d) else os.path.expanduser("~")

    @last_dir.setter
    def last_dir(self, value: str) -> None:
        self._set("files/last_dir", value)

    # ------------------------------------------------------------- window
    @property
    def window_geometry(self) -> Optional[QByteArray]:
        v = self._get("window/geometry")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._set("window/geometry", value)

    @property
    def window_state(self) -> Optional[QByteArray]:
        v = self._get("window/state")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    @window_state.setter
    def window_state(self, value: QByteArray) -> None:
        self._set("window/state", value)

    @property
    def sidebar_width(self) -> int:
        return int(self._get("window/sidebar_width", 230, int))

    @sidebar_width.setter
    def sidebar_width(self, value: int) -> None:
        self._set("window/sidebar_width", int(value))

    @property
    def properties_width(self) -> int:
        return int(self._get("window/properties_width", 270, int))

    @properties_width.setter
    def properties_width(self, value: int) -> None:
        self._set("window/properties_width", int(value))

    @property
    def sidebar_panel(self) -> str:
        """Last shown side panel key; empty string = collapsed."""
        return str(self._get("window/sidebar_panel", "pages", str))

    @sidebar_panel.setter
    def sidebar_panel(self, value: str) -> None:
        self._set("window/sidebar_panel", value)

    # --------------------------------------------------------------- view
    @property
    def theme(self) -> str:
        return str(self._get("view/theme", "system", str))

    @theme.setter
    def theme(self, value: str) -> None:
        self._set("view/theme", value)

    @property
    def view_mode(self) -> str:
        return str(self._get("view/mode", "continuous", str))

    @view_mode.setter
    def view_mode(self, value: str) -> None:
        self._set("view/mode", value)

    @property
    def zoom_mode(self) -> str:
        return str(self._get("view/zoom_mode", "auto", str))

    @zoom_mode.setter
    def zoom_mode(self, value: str) -> None:
        self._set("view/zoom_mode", value)

    # -------------------------------------------------------- annotation
    def tool_value(self, tool: str, key: str, default: Any = None) -> Any:
        return self._get(f"tools/{tool}/{key}", default)

    def set_tool_value(self, tool: str, key: str, value: Any) -> None:
        self._set(f"tools/{tool}/{key}", value)

    @property
    def author(self) -> str:
        default = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        return str(self._get("annotations/author", default, str))

    @author.setter
    def author(self, value: str) -> None:
        self._set("annotations/author", value)

    # ------------------------------------------------------------ generic
    def value(self, key: str, default: Any = None) -> Any:
        return self._get(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self._set(key, value)
