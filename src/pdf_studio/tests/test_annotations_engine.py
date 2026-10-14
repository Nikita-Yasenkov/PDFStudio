"""Tickets 14/19: Qt-free annotation CRUD."""

from __future__ import annotations

import pytest

from pdf_studio.pdf import (AnnotationError, AnnotationKind as K, AnnotationModel,
                            PdfDocument, Point, Rect)


@pytest.fixture
def doc(sample_pdf):
    d = PdfDocument.open(sample_pdf)
    yield d
    d.close()


def _png(w=60, h=30) -> bytes:
    import pymupdf as fitz  # test-only helper
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), True)
    pix.clear_with(0)
    for x in range(w):
        pix.set_pixel(x, h // 2, (20, 40, 160, 255))
    return pix.tobytes("png")


def test_existing_annotation_is_listed(doc):
    annots = doc.annotations(2)
    assert len(annots) == 1
    a = annots[0]
    assert a.kind == K.HIGHLIGHT and a.contents == "Pre-existing highlight"
    assert a.author == "Fixture" and a.id
    assert a.quads and a.page_index == 2


@pytest.mark.parametrize("model", [
    AnnotationModel(K.HIGHLIGHT, Rect(70, 120, 200, 135), quads=(Rect(70, 120, 200, 135),),
                    color=(1, 0.85, 0)),
    AnnotationModel(K.UNDERLINE, Rect(70, 120, 200, 135), quads=(Rect(70, 120, 200, 135),),
                    color=(0, 0.4, 1)),
    AnnotationModel(K.STRIKEOUT, Rect(70, 120, 200, 135), quads=(Rect(70, 120, 200, 135),),
                    color=(1, 0, 0)),
    AnnotationModel(K.NOTE, Rect(300, 300, 320, 320), contents="Check this", color=(1, 0.8, 0)),
    AnnotationModel(K.FREE_TEXT, Rect(100, 500, 300, 540), contents="Typed text",
                    font_size=14, text_color=(0.8, 0, 0)),
    AnnotationModel(K.INK, Rect(0, 0, 0, 0),
                    strokes=((Point(100, 600), Point(150, 640), Point(200, 610)),),
                    color=(0, 0, 1), width=2.5),
    AnnotationModel(K.RECTANGLE, Rect(50, 650, 150, 720), color=(1, 0, 0), fill=(1, 1, 0),
                    width=2, opacity=0.6),
    AnnotationModel(K.ELLIPSE, Rect(200, 650, 300, 720), color=(0, 0.6, 0), width=3),
    AnnotationModel(K.LINE, Rect(0, 0, 0, 0), strokes=((Point(60, 760), Point(260, 780)),),
                    color=(0, 0, 0), width=2),
    AnnotationModel(K.ARROW, Rect(0, 0, 0, 0), strokes=((Point(60, 790), Point(260, 800)),),
                    color=(0, 0, 0), width=2),
], ids=lambda m: m.kind.value)
def test_create_list_update_delete(doc, model):
    created = doc.add_annotation(1, model)
    assert created.kind == model.kind and created.id
    listed = {a.id: a for a in doc.annotations(1)}
    assert created.id in listed
    got = listed[created.id]
    if model.contents:
        assert got.contents == model.contents
    if model.kind in (K.RECTANGLE, K.ELLIPSE, K.FREE_TEXT):
        assert got.rect.x0 == pytest.approx(model.rect.x0, abs=3)
        assert got.rect.y1 == pytest.approx(model.rect.y1, abs=3)
    if model.kind == K.FREE_TEXT:
        assert got.font_size == pytest.approx(14)
        assert got.text_color == pytest.approx((0.8, 0, 0), abs=0.01)
    if model.kind == K.RECTANGLE:
        assert got.fill == pytest.approx((1, 1, 0), abs=0.01)
        assert got.opacity == pytest.approx(0.6, abs=0.01)
    if model.kind in (K.INK, K.LINE, K.ARROW):
        assert got.strokes[0][0].x == pytest.approx(model.strokes[0][0].x, abs=0.5)

    # update colour (+ contents where meaningful)
    upd = got.with_changes(color=(0.2, 0.3, 0.4), contents="edited" if got.has_contents else "")
    after = doc.update_annotation(1, got.id, upd)
    assert after.id == got.id
    if got.has_color and model.kind != K.FREE_TEXT:
        assert after.color == pytest.approx((0.2, 0.3, 0.4), abs=0.01)

    doc.delete_annotation(1, got.id)
    assert got.id not in {a.id for a in doc.annotations(1)}


def test_move_keeps_identity_for_all_movable_kinds(doc):
    specs = [
        AnnotationModel(K.NOTE, Rect(300, 300, 320, 320), contents="n"),
        AnnotationModel(K.FREE_TEXT, Rect(100, 500, 300, 540), contents="t"),
        AnnotationModel(K.INK, Rect(0, 0, 0, 0), strokes=((Point(100, 600), Point(150, 640)),)),
        AnnotationModel(K.RECTANGLE, Rect(50, 650, 150, 720)),
        AnnotationModel(K.LINE, Rect(0, 0, 0, 0), strokes=((Point(60, 760), Point(260, 780)),)),
        AnnotationModel(K.STAMP, Rect(300, 100, 420, 160), stamp_image=_png()),
    ]
    for spec in specs:
        a = doc.add_annotation(0, spec)
        moved = doc.update_annotation(0, a.id, a.translated(25, 40))
        assert moved.id == a.id
        assert moved.rect.x0 == pytest.approx(a.rect.x0 + 25, abs=1.5), spec.kind
        assert moved.rect.y0 == pytest.approx(a.rect.y0 + 40, abs=1.5), spec.kind


def test_recreate_with_same_id(doc):
    a = doc.add_annotation(0, AnnotationModel(K.RECTANGLE, Rect(10, 10, 60, 60)))
    snap = doc.annotation(0, a.id)
    doc.delete_annotation(0, a.id)
    again = doc.add_annotation(0, snap)
    assert again.id == a.id


def test_image_stamp_snapshot_recovers_pixels(doc):
    a = doc.add_annotation(0, AnnotationModel(K.STAMP, Rect(300, 100, 420, 160),
                                              stamp_image=_png()))
    snap = doc.annotation(0, a.id, with_image=True)
    assert snap.stamp_image and snap.stamp_image[:4] == b"\x89PNG"
    doc.delete_annotation(0, a.id)
    restored = doc.add_annotation(0, snap)
    assert restored.kind == K.STAMP and restored.id == a.id


def test_named_stamp(doc):
    a = doc.add_annotation(0, AnnotationModel(K.STAMP, Rect(300, 100, 450, 150),
                                              stamp_name="Draft"))
    assert doc.annotation(0, a.id).stamp_name == "Draft"


def test_annotations_on_rotated_page_use_display_space(doc):
    w, h = doc.page_info(5).width, doc.page_info(5).height
    assert w > h  # landscape when displayed (/Rotate 90)
    r = Rect(600, 400, 700, 450)  # only valid in displayed space (x > unrotated width)
    a = doc.add_annotation(5, AnnotationModel(K.RECTANGLE, r, color=(1, 0, 0)))
    assert a.rect.x0 == pytest.approx(600, abs=2) and a.rect.y1 == pytest.approx(450, abs=2)
    hl = doc.add_annotation(5, AnnotationModel(K.UNDERLINE, r, quads=(r,)))
    assert hl.quads[0].x0 == pytest.approx(600, abs=1)


def test_errors(doc):
    with pytest.raises(AnnotationError):
        doc.delete_annotation(0, "missing-id")
    with pytest.raises(AnnotationError):
        doc.add_annotation(0, AnnotationModel(K.HIGHLIGHT, Rect(0, 0, 1, 1)))  # no quads
    with pytest.raises(AnnotationError):
        doc.add_annotation(0, AnnotationModel(K.OTHER, Rect(0, 0, 10, 10)))


def test_annotations_survive_save_and_reopen(sample_pdf, tmp_path):
    d = PdfDocument.open(sample_pdf)
    a = d.add_annotation(0, AnnotationModel(K.NOTE, Rect(100, 100, 120, 120),
                                            contents="persist me"))
    out = str(tmp_path / "annot.pdf")
    d.save(out)
    d.close()
    again = PdfDocument.open(out)
    notes = [x for x in again.annotations(0) if x.kind == K.NOTE]
    assert notes and notes[0].contents == "persist me" and notes[0].id == a.id
    again.close()
