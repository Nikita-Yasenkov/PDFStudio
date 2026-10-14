"""Ticket 34: Milestone 4 integration — content edits and signatures survive
save/reopen; edits are undoable and drive the dirty flag."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.core.annotation_commands import AddAnnotationCommand
from pdf_studio.core.edit_commands import (PlaceImageCommand, RedactCommand,
                                           ReplaceTextCommand)
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PdfDocument
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import Point, Rect
from pdf_studio.pdf.stamps import signature_model
from pdf_studio.tests.conftest import pump
from pdf_studio.tests.test_editing_engine import _png
from pdf_studio.tests.test_stamps_engine import signature_png
from pdf_studio.ui.dialogs import prompts
from pdf_studio.ui.signatures import SignatureChoice


def _span(s, page, first, last):
    words = s.words(page)
    a = next(w for w in words if w.text == first)
    b = next(w for w in words if w.text == last)
    return a.rect.union(b.rect)


def test_session_edits_save_reopen(sample_pdf, tmp_path):
    s = DocumentSession.open(sample_pdf)
    p2, p3, p4 = s.page_id(1), s.page_id(2), s.page_id(3)
    rect = _span(s, 2, "important", "sentence")
    style = s.probe_text_style(2, rect)
    assert s.stack.push(ReplaceTextCommand(p3, rect, "replaced phrase", style))
    assert s.stack.push(RedactCommand(p2, [_span(s, 1, "PAGE-MARKER-2", "PAGE-MARKER-2")]))
    img = s.image_rects(3)[0].rect
    assert s.stack.push(PlaceImageCommand(p4, img, _png(120, 70, (10, 160, 60))))
    sig = AddAnnotationCommand(p4, signature_model(Rect(330, 700, 500, 750), signature_png()),
                               label="Add signature")
    assert s.stack.push(sig)
    assert s.dirty
    out = str(tmp_path / "edited.pdf")
    result = s.save(out)
    assert not result.incremental and not s.dirty
    s.close()

    d = PdfDocument.open(out)
    try:
        t3 = d.page_text(2)
        assert "replaced phrase" in t3 and "important sentence" not in t3
        assert "PAGE-MARKER-2" not in d.page_text(1)  # truly removed, not just covered
        assert len(d.image_rects(3)) == 2  # original (covered) + replacement
        stamps = [a for a in d.annotations(3) if a.kind == K.STAMP]
        assert len(stamps) == 1 and stamps[0].id == sig.annot_id
    finally:
        d.close()


def test_undo_all_edits_restores_original_text(sample_pdf):
    s = DocumentSession.open(sample_pdf)
    original = [s.doc.page_text(i) for i in range(4)]
    rect = _span(s, 2, "important", "sentence")
    s.stack.push(ReplaceTextCommand(s.page_id(2), rect, "x", s.probe_text_style(2, rect)))
    s.stack.push(RedactCommand(s.page_id(1), [_span(s, 1, "Chapter", "Rendering")]))
    s.stack.push(PlaceImageCommand(s.page_id(0), Rect(60, 600, 200, 680), _png()))
    while s.stack.can_undo:
        s.stack.undo()
    assert [s.doc.page_text(i) for i in range(4)] == original
    assert not s.dirty
    s.close()


def test_ui_edit_and_sign_then_save(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(2)
    pump(30)
    # 1) redact & replace through the Edit Text tool
    monkeypatch.setattr(prompts, "ask_replace_text",
                        lambda parent, original, style: ("UI replacement", style, 0))
    window.set_tool("edit_text")
    rect = _span(v.session, 2, "important", "sentence")
    a = v.canvas.pdf_to_widget(2, Point(rect.x0 + 2, rect.center.y)).toPoint()
    b = v.canvas.pdf_to_widget(2, Point(rect.x1 - 2, rect.center.y)).toPoint()
    vp = v.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    # 2) place a signature
    window.arm_signature(SignatureChoice(image=signature_png()))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton,
                     pos=v.canvas.pdf_to_widget(2, Point(400, 720)).toPoint())
    assert v.session.dirty and len(v.session.stack) == 2
    out = str(tmp_path / "ui-edited.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    assert window.save_as()
    assert not v.session.dirty
    window.close_view(v)
    assert window.open_path(out)
    v2 = window.view
    assert "UI replacement" in v2.session.doc.page_text(2)
    assert [x.kind for x in v2.session.annotations(2)].count(K.STAMP) == 1
