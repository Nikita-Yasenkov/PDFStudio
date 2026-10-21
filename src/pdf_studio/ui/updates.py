"""Network side of Help → Check for Updates.

:class:`UpdateChecker` asks GitHub for the latest release and
:class:`Download` saves one of its files, both through Qt's network module:
the system's certificates and proxy settings, and no thread of our own. This
is the only code in PDF Studio that goes online, and only when the user asks.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional, Tuple

from PyQt6.QtCore import QCoreApplication, QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import (QNetworkAccessManager, QNetworkProxyFactory, QNetworkReply,
                             QNetworkRequest, QSslConfiguration, QSslSocket)

from .. import APP_ID, __version__
from ..core.updates import LATEST_RELEASE_URL, Asset, parse_release

#: give up when no data arrives for this long
TIMEOUT_MS = 20_000


def network_manager(parent: QObject) -> QNetworkAccessManager:
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    return QNetworkAccessManager(parent)


def https_status() -> Tuple[bool, str]:
    """Whether this build can make HTTPS requests: a TLS backend and the
    system's root certificates. Offline, for the bundle's self-test."""
    if not QSslSocket.supportsSsl():
        return False, "no TLS backend"
    count = len(QSslConfiguration.systemCaCertificates())
    return count > 0, f"{QSslSocket.activeBackend()} backend, {count} root certificates"


def _request(url: str) -> QNetworkRequest:
    req = QNetworkRequest(QUrl(url))
    req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"{APP_ID}/{__version__}")
    req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                     QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    req.setTransferTimeout(TIMEOUT_MS)
    return req


def error_text(reply: QNetworkReply) -> str:
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if status in (403, 429):
        return "GitHub is limiting requests from your network right now. Try again in an hour."
    if status == 404:
        return "GitHub has no release of PDF Studio yet."
    if status:
        return f"GitHub answered with an error (HTTP {status}). Try again later."
    err = QNetworkReply.NetworkError
    if reply.error() in (err.OperationCanceledError, err.TimeoutError):
        return "GitHub took too long to answer. Try again later."
    if reply.error() == err.SslHandshakeFailedError:
        return "Couldn't make a secure connection to GitHub."
    return "Couldn't connect to GitHub. Check your internet connection and try again."


class UpdateChecker(QObject):
    """One request for the latest release: ``found(Release)`` or ``failed(str)``."""

    found = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, network: QNetworkAccessManager, url: str = LATEST_RELEASE_URL,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.network = network
        self.url = url
        self.reply: Optional[QNetworkReply] = None

    @property
    def running(self) -> bool:
        return self.reply is not None

    def start(self) -> None:
        req = _request(self.url)
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self.reply = self.network.get(req)
        reply.finished.connect(lambda: self._finished(reply))

    def abort(self) -> None:
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def _finished(self, reply: QNetworkReply) -> None:
        if reply is not self.reply:  # aborted
            return
        self.reply = None
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(error_text(reply))
            return
        try:
            release = parse_release(json.loads(bytes(reply.readAll())))
        except ValueError:  # includes malformed JSON
            self.failed.emit("GitHub's answer couldn't be read. Try again later.")
            return
        self.found.emit(release)


class Download(QObject):
    """Save one release file: it's written to ``<path>.part`` as it arrives,
    checked against the SHA-256 GitHub reported, then renamed to ``path``."""

    progress = pyqtSignal(int, int)  # bytes received, total
    finished = pyqtSignal(str)  # the saved file
    failed = pyqtSignal(str)

    def __init__(self, network: QNetworkAccessManager, asset: Asset, path: str,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.network = network
        self.asset = asset
        self.path = path
        self.part = path + ".part"
        self.reply: Optional[QNetworkReply] = None
        self._file = None
        self._hash = hashlib.sha256()

    @property
    def running(self) -> bool:
        return self.reply is not None

    def start(self) -> None:
        try:
            self._file = open(self.part, "wb")
        except OSError as exc:
            self.failed.emit(f"Couldn't save the download: {exc.strerror or exc}")
            return
        reply = self.reply = self.network.get(_request(self.asset.url))
        reply.readyRead.connect(lambda: self._read(reply))
        reply.downloadProgress.connect(
            lambda got, total: self.progress.emit(got, total if total > 0 else self.asset.size))
        reply.finished.connect(lambda: self._finished(reply))
        QCoreApplication.instance().aboutToQuit.connect(self.abort)  # leave no .part behind

    def abort(self) -> None:
        reply, self.reply = self.reply, None
        if reply is None:
            return
        self._unhook()
        reply.abort()
        reply.deleteLater()
        self._discard()

    def _unhook(self) -> None:
        try:
            QCoreApplication.instance().aboutToQuit.disconnect(self.abort)
        except TypeError:
            pass

    def _read(self, reply: QNetworkReply) -> None:
        if reply is not self.reply or self._file is None:
            return
        data = bytes(reply.readAll())
        try:
            self._file.write(data)
        except OSError as exc:
            self.abort()
            self.failed.emit(f"Couldn't save the download: {exc.strerror or exc}")
            return
        self._hash.update(data)

    def _finished(self, reply: QNetworkReply) -> None:
        if reply is not self.reply:  # aborted
            return
        self._read(reply)
        if reply is not self.reply:  # the last write failed
            return
        self.reply = None
        reply.deleteLater()
        self._unhook()
        self._file.close()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._discard()
            self.failed.emit(error_text(reply))
            return
        if self.asset.sha256:
            damaged = self._hash.hexdigest() != self.asset.sha256
        else:  # no digest reported (older uploads): at least check the size
            damaged = self.asset.size > 0 and os.path.getsize(self.part) != self.asset.size
        if damaged:
            self._discard()
            self.failed.emit("The download was damaged on the way (its checksum doesn't "
                             "match). Try again.")
            return
        try:
            os.replace(self.part, self.path)
        except OSError as exc:
            self._discard()
            self.failed.emit(f"Couldn't save the download: {exc.strerror or exc}")
            return
        self.finished.emit(self.path)

    def _discard(self) -> None:
        if self._file is not None:
            self._file.close()
        try:
            os.remove(self.part)
        except OSError:
            pass
