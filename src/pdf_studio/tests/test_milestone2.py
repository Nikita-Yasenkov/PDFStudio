"""Ticket 19: Milestone 2 integration — annotate, save, reopen."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.core.annotation_commands import (AddAnnotationCommand,
                                                 DeleteAnnotationCommand,
                                                 UpdateAnnotationCommand)
from pdf_studio.core.selection import select_between
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PdfDocument
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import AnnotationModel, Point, Rect
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.dialogs import prompts


def test_session_annotate_save_reopen(sample_pdf, tmp_path):
    s = DocumentSession.open(sample_pdf)
    pid = s.page_id(1)
    words = s.words(1)
    sel = select_between(1, words, words[2].rect.center, words[6].rect.center)
    hl = AddAnnotationCommand(pid, AnnotationModel(K.HIGHLIGHT, sel.bounds,
                                                   quads=tuple(sel.line_rects),
                                                   color=(1, 0.85, 0), contents="key point"))
    note = AddAnnotationCommand(pid, AnnotationModel(K.NOTE, Rect(400, 400, 418, 418),
                                                     contents="remember"))
    ink = AddAnnotationCommand(pid, AnnotationModel(
        K.INK, Rect(0, 0, 0, 0), strokes=((Point(80, 700), Point(120, 720), Point(160, 700)),),
        color=(0, 0, 1), width=3))
    for c in (hl, note, ink):
        assert s.stack.push(c)
    # move the note, then delete the ink
    n = s.annotation_by_id(pid, note.annot_id)
    s.stack.push(UpdateAnnotationCommand(pid, n.id, n.translated(20, 0), "Move"))
    s.stack.push(DeleteAnnotationCommand(pid, ink.annot_id))
    assert s.dirty
    out = str(tmp_path / "annotated.pdf")
    s.save(out)
    assert not s.dirty
    s.close()

    again = PdfDocument.open(out)
    try:
        by_kind = {a.kind: a for a in again.annotations(1)}
        assert set(by_kind) == {K.HIGHLIGHT, K.NOTE}
        assert by_kind[K.HIGHLIGHT].contents == "key point"
        assert by_kind[K.NOTE].contents == "remember"
        assert by_kind[K.NOTE].rect.x0 == pytest.approx(420, abs=1.5)
        assert by_kind[K.HIGHLIGHT].id == hl.annot_id  # ids survive the round trip
    finally:
        again.close()


def test_undo_everything_then_save_matches_original_content(sample_pdf, tmp_path):
    s = DocumentSession.open(sample_pdf)
    pid = s.page_id(0)
    s.stack.push(AddAnnotationCommand(pid, AnnotationModel(K.RECTANGLE, Rect(50, 50, 90, 90))))
    s.stack.undo()
    assert not s.dirty
    out = str(tmp_path / "same.pdf")
    s.save(out)
    s.close()
    again = PdfDocument.open(out)
    assert again.annotations(0) == []
    again.close()


def test_ui_highlight_save_as_reopen(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(1)
    pump(30)
    window.set_tool("highlight")
    words = v.session.words(1)
    a = v.canvas.pdf_to_widget(1, words[3].rect.center).toPoint()
    b = v.canvas.pdf_to_widget(1, words[8].rect.center).toPoint()
    vp = v.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    assert v.session.dirty and "*" in window.tabs.tabText(window.tabs.currentIndex())

    out = str(tmp_path / "ui-saved.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    window.actions["save_as"].trigger()
    assert not v.session.dirty
    window.close_view(v)
    assert window.open_path(out)
    kinds = [x.kind for x in window.view.session.annotations(1)]
    assert kinds == [K.HIGHLIGHT]


def test_dirty_close_prompt_seam_is_non_blocking(window, sample_pdf, monkeypatch):
    """The close prompt goes through a monkeypatchable function."""
    calls = []
    monkeypatch.setattr(prompts, "ask_close_choice",
                        lambda parent, name: calls.append(name) or prompts.CloseChoice.DISCARD)
    window.open_path(sample_pdf)
    v = window.view
    v.execute(AddAnnotationCommand(v.session.page_id(0),
                                   AnnotationModel(K.RECTANGLE, Rect(50, 50, 90, 90))))
    assert window.close_view(v)
    assert calls == ["sample.pdf"]
