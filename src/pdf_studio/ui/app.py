"""Application bootstrap: QApplication, theme, main window."""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QApplication

from .. import APP_ID, APP_NAME, ORG_NAME, __version__


def create_application(argv: Optional[Sequence[str]] = None) -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app  # type: ignore[return-value]
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    if sys.platform == "win32":
        font = QFont("Segoe UI", 9)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        app.setFont(font)
    return app


def install_exception_hook() -> None:
    """Log unexpected errors and keep running.

    PyQt6 aborts the process on an exception escaping a slot unless a custom
    ``sys.excepthook`` is installed; a desktop editor should never lose the
    user's work to a bug in one handler.
    """
    import logging
    import traceback

    def hook(exc_type, exc, tb):
        logging.getLogger("pdf_studio").error("Unhandled error", exc_info=(exc_type, exc, tb))
        traceback.print_exception(exc_type, exc, tb)
        app = QApplication.instance()
        window = getattr(app, "main_window", None) if app else None
        if window is not None and hasattr(window, "report_unexpected_error"):
            try:
                window.report_unexpected_error(exc)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = hook


def smoke(paths: Sequence[str], log: Optional[str] = None) -> int:
    """Headless self-check: open each PDF in a real main window, render its
    first page, then exit. Returns 0 on success (used to verify packaged
    builds without any interaction)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: List[str] = []
    code = 0
    app = create_application(["pdf-studio", "-platform", "offscreen"])
    from PyQt6.QtCore import QEventLoop, QTimer

    from ..pdf import PdfDocument, PdfError, library_versions
    from .main_window import MainWindow
    from .settings import AppSettings
    import tempfile

    lines.append(f"PDF Studio {__version__} · PyMuPDF {library_versions()['PyMuPDF']}")
    settings = AppSettings.from_file(os.path.join(tempfile.mkdtemp(), "smoke.ini"))
    window = MainWindow(settings=settings)
    window.resize(1000, 700)
    window.show()
    for path in paths:
        try:
            doc = PdfDocument.open(path)
            img = doc.render(0, 0.5)
            doc.close()
            ok = window.open_path(path)
            loop = QEventLoop()
            QTimer.singleShot(400, loop.quit)
            loop.exec()
            view = window.view
            if not ok or view is None:
                raise PdfError("could not open in the main window")
            lines.append(f"OK {os.path.basename(path)}: {view.page_count} pages, "
                         f"first page {img.width}x{img.height}px")
        except Exception as exc:  # noqa: BLE001 - report every failure
            code = 1
            lines.append(f"FAIL {path}: {exc}")
    if not paths:
        lines.append("FAIL no file given")
        code = 2
    for v in window.views():
        v.session.stack.set_clean()
    window.close()
    text = "\n".join(lines)
    if log:
        with open(log, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    if sys.stdout is not None:  # windowed builds have no console
        print(text)
    return code


def _message(title: str, text: str, error: bool = False) -> None:
    from PyQt6.QtWidgets import QMessageBox
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(text)
    box.exec()


def run_install_command(argv: List[str]) -> int:
    """``--install`` / ``--uninstall`` / ``--register`` (packaged app only)."""
    from ..integration import windows as win
    # keep a reference: an unreferenced QApplication is garbage-collected at once
    app = create_application(argv)
    from .icons import app_icon
    from .settings import AppSettings
    from .theme import ThemeManager
    app.setWindowIcon(app_icon())
    ThemeManager(app, AppSettings().theme).apply()
    quiet = "--quiet" in argv
    log_path = os.path.join(os.environ.get("TEMP", "."), "pdf-studio-setup.log")

    def log(text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(text.rstrip() + "\n")
        except OSError:
            pass

    log(f"--- {' '.join(argv[1:])}")
    if not win.is_frozen():
        _message(APP_NAME, "Install from the packaged app: build it with "
                           "scripts\\build_pdf_studio.ps1, then run PDFStudio.exe --install.", True)
        return 2
    exe = win.current_exe()
    try:
        if "--uninstall" in argv:
            from PyQt6.QtWidgets import QMessageBox
            if not quiet and QMessageBox.question(
                    None, f"Uninstall {APP_NAME}",
                    f"Remove {APP_NAME}, its shortcuts and its PDF file association?") \
                    != QMessageBox.StandardButton.Yes:
                return 1
            win.uninstall()
            if not quiet:
                _message(APP_NAME, f"{APP_NAME} was uninstalled.")
            return 0
        if "--register" in argv:
            win.register(exe)
            return 0
        result = win.install(os.path.dirname(exe))
        log(f"installed to {result.install_dir}; shortcuts: {result.shortcuts}")
    except Exception as exc:  # noqa: BLE001 - report every failure to the user
        import traceback
        log(traceback.format_exc())
        _message(f"{APP_NAME} setup", f"Installation failed:\n{exc}\n\nDetails: {log_path}",
                 error=True)
        return 1
    if not quiet:
        _message(f"{APP_NAME} setup",
                 f"{APP_NAME} is installed.\n\nA shortcut is on your Desktop and in the Start "
                 f"menu. To open PDFs with it by default, use Help → {APP_NAME} on This Computer "
                 f"→ Make default PDF app.")
    if "--launch" in argv or not quiet:
        import subprocess
        subprocess.Popen([result.exe], close_fds=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if any(a in argv for a in ("--install", "--uninstall", "--register")):
        return run_install_command(argv)
    if "--smoke" in argv:
        log = None
        if "--smoke-log" in argv:
            i = argv.index("--smoke-log")
            log = argv[i + 1] if i + 1 < len(argv) else None
        files = [a for a in argv[1:] if a.lower().endswith(".pdf")]
        return smoke(files, log)
    if sys.platform == "win32":
        try:  # separate taskbar identity from python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORG_NAME}.{APP_ID}")
        except Exception:  # noqa: BLE001
            pass
    app = create_application(argv)
    files = [a for a in argv[1:] if not a.startswith("-") and os.path.isfile(a)]

    # Already running? Hand the files to that window (Explorer double-clicks).
    from .single_instance import InstanceServer, send_to_running
    if "--new-instance" not in argv and send_to_running(files):
        return 0

    from .icons import app_icon
    from .main_window import MainWindow
    from .settings import AppSettings
    from .theme import ThemeManager

    app.setWindowIcon(app_icon())
    settings = AppSettings()
    theme_manager = ThemeManager(app, settings.theme)
    theme_manager.apply()
    window = MainWindow(settings=settings, theme_manager=theme_manager)
    app.main_window = window
    install_exception_hook()
    server = InstanceServer(parent=window)
    server.filesReceived.connect(window.receive_files)
    window.show()
    for path in files:
        window.open_path(path)
    return app.exec()
