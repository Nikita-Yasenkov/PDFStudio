"""Ticket 40: menus, tooltips, About, shortcuts cheat sheet, empty state."""

from __future__ import annotations

from collections import Counter

from PyQt6.QtGui import QKeySequence

from pdf_studio import APP_NAME, __version__
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.dialogs import prompts


def test_menus_are_complete_and_ordered(window):
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()]
    assert titles == ["File", "Edit", "View", "Pages", "Tools", "Help"]


def test_no_duplicate_shortcuts(window):
    seen = Counter()
    for a in window.actions.values():
        for seq in a.shortcuts():
            if not seq.isEmpty():
                seen[seq.toString(QKeySequence.SequenceFormat.PortableText)] += 1
    dupes = {k: n for k, n in seen.items() if n > 1}
    assert not dupes, f"conflicting shortcuts: {dupes}"


def test_toolbar_actions_have_tooltips_and_status_tips(window):
    for a in window.toolbar.actions():
        if a.isSeparator() or not a.text():
            continue
        assert a.toolTip(), a.text()
    for key, a in window.actions.items():
        if key.startswith("tool_"):
            assert "(" in a.toolTip(), f"{key} tooltip lacks its shortcut"
            assert a.statusTip(), key


def test_tool_hint_shown_in_status_bar(window, sample_pdf):
    window.open_path(sample_pdf)
    window.set_tool("ink")
    assert "Draw freehand" in window.status_hint.text()
    window.set_tool("select")
    assert "select" in window.status_hint.text().lower()


def test_about_dialog_versions(window):
    from pdf_studio.ui.dialogs.about import AboutDialog, version_info
    info = version_info()
    assert info[APP_NAME] == __version__
    assert {"PyQt6", "Qt", "PyMuPDF", "MuPDF", "Python"} <= set(info)
    dlg = AboutDialog(window)
    assert "PyMuPDF" in dlg.text() and __version__ in dlg.text()
    dlg.close()


def test_help_actions_use_non_blocking_seams(window, monkeypatch):
    shown = []
    monkeypatch.setattr(prompts, "show_about", lambda parent: shown.append("about"))
    monkeypatch.setattr(prompts, "show_shortcuts", lambda parent, rows: shown.append(rows))
    window.actions["about"].trigger()
    window.actions["shortcuts"].trigger()
    assert shown[0] == "about"
    rows = shown[1]
    actions = {r[1] for r in rows}
    assert {"Open", "Undo", "Find", "Zoom In", "Highlight", "Rotate Right"} <= actions
    assert any(r[0] == "Canvas" for r in rows)


def test_shortcuts_dialog_filter(window):
    from pdf_studio.ui.dialogs.about import ShortcutsDialog
    dlg = ShortcutsDialog(window, window.shortcut_rows())
    total = dlg.visible_rows()
    assert total > 30
    dlg.filter.setText("zoom")
    assert 0 < dlg.visible_rows() < total
    dlg.close()


def test_empty_state_when_no_tabs(window, sample_pdf):
    assert window.stack.currentWidget() is window.empty
    assert not window.tab_strip.isVisible() and not window.rail.isVisible()
    for key in ("save", "close", "zoom_in", "rotate_right", "tool_highlight"):
        assert not window.actions[key].isEnabled(), key
    assert window.actions["open"].isEnabled() and window.actions["combine_files"].isEnabled()
    window.open_path(sample_pdf)
    assert window.stack.currentWidget() is window.view
    window.close_all()
    assert window.stack.currentWidget() is window.empty
    pump()
    # the file just closed is offered as a recent file
    assert window.empty._recent_box.isVisibleTo(window.empty)
    assert window.empty._recent_list.count() == 1


def test_recent_rows_fit_large_text(window, sample_pdf):
    # the button stylesheet's min-height must not squeeze the two-line row
    # (large text scaling), also after the theme is re-applied
    from PyQt6.QtGui import QFont
    font = QFont(window.empty.font())
    font.setPixelSize(30)
    window.empty.setFont(font)
    window.open_path(sample_pdf)
    window.close_all()
    pump()
    row = window.empty._recent_list.itemAt(0).widget()
    needed = row.layout().minimumSize().height()
    assert needed > 60
    assert row.minimumHeight() >= needed and row.height() >= needed
    window._on_theme_changed()
    pump()
    assert row.minimumHeight() >= needed and row.height() >= needed


def test_window_title_reflects_document(window, sample_pdf):
    assert window.windowTitle() == APP_NAME
    window.open_path(sample_pdf)
    assert window.windowTitle() == f"sample.pdf — {APP_NAME}"
