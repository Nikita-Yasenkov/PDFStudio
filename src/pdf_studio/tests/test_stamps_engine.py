"""Ticket 32: visual stamp / signature placement (engine, Qt-free)."""

from __future__ import annotations

import pytest

from pdf_studio.core.annotation_commands import (AddAnnotationCommand,
                                                 DeleteAnnotationCommand)
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import EditError, PdfDocument
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import Point, Rect
from pdf_studio.pdf.stamps import (default_rect, fit_into, fit_rect, image_size,
                                   signature_model, trim_transparent)


def signature_png(w=300, h=100) -> bytes:
    import pymupdf as fitz  # test helper only
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), True)
    pix.clear_with()  # fully transparent
    for x in range(40, w - 40):
        y = h // 2 + int(15 * ((x // 12) % 2))
        for dy in range(3):
            pix.set_pixel(x, y + dy, (20, 30, 120, 255))
    return pix.tobytes("png")


def test_geometry_helpers():
    r = fit_rect(Point(300, 400), 3.0, 150, 595, 842)
    assert r.width == pytest.approx(150) and r.height == pytest.approx(50)
    assert r.center.x == pytest.approx(300) and r.center.y == pytest.approx(400)
    edge = fit_rect(Point(590, 840), 3.0, 150, 595, 842)
    assert edge.x1 <= 593 and edge.y1 <= 840  # kept on the page
    box = fit_into(Rect(0, 0, 200, 200), 2.0)
    assert (box.width, box.height) == (200, 100) and box.y0 == 50


def test_trim_transparent_margins():
    data = signature_png()
    w, h = image_size(data)
    tw, th = image_size(trim_transparent(data))
    assert tw < w - 60 and th < h - 40


def test_place_list_save_reopen_delete(sample_pdf, tmp_path):
    doc = PdfDocument.open(sample_pdf)
    data = trim_transparent(signature_png())
    rect = default_rect(595, 842, Point(400, 700), image=data)
    sig = doc.place_stamp(11, rect, image=data, author="Tester")
    approved = doc.place_stamp(11, Rect(60, 60, 220, 102), name="Approved")
    listed = {a.id: a for a in doc.annotations(11)}
    assert listed[sig.id].kind == K.STAMP and listed[sig.id].stamp_name is None
    assert listed[approved.id].stamp_name == "Approved"
    assert listed[sig.id].rect.center.x == pytest.approx(400, abs=1)
    out = str(tmp_path / "signed.pdf")
    doc.save(out)
    doc.close()
    again = PdfDocument.open(out)
    stamps = [a for a in again.annotations(11) if a.kind == K.STAMP]
    assert len(stamps) == 2
    # the signature's pixels can be recovered (needed to undo a deletion)
    snap = again.annotation(11, sig.id, with_image=True)
    assert snap.stamp_image and image_size(snap.stamp_image)[0] > 10
    again.delete_annotation(11, sig.id)
    assert len([a for a in again.annotations(11) if a.kind == K.STAMP]) == 1
    again.close()


def test_signature_commands_undo(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    try:
        pid = s.page_id(0)
        data = signature_png()
        add = AddAnnotationCommand(pid, signature_model(Rect(300, 600, 480, 660), data),
                                   label="Add signature")
        s.stack.push(add)
        s.stack.push(DeleteAnnotationCommand(pid, add.annot_id))
        assert s.annotations(0) == []
        s.stack.undo()  # deletion undone from the recovered image
        restored = s.annotation_by_id(pid, add.annot_id)
        assert restored is not None and restored.kind == K.STAMP
        s.stack.undo()
        assert s.annotations(0) == [] and not s.dirty
    finally:
        s.close()


def test_bad_inputs():
    with pytest.raises(EditError):
        image_size(b"nope")
    with pytest.raises(EditError):
        from pdf_studio.pdf.stamps import stamp_model
        stamp_model(Rect(0, 0, 10, 10), "NoSuchStamp")
