"""Tickets 27/30/34: redact-and-replace and image cover (engine)."""

from __future__ import annotations

import pytest

from pdf_studio.pdf import EditError, PdfDocument, Rect
from pdf_studio.pdf.models import TextStyle


@pytest.fixture
def doc(sample_pdf):
    d = PdfDocument.open(sample_pdf)
    yield d
    d.close()


def _word(doc, page, text):
    return next(w for w in doc.words(page) if w.text == text)


def _png(w=60, h=30, color=(200, 30, 30)) -> bytes:
    import pymupdf as fitz  # test helper only
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def test_probe_text_style(doc):
    heading = _word(doc, 1, "Chapter")
    st = doc.probe_text_style(1, heading.rect)
    assert st.font_size == pytest.approx(22, abs=0.5)
    assert st.font_name == "hebo"  # Helvetica-Bold heading
    assert "Chapter" in st.text
    body = _word(doc, 1, "Portable")
    st2 = doc.probe_text_style(1, body.rect)
    assert st2.font_size == pytest.approx(10.5, abs=0.3)
    assert st2.color == pytest.approx((0.13, 0.15, 0.19), abs=0.02)
    assert st2.background == pytest.approx((1, 1, 1), abs=0.01)


def test_background_probe_on_tinted_area(doc):
    title = _word(doc, 0, "Studio")  # cover page title sits on a light grey band
    st = doc.probe_text_style(0, title.rect)
    assert st.background != (1.0, 1.0, 1.0)
    assert all(0.9 < c < 1.0 for c in st.background)


def test_redact_removes_text_and_restores(doc):
    marker = _word(doc, 3, "PAGE-MARKER-4")
    snap = doc.snapshot_page_content(3)
    doc.redact(3, marker.rect)
    assert "PAGE-MARKER-4" not in doc.page_text(3)
    assert doc.requires_full_save
    doc.restore_page_content(3, snap)
    assert "PAGE-MARKER-4" in doc.page_text(3)


def test_replace_text_round_trip(doc):
    heading = next(w for w in doc.words(2) if w.text == "important")
    end = next(w for w in doc.words(2) if w.text == "sentence")
    rect = heading.rect.union(end.rect)
    style = doc.probe_text_style(2, rect)
    snap = doc.snapshot_page_content(2)
    size = doc.replace_text(2, rect, "crucial statement", style)
    text = doc.page_text(2)
    assert "crucial statement" in text and "important sentence" not in text
    assert size == pytest.approx(style.font_size, abs=0.01)
    doc.restore_page_content(2, snap)
    assert "important sentence" in doc.page_text(2)
    assert "crucial" not in doc.page_text(2)


def test_long_replacement_shrinks_to_fit(doc):
    w = _word(doc, 1, "Portable")
    style = doc.probe_text_style(1, w.rect)
    size = doc.replace_text(1, w.rect, "A much longer replacement phrase", style)
    assert size < style.font_size
    assert "replacement" in doc.text_in_rect(1, w.rect.inflated(30))


def test_replace_on_rotated_page_reads_upright(doc):
    # displayed-space rect on the /Rotate 90 page, in an empty area
    rect = Rect(500, 400, 760, 430)
    doc.replace_text(5, rect, "Upright replacement", TextStyle(font_size=14))
    words = [w for w in doc.words(5) if w.text in ("Upright", "replacement")]
    assert len(words) == 2
    for w in words:
        assert w.rect.width > w.rect.height  # horizontal for the reader
        assert rect.inflated(2).contains(w.rect.center)


def test_place_image_and_errors(doc):
    rect = Rect(56, 500, 296, 640)
    before = len(doc.image_rects(3))
    snap = doc.snapshot_page_content(3)
    doc.place_image(3, rect, _png())
    imgs = doc.image_rects(3)
    assert len(imgs) == before + 1
    placed = max(imgs, key=lambda i: i.rect.y0)
    assert rect.inflated(1).contains(placed.rect.center)
    # keep_aspect: a 2:1 image in a 240x140 box is 240x120 (centred)
    assert placed.rect.width == pytest.approx(240, abs=1)
    assert placed.rect.height == pytest.approx(120, abs=1)
    doc.restore_page_content(3, snap)
    assert len(doc.image_rects(3)) == before
    with pytest.raises(EditError):
        doc.place_image(3, rect, b"not an image")
    with pytest.raises(EditError):
        doc.place_image(3, Rect(10, 10, 10, 10), _png())


def test_existing_image_rect_is_reported(doc):
    imgs = doc.image_rects(3)  # the gradient figure on page 4
    assert imgs and imgs[0].rect.width == pytest.approx(240, abs=1)


def test_redaction_forces_full_save(sample_pdf, tmp_path):
    d = PdfDocument.open(sample_pdf)
    d.redact(0, _word(d, 0, "PAGE-MARKER-1").rect)
    res = d.save()
    assert not res.incremental  # redacted text must not survive in old revisions
    d.close()
    with open(sample_pdf, "rb") as f:
        raw = f.read()
    assert b"PAGE-MARKER-1" not in raw
    again = PdfDocument.open(sample_pdf)
    assert "PAGE-MARKER-1" not in again.page_text(0)
    again.close()


def test_place_image_from_path_and_other_formats(doc, tmp_path):
    import pymupdf as fitz  # test helper only
    png = tmp_path / "sig.png"
    png.write_bytes(_png(80, 40, (10, 10, 120)))
    doc.place_image(0, Rect(60, 600, 220, 680), str(png))
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pix.set_rect(pix.irect, (0, 128, 0))
    bmp = pix.tobytes("pnm")  # not embeddable as-is: converted to PNG
    doc.place_image(0, Rect(300, 600, 340, 640), bmp, cover=False)
    assert len(doc.image_rects(0)) == 2
    with pytest.raises(EditError):
        doc.place_image(0, Rect(60, 600, 220, 680), str(tmp_path / "missing.png"))
