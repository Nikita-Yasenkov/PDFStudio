"""Ticket 24: insert blank page + insert pages from PDF."""

from __future__ import annotations

import pytest

from pdf_studio.core.page_ranges import (PageRangeError, chunk, format_ranges,
                                         parse_ranges, split_before)
from pdf_studio.pdf import PdfDocument
from pdf_studio.tests.conftest import pump
from pdf_studio.tests.test_page_ops_engine import markers
from pdf_studio.ui.dialogs import prompts


# ------------------------------------------------------------- page ranges
def test_parse_ranges():
    assert parse_ranges("1-3, 5", 10) == [0, 1, 2, 4]
    assert parse_ranges("8-", 10) == [7, 8, 9]
    assert parse_ranges("-2", 10) == [0, 1]
    assert parse_ranges("3-1", 10) == [2, 1, 0]
    assert parse_ranges("", 3) == [0, 1, 2]
    assert parse_ranges("all", 3) == [0, 1, 2]
    assert parse_ranges("2, 2, 1", 3) == [1, 0]
    for bad in ("0", "11", "x", "1-a", "5-12"):
        with pytest.raises(PageRangeError):
            parse_ranges(bad, 10)


def test_format_and_split_helpers():
    assert format_ranges([0, 1, 2, 4, 6, 7]) == "1-3, 5, 7-8"
    assert format_ranges([]) == ""
    assert chunk(5, 2) == [[0, 1], [2, 3], [4]]
    assert split_before(6, [2, 4]) == [[0, 1], [2, 3], [4, 5]]
    assert split_before(3, [0, 9]) == [[0, 1, 2]]


# ------------------------------------------------------------------ dialog
def test_insert_dialog_selection_and_position(window, fixture_paths):
    from pdf_studio.ui.dialogs.insert_pages import POS_END, InsertPagesDialog
    src = PdfDocument.open(fixture_paths["sample.pdf"])
    try:
        dlg = InsertPagesDialog(window, src, "sample.pdf", target_count=5, current_page=2)
        assert dlg.picker.selected() == list(range(12))
        dlg.picker.range_edit.setText("2-4, 9")
        dlg.picker._on_range_edited("2-4, 9")
        assert dlg.picker.selected() == [1, 2, 3, 8]
        assert dlg.ok.text() == "Insert 4 pages"
        assert dlg.insert_index() == 3  # after current page (index 2)
        dlg.position.setCurrentIndex(POS_END)
        assert dlg.result_values() == ([1, 2, 3, 8], 5)
        dlg.picker._on_range_edited("99")
        assert dlg.picker.range_error and not dlg.ok.isEnabled()
        pump(100)  # thumbnails render in batches without blocking
        # selected pages are not tinted: the Selected icon is the page itself
        from PyQt6.QtGui import QIcon
        icon = dlg.picker.grid.item(1).icon()
        size = dlg.picker.grid.iconSize()
        normal = icon.pixmap(size, QIcon.Mode.Normal).toImage()
        assert icon.pixmap(size, QIcon.Mode.Selected).toImage() == normal
        dlg.close()
    finally:
        src.close()


# --------------------------------------------------------------------- UI
def test_insert_from_pdf_command_flow(window, sample_pdf, fixture_paths, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(1)
    monkeypatch.setattr(prompts, "ask_open_pdf", lambda *a, **k: fixture_paths["plain.pdf"])
    monkeypatch.setattr(prompts, "ask_insert_pages",
                        lambda parent, src, name, count, cur, pos: ([1, 0], cur + 1))
    assert window.insert_from_pdf()
    assert v.page_count == 14
    assert "Plain page 2" in v.session.doc.page_text(2)
    assert "Plain page 1" in v.session.doc.page_text(3)
    assert v.current_page == 2
    assert window.thumbnails.model.rowCount() == 14
    assert v.session.dirty
    window.actions["undo"].trigger()
    assert v.page_count == 12 and markers(v.session.doc) == list(range(1, 13))


def test_insert_cancelled_changes_nothing(window, sample_pdf, fixture_paths, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    monkeypatch.setattr(prompts, "ask_open_pdf", lambda *a, **k: fixture_paths["plain.pdf"])
    monkeypatch.setattr(prompts, "ask_insert_pages", lambda *a, **k: None)
    assert not window.insert_from_pdf()
    assert v.page_count == 12 and not v.session.dirty


def test_insert_from_encrypted_pdf_prompts_for_password(window, sample_pdf, fixture_paths,
                                                        monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    asked = []
    monkeypatch.setattr(prompts, "ask_open_pdf", lambda *a, **k: fixture_paths["encrypted.pdf"])
    monkeypatch.setattr(prompts, "ask_password",
                        lambda parent, name, wrong=False: asked.append(wrong) or
                        ("nope" if len(asked) == 1 else "secret"))
    monkeypatch.setattr(prompts, "ask_insert_pages",
                        lambda parent, src, name, count, cur, pos: ([0], count))
    assert window.insert_from_pdf()
    assert asked == [False, True]  # retried after a wrong password
    assert "Encrypted page 1" in v.session.doc.page_text(12)


def test_dropping_pdf_files_on_thumbnails_inserts_them(window, sample_pdf, fixture_paths):
    window.open_path(sample_pdf)
    v = window.view
    window.thumbnails.filesDropped.emit([fixture_paths["plain.pdf"]], 0)
    assert v.page_count == 14
    assert "Plain page 1" in v.session.doc.page_text(0)
    window.actions["undo"].trigger()
    assert v.page_count == 12
