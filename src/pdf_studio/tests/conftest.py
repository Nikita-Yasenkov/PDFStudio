from __future__ import annotations

import os
import shutil

import pytest

# UI tests run headless; must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pdf_studio.tests.fixtures.make_fixtures import ensure_fixtures  # noqa: E402

_FIXTURES = ensure_fixtures()


@pytest.fixture(scope="session")
def fixture_paths() -> dict:
    return dict(_FIXTURES)


@pytest.fixture
def sample_pdf(tmp_path) -> str:
    """A writable copy of the 12-page sample (safe to mutate/save)."""
    dst = tmp_path / "sample.pdf"
    shutil.copy(_FIXTURES["sample.pdf"], dst)
    return str(dst)


@pytest.fixture
def plain_pdf(tmp_path) -> str:
    dst = tmp_path / "plain.pdf"
    shutil.copy(_FIXTURES["plain.pdf"], dst)
    return str(dst)


@pytest.fixture
def long_pdf() -> str:
    return _FIXTURES["long.pdf"]


@pytest.fixture
def encrypted_pdf(tmp_path) -> str:
    dst = tmp_path / "encrypted.pdf"
    shutil.copy(_FIXTURES["encrypted.pdf"], dst)
    return str(dst)


@pytest.fixture(autouse=True)
def _fail_on_slot_exceptions():
    """PyQt6 aborts the process when a slot raises and no excepthook is set.
    Record such exceptions instead and fail the test that caused them."""
    import sys
    errors = []
    old = sys.excepthook
    sys.excepthook = lambda t, e, tb: errors.append((t, e, tb))
    yield
    sys.excepthook = old
    if errors:
        import traceback
        t, e, tb = errors[0]
        pytest.fail("Exception in a Qt slot:\n" + "".join(traceback.format_exception(t, e, tb)))


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["pdf-studio-tests"])
    yield app


@pytest.fixture
def settings(tmp_path, qapp):
    """Isolated AppSettings backed by an ini file in tmp_path."""
    from pdf_studio.ui.settings import AppSettings

    return AppSettings.from_file(str(tmp_path / "settings.ini"))


def pump(ms: int = 50) -> None:
    """Let queued signals, paints and render results run."""
    from PyQt6.QtTest import QTest
    QTest.qWait(ms)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A shown MainWindow with isolated settings and non-blocking prompts."""
    from pdf_studio.ui import main_window as mw
    from pdf_studio.ui.dialogs import prompts

    errors = []
    monkeypatch.setattr(prompts, "show_error", lambda *a, **k: errors.append(a))
    kwargs = {}
    try:
        from pdf_studio.ui.settings import AppSettings
        kwargs["settings"] = AppSettings.from_file(str(tmp_path / "settings.ini"))
    except ImportError:
        pass
    w = mw.MainWindow(**kwargs)
    from pdf_studio.ui.signatures import SignatureStore
    w.tool_defaults.signature_store = SignatureStore(str(tmp_path / "signatures"))
    w.errors = errors
    w.resize(1100, 800)
    w.show()
    pump()
    yield w
    # never prompt on teardown
    for v in list(w.views()):
        v.session.stack.set_clean() if hasattr(v.session.stack, "set_clean") else None
    w.close()
    # destroy it: leftover top-level widgets make every later stylesheet
    # change (theme tests) re-polish them all
    w.deleteLater()
    pump()
