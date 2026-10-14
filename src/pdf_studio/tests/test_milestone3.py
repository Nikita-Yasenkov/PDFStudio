"""Ticket 26: Milestone 3 integration — page structure edits survive save."""

from __future__ import annotations

from pdf_studio.core.page_commands import (DeletePagesCommand, DuplicatePagesCommand,
                                           InsertBlankPageCommand, InsertPdfCommand,
                                           MovePagesCommand, RotatePagesCommand)
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PdfDocument
from pdf_studio.tests.test_page_ops_engine import markers
from pdf_studio.ui.dialogs import prompts


def rotations(doc):
    return [doc.page_info(i).rotation for i in range(doc.page_count)]


def test_structure_edits_save_and_reopen(sample_pdf, fixture_paths, tmp_path):
    s = DocumentSession.open(sample_pdf)
    ids = list(s.page_ids)
    with open(fixture_paths["plain.pdf"], "rb") as f:
        plain = f.read()
    for cmd in (
        RotatePagesCommand([ids[0]], 90),          # p1 → 90°
        MovePagesCommand([ids[11]], 0),            # p12 first
        DeletePagesCommand([ids[3], ids[4]]),      # drop p4, p5
        DuplicatePagesCommand([ids[1]]),           # p2 twice
        InsertBlankPageCommand(3, (595, 842)),     # blank before p3's copy area
        InsertPdfCommand(plain, 10 ** 6, [0]),     # append "Plain page 1"
    ):
        assert s.stack.push(cmd)
    expected_markers = markers(s.doc)
    expected_rot = rotations(s.doc)
    assert expected_markers[:4] == [12, 1, 2, 0]
    assert s.dirty
    out = str(tmp_path / "restructured.pdf")
    s.save(out)
    assert not s.dirty
    s.close()

    again = PdfDocument.open(out)
    try:
        assert again.page_count == len(expected_markers)
        assert markers(again) == expected_markers
        assert rotations(again) == expected_rot
        assert rotations(again)[1] == 90  # original page 1, now second
        assert "Plain page 1" in again.page_text(again.page_count - 1)
        # the highlight on page 3 survived the moves
        p3 = expected_markers.index(3)
        assert len(again.annotations(p3)) == 1
    finally:
        again.close()


def test_incremental_save_after_page_ops(sample_pdf):
    s = DocumentSession.open(sample_pdf)
    s.stack.push(RotatePagesCommand([s.page_ids[2]], 180))
    s.stack.push(MovePagesCommand([s.page_ids[0]], 12))
    result = s.save()
    assert result.incremental
    s.close()
    again = PdfDocument.open(sample_pdf)
    assert markers(again)[-1] == 1 and again.page_info(1).rotation == 180
    again.close()


def test_ui_structure_change_dirty_save_reopen(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(4)
    window.actions["rotate_right"].trigger()
    window.actions["duplicate_page"].trigger()
    window.thumbnails.list.rowsDropped.emit([0], 12)  # move first page to the end
    window.actions["first_page"].trigger()
    window.actions["delete_page"].trigger()
    assert v.session.dirty
    expected = markers(v.session.doc)
    out = str(tmp_path / "ui-pages.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    assert window.save_as()
    assert not v.session.dirty
    window.close_view(v)
    window.open_path(out)
    v2 = window.view
    assert markers(v2.session.doc) == expected
    assert v2.page_count == 12
    assert v2.session.page_info(expected.index(5)).rotation == 90
    assert window.thumbnails.model.rowCount() == 12
