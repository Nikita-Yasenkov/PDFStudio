"""Help → Check for Updates: what's new in the latest release, and get it.

Packaged Windows builds install the update themselves: the dialog downloads
and unpacks it, and **Restart Now** hands over to the new version (see
``integration/updater.py``). Elsewhere the file is saved to Downloads.
"""

from __future__ import annotations

import os
import sys
import zipfile
from typing import Callable, Optional, Tuple

from PyQt6.QtCore import QStandardPaths, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QProgressBar, QPushButton,
                             QTextBrowser, QVBoxLayout, QWidget)

from ... import APP_NAME, __version__
from ...core import updates
from ...integration import updater
from ...integration import windows as win
from ..qtutil import label, repolish, reveal_in_folder
from ..updates import Download, UpdateChecker, network_manager

MB = 1024 * 1024


def megabytes(size: int) -> str:
    return f"{size / MB:.0f} MB" if size >= 10 * MB else f"{size / MB:.1f} MB"


def downloads_folder() -> str:
    return (QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
            or os.path.expanduser("~"))


def in_place_folder() -> Optional[str]:
    """The app folder this copy can replace with an update: packaged Windows
    builds whose folder the user may rename. None: download only."""
    exe = win.current_exe()
    if not (win.IS_WINDOWS and exe):
        return None
    app_dir = os.path.dirname(exe)
    return app_dir if updater.can_replace(app_dir) else None


class UpdateDialog(QDialog):
    """Non-modal, so you can keep working while it checks and downloads.
    Closing it during a download only hides it: the download carries on and
    the dialog comes back when it's done."""

    #: Restart Now: the unpacked new exe, and the app folder it replaces
    restartRequested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget, url: Optional[str] = None, folder: Optional[str] = None,
                 current: str = __version__, asset_name: Optional[str] = None,
                 app_dir: Optional[str] = None, staging: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.setMinimumWidth(480)
        self.url = url or updates.LATEST_RELEASE_URL
        self.folder = folder or downloads_folder()
        self.current = current
        self.asset_name = asset_name if asset_name is not None else updates.asset_name()
        self.app_dir = app_dir if app_dir is not None else in_place_folder()
        self.staging = staging or updater.staging_root()
        self.network = network_manager(self)
        self.checker: Optional[UpdateChecker] = None
        self.download: Optional[Download] = None
        self.release: Optional[updates.Release] = None
        self.saved: Optional[str] = None
        self.new_exe: Optional[str] = None
        self.state = ""
        self._primary_slot: Optional[Callable[[], None]] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        self.heading = label("", "heading")
        lay.addWidget(self.heading)
        self.detail = label("", "muted", wrap=True)
        lay.addWidget(self.detail)
        self.notes = QTextBrowser(self)
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(170)
        lay.addWidget(self.notes, 1)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.status = label("", "faint")
        lay.addWidget(self.status)
        buttons = QHBoxLayout()
        self.page_btn = QPushButton("Release Page", self)
        self.page_btn.clicked.connect(self.open_release_page)
        buttons.addWidget(self.page_btn)
        buttons.addStretch(1)
        self.primary = QPushButton(self)
        self.primary.clicked.connect(self._on_primary)
        buttons.addWidget(self.primary)
        self.close_btn = QPushButton("Close", self)
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        lay.addStretch(0)  # spare height goes here when the notes are hidden
        lay.addLayout(buttons)
        for b in (self.page_btn, self.primary, self.close_btn):
            b.setAutoDefault(False)  # Enter and the accent color go to the primary action only

    @property
    def busy(self) -> bool:
        return any(job is not None and job.running for job in (self.checker, self.download))

    # --------------------------------------------------------------- states
    def _show(self, state: str, heading: str, detail: str = "", notes: str = "",
              primary: Optional[Tuple[str, Callable[[], None]]] = None, emphasis: bool = True,
              page: bool = False, progress: bool = False, close: str = "Close") -> None:
        self.state = state
        self.heading.setText(heading)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.notes.setMarkdown(notes)
        self.notes.setVisible(bool(notes))
        self.bar.setVisible(progress)
        self.status.setText("")
        self.status.setVisible(False)  # shown by _progress
        text, self._primary_slot = primary or ("", None)
        self.primary.setText(text)
        self.primary.setVisible(primary is not None)
        self.primary.setProperty("variant", "primary" if emphasis else None)
        self.primary.setDefault(primary is not None and emphasis)
        repolish(self.primary)
        self.page_btn.setVisible(page)
        self.close_btn.setText(close)
        self.adjustSize()

    def check(self) -> None:
        """Ask GitHub for the latest release, unless a check or a download is running."""
        if self.busy:
            return
        self.release = None
        self._show("checking", "Checking for updates…",
                   f"Asking GitHub for the latest release of {APP_NAME}.",
                   progress=True, close="Cancel")
        self.bar.setRange(0, 0)  # busy
        if self.checker is not None:
            self.checker.deleteLater()
        self.checker = UpdateChecker(self.network, self.url, self)
        self.checker.found.connect(self._found)
        self.checker.failed.connect(
            lambda text: self._show("check_failed", "Couldn't check for updates", text,
                                    primary=("Try Again", self.check), page=True))
        self.checker.start()

    def _found(self, release: updates.Release) -> None:
        self.release = release
        if not updates.is_newer(release.version, self.current):
            detail = (f"You have {self.current}; the latest release is {release.version}."
                      if updates.is_newer(self.current, release.version) else
                      f"{APP_NAME} {self.current} is the latest release.")
            self._show("up_to_date", "You have the latest version", detail)
            return
        asset = release.asset(self.asset_name)
        verb = "Update" if self.app_dir else "Download"
        primary = ((f"{verb} ({megabytes(asset.size)})", self.start_download) if asset else
                   ("Open Release Page", self.open_release_page))
        self._show("available", f"{APP_NAME} {release.version} is available",
                   f"You have version {self.current}." + (" What's new:" if release.notes else ""),
                   notes=release.notes, primary=primary, page=asset is not None, close="Not Now")

    def start_download(self) -> None:
        asset = self.release.asset(self.asset_name) if self.release else None
        if asset is None or self.busy:
            return
        if self.app_dir:  # unpacked and installed from there
            folder = os.path.join(self.staging, self.release.version)
        else:
            folder = self.folder
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self._download_failed(f"Couldn't save the download: {exc.strerror or exc}")
            return
        path = (os.path.join(folder, asset.name) if self.app_dir else updates.unique_path(
            folder, updates.download_name(asset.name, self.release.version)))
        if self.download is not None:
            self.download.deleteLater()
        self.download = Download(self.network, asset, path, self)
        self.download.progress.connect(self._progress)
        self.download.finished.connect(self._downloaded)
        self.download.failed.connect(self._download_failed)
        self._show("downloading", f"Downloading {APP_NAME} {self.release.version}…",
                   "You can keep working. If you close this window, the download carries on "
                   "and the window comes back when it's done.",
                   primary=("Cancel Download", self.cancel_download), emphasis=False,
                   progress=True, close="Hide")
        self.bar.setRange(0, 1000)
        self._progress(0, asset.size)
        self.download.start()

    def _progress(self, received: int, total: int) -> None:
        self.status.setVisible(True)
        if total > 0:
            self.bar.setValue(min(1000, received * 1000 // total))
            self.status.setText(f"{received / MB:.1f} of {total / MB:.1f} MB")
        else:
            self.status.setText(f"{received / MB:.1f} MB")

    def cancel_download(self) -> None:
        if self.download is not None:
            self.download.abort()
        self._found(self.release)

    def _download_failed(self, text: str, heading: str = "Download failed") -> None:
        self._show("download_failed", heading, text,
                   primary=("Try Again", self.start_download), page=True)
        self._bring_back()

    def _downloaded(self, path: str) -> None:
        self.saved = path
        if self.app_dir:
            self._prepare(path)
            return
        folder = os.path.dirname(path)
        where = ("your Downloads folder"
                 if os.path.normcase(folder) == os.path.normcase(downloads_folder())
                 else os.path.normpath(folder))
        if sys.platform == "darwin":
            steps = ("To update, open it, quit PDF Studio, then drag PDF Studio to Applications "
                     "and choose Replace.")
            primary = ("Open Disk Image", self.open_saved)
        else:
            steps = ("To update, close PDF Studio, unzip the file and run PDFStudio.exe from "
                     "the unzipped folder.")
            from ...integration import windows as win
            if win.IS_WINDOWS and win.is_installed():
                steps += (f" Then choose Help → {APP_NAME} on This Computer → Install for this "
                          "user to replace the installed copy.")
            primary = ("Show in Folder", self.reveal_saved)
        self._show("downloaded", "Download complete",
                   f"{os.path.basename(path)} is in {where}. {steps} Your settings and saved "
                   "signatures are kept.", primary=primary)
        self._bring_back()

    def _prepare(self, path: str) -> None:
        """Unpack the download and check it may replace this copy."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_exe = os.path.join(updater.unpack(path, os.path.join(os.path.dirname(path), "app")),
                                   updater.EXE_NAME)
            current = win.signer(os.path.join(self.app_dir, updater.EXE_NAME))
            trusted = updater.same_publisher(current, win.signer(new_exe))
        except (OSError, zipfile.BadZipFile) as exc:
            self._download_failed(f"The download couldn't be unpacked: {exc}",
                                  "Couldn't install the update")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not trusted:
            self._download_failed(f"The update isn't signed by the same publisher as this copy "
                                  f"of {APP_NAME}, so it wasn't installed.",
                                  "Couldn't install the update")
            return
        self.new_exe = new_exe
        self._show("ready", f"{APP_NAME} {self.release.version} is ready to install",
                   f"{APP_NAME} will close, install the update and open again with your "
                   "documents. You'll be asked to save any unsaved changes first.",
                   primary=("Restart Now", self.restart), close="Later")
        self._bring_back()

    def restart(self) -> None:
        if self.new_exe and self.app_dir:
            self.restartRequested.emit(self.new_exe, self.app_dir)

    # -------------------------------------------------------------- actions
    def _on_primary(self) -> None:
        if self._primary_slot is not None:
            self._primary_slot()

    def _bring_back(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def open_release_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.release.page_url if self.release
                                      else updates.RELEASES_URL))

    def open_saved(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.saved))

    def reveal_saved(self) -> None:
        reveal_in_folder(self.saved)

    def reject(self) -> None:
        if self.checker is not None and self.checker.running:
            self.checker.abort()  # a download, though, carries on
        super().reject()
