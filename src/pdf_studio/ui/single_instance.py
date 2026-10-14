"""Single running instance: files opened from Explorer while PDF Studio is
already running are handed to that window (opened as tabs) instead of
starting a second copy."""

from __future__ import annotations

import getpass
import json
import os
import sys
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"PDFStudio-{user}"


def send_to_running(paths: List[str], name: Optional[str] = None, timeout_ms: int = 800) -> bool:
    """Hand ``paths`` to a running instance. True if one received them."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    if sys.platform == "win32":
        try:  # let the running instance bring its window to the front
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass
    payload = json.dumps({"open": [os.path.abspath(p) for p in paths]}).encode("utf-8")
    sock.write(payload)
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(timeout_ms)
    return True


class InstanceServer(QObject):
    """Listens for paths sent by later launches."""

    filesReceived = pyqtSignal(list)   # may be empty: "just bring the window up"

    def __init__(self, name: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(self.name):
            # a crashed instance can leave a stale endpoint behind
            QLocalServer.removeServer(self.name)
            self.server.listen(self.name)
        self.server.newConnection.connect(self._on_connection)
        self._buffers = {}

    @property
    def listening(self) -> bool:
        return self.server.isListening()

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._buffers[s].extend(bytes(s.readAll())))
            sock.disconnected.connect(lambda s=sock: self._finish(s))

    def _finish(self, sock: QLocalSocket) -> None:
        data = bytes(self._buffers.pop(sock, b"")) + bytes(sock.readAll())
        sock.deleteLater()
        try:
            paths = json.loads(data.decode("utf-8")).get("open", [])
        except (ValueError, AttributeError):
            return
        self.filesReceived.emit([p for p in paths if isinstance(p, str)])

    def close(self) -> None:
        self.server.close()
