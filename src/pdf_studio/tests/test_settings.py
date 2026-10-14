"""Ticket 09: preferences persistence, Open Recent, sidebar switching."""

from __future__ import annotations

import os

from pdf_studio.tests.conftest import pump
from pdf_studio.ui.settings import MAX_RECENT, AppSettings


def test_recent_files_round_trip(tmp_path, qapp):
    path = str(tmp_path / "s.ini")
    s = AppSettings.from_file(path)
    for i in range(MAX_RECENT + 3):
        s.add_recent(str(tmp_path / f"f{i}.pdf"))
    s.add_recent(str(tmp_path / "f5.pdf"))  # moves to front, no duplicate
    s.sync()
    again = AppSettings.from_file(path)
    rec = again.recent_files
    assert len(rec) == MAX_RECENT
    assert os.path.basename(rec[0]) == "f5.pdf"
    assert len(set(map(os.path.normcase, rec))) == len(rec)
    again.remove_recent(rec[0])
    assert os.path.basename(again.recent_files[0]) != "f5.pdf"
    again.clear_recent()
    assert again.recent_files == []


def test_scalar_preferences_round_trip(tmp_path, qapp):
    path = str(tmp_path / "s.ini")
    s = AppSettings.from_file(path)
    s.last_dir = str(tmp_path)
    s.sidebar_width = 311
    s.sidebar_panel = "outline"
    s.view_mode = "two_page"
    s.theme = "dark"
    s.sync()
    again = AppSettings.from_file(path)
    assert again.last_dir == str(tmp_path)
    assert again.sidebar_width == 311
    assert again.sidebar_panel == "outline"
    assert again.view_mode == "two_page"
    assert again.theme == "dark"


def test_missing_last_dir_falls_back_to_home(tmp_path, qapp):
    s = AppSettings.from_file(str(tmp_path / "s.ini"))
    s.last_dir = str(tmp_path / "gone")
    assert s.last_dir == os.path.expanduser("~")


def test_window_persists_state_across_restarts(qapp, tmp_path, sample_pdf, monkeypatch):
    from pdf_studio.ui.dialogs import prompts
    from pdf_studio.ui.main_window import MainWindow

    monkeypatch.setattr(prompts, "show_error", lambda *a, **k: None)
    ini = str(tmp_path / "persist.ini")
    w = MainWindow(settings=AppSettings.from_file(ini))
    w.resize(760, 540)
    w.show()
    pump()
    w.open_path(sample_pdf)
    w.show_side_panel("outline")
    w.splitter.setSizes([300, 400])
    w._on_splitter_moved()
    w.close()
    w.deleteLater()
    pump()

    s = AppSettings.from_file(ini)
    assert s.recent_files[0] == os.path.abspath(sample_pdf)
    assert s.last_dir == os.path.dirname(os.path.abspath(sample_pdf))
    assert s.sidebar_panel == "outline"
    assert abs(s.sidebar_width - 300) <= 2

    w2 = MainWindow(settings=AppSettings.from_file(ini))
    w2.show()
    pump()
    assert w2.size().width() == 760 and w2.size().height() == 540
    assert w2.rail.current == "outline"
    # Open Recent menu lists the file and opens it in a tab
    w2._build_recent_menu()
    entries = [a for a in w2.recent_menu.actions() if a.toolTip() == os.path.abspath(sample_pdf)]
    assert entries
    entries[0].trigger()
    assert w2.view is not None and w2.view.session.path == os.path.abspath(sample_pdf)
    # the empty state lists recent files too
    w2.close()
    w2.deleteLater()
    pump()


def test_missing_recent_file_is_removed(window, tmp_path):
    ghost = str(tmp_path / "ghost.pdf")
    window.settings.add_recent(ghost)
    assert not window.open_recent(ghost)
    assert ghost not in window.settings.recent_files
    assert window.errors


def test_sidebar_switches_between_panels(window, sample_pdf):
    window.open_path(sample_pdf)
    for key, panel in (("pages", window.thumbnails), ("outline", window.outline_panel),
                       ("search", window.search_panel)):
        window.show_side_panel(key)
        assert window.side.currentWidget() is panel
        assert window.rail.current == key
    # clicking the active rail button collapses the sidebar
    window.rail._on_clicked("search")
    assert not window.side.isVisible()
    window.rail._on_clicked("pages")
    assert window.side.isVisible() and window.side.currentWidget() is window.thumbnails
