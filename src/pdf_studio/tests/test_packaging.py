"""Packaging helpers (Windows and macOS builds) and macOS file-open events."""

from __future__ import annotations

import plistlib

import pytest

from pdf_studio import APP_NAME, __version__
from pdf_studio.packaging import build_macos, build_windows


def test_shared_pyinstaller_args(tmp_path):
    args = build_windows.pyinstaller_args("PDF Studio", str(tmp_path / "dist"), str(tmp_path))
    assert args[0].endswith("pdf_studio_entry.py") and (tmp_path / "pdf_studio_entry.py").exists()
    assert args[args.index("--name") + 1] == "PDF Studio"
    assert "--windowed" in args and "--noupx" in args
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert {"torch", "pdf_studio.tests"} <= excluded


def test_windows_version_resource(tmp_path):
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    path = build_windows.make_version_file(str(tmp_path / "version.txt"))
    info = versioninfo.load_version_info_from_text_file(path)
    major_minor = info.ffi.fileVersionMS
    assert (major_minor >> 16, major_minor & 0xFFFF) == tuple(int(n) for n in __version__.split(".")[:2])
    assert f"'ProductVersion', '{__version__}'" in open(path, encoding="utf-8").read()


def test_macos_info_plist(tmp_path):
    path = tmp_path / "Info.plist"
    path.write_bytes(plistlib.dumps({"CFBundleIdentifier": build_macos.BUNDLE_ID,
                                     "CFBundleExecutable": APP_NAME}))
    build_macos.update_info_plist(str(path))
    info = plistlib.loads(path.read_bytes())
    assert info["CFBundleIdentifier"] == build_macos.BUNDLE_ID  # PyInstaller's keys are kept
    assert info["CFBundleShortVersionString"] == __version__
    assert info["LSMinimumSystemVersion"] == build_macos.MIN_MACOS
    doc = info["CFBundleDocumentTypes"][0]
    assert doc["LSItemContentTypes"] == ["com.adobe.pdf"] and doc["CFBundleTypeRole"] == "Editor"


class _FileOpenEvent:
    """Stands in for QFileOpenEvent, which PyQt can't construct."""

    def __init__(self, path: str):
        self._path = path

    def type(self):
        from PyQt6.QtCore import QEvent
        return QEvent.Type.FileOpen

    def file(self) -> str:
        return self._path


def test_finder_open_requests_wait_for_the_window(window, sample_pdf, plain_pdf):
    from PyQt6.QtCore import QEvent

    from pdf_studio.ui.app import FileOpenQueue
    queue = FileOpenQueue()
    # at launch macOS can send the file before the main window exists
    assert queue.handle(_FileOpenEvent(sample_pdf))
    assert queue.pending == [sample_pdf] and window.views() == []
    queue.attach(window.receive_files)
    assert queue.pending == [] and len(window.views()) == 1
    # later requests open straight away
    queue.handle(_FileOpenEvent(plain_pdf))
    assert len(window.views()) == 2
    assert not queue.handle(QEvent(QEvent.Type.User))  # other events pass through
