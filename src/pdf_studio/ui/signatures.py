"""Saved signatures (PNG files in the user's app-data folder)."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import QStandardPaths


@dataclass(frozen=True)
class SignatureChoice:
    """What the Sign tool will place: an image (signature) or a named stamp."""
    image: Optional[bytes] = None
    stamp_name: Optional[str] = None
    label: str = ""

    @property
    def is_stamp(self) -> bool:
        return self.stamp_name is not None


class SignatureStore:
    def __init__(self, directory: Optional[str] = None):
        if directory is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            directory = os.path.join(base or os.path.expanduser("~"), "signatures")
        self.directory = directory

    def _ensure(self) -> None:
        os.makedirs(self.directory, exist_ok=True)

    def list(self) -> List[str]:
        if not os.path.isdir(self.directory):
            return []
        files = [os.path.join(self.directory, f) for f in os.listdir(self.directory)
                 if f.lower().endswith(".png")]
        return sorted(files, key=os.path.getmtime, reverse=True)

    def add(self, png: bytes) -> str:
        """Save (deduplicated by content); returns the file path."""
        self._ensure()
        digest = hashlib.sha1(png).hexdigest()[:12]
        for path in self.list():
            if os.path.basename(path).endswith(f"{digest}.png"):
                os.utime(path, None)  # bump to "most recent"
                return path
        path = os.path.join(self.directory, f"signature-{int(time.time())}-{digest}.png")
        with open(path, "wb") as f:
            f.write(png)
        return path

    def remove(self, path: str) -> None:
        try:
            if os.path.dirname(os.path.abspath(path)) == os.path.abspath(self.directory):
                os.remove(path)
        except OSError:
            pass

    @staticmethod
    def read(path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None
