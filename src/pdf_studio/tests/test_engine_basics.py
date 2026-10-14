"""Ticket 01/10: engine facade — open, metadata, render, text, outline."""

from __future__ import annotations

import ast
import pathlib

import pytest

from pdf_studio.pdf import (PasswordRequiredError, PdfDocument, PdfOpenError,
                            Rect)

PKG = pathlib.Path(__file__).resolve().parents[1]


def test_open_reports_pages_and_sizes(fixture_paths):
    doc = PdfDocument.open(fixture_paths["sample.pdf"])
    try:
        assert doc.page_count == 12
        infos = doc.page_infos()
        assert [round(i.width) for i in infos][:5] == [595, 595, 595, 595, 842]
        # page 6 is stored with /Rotate 90: displayed size is landscape
        assert infos[5].rotation == 90
        assert (round(infos[5].width), round(infos[5].height)) == (842, 595)
        # letter page
        assert (round(infos[7].width), round(infos[7].height)) == (612, 792)
        assert doc.metadata()["title"] == "PDF Studio Sample"
    finally:
        doc.close()


def test_render_full_page_and_tile(fixture_paths):
    doc = PdfDocument.open(fixture_paths["sample.pdf"])
    try:
        img = doc.render(0, 1.0)
        assert (img.width, img.height) == (595, 842)
        assert img.stride >= img.width * 3
        assert len(img.data) == img.stride * img.height
        tile = doc.render(0, 2.0, clip=Rect(100, 100, 356, 356))
        assert (tile.width, tile.height) == (512, 512)
        assert (tile.x, tile.y) == (200, 200)
    finally:
        doc.close()


def test_text_extraction_in_display_space(fixture_paths):
    doc = PdfDocument.open(fixture_paths["sample.pdf"])
    try:
        assert "PAGE-MARKER-2" in doc.page_text(1)
        words = doc.words(1)
        assert any(w.text == "PAGE-MARKER-2" for w in words)
        # words on the rotated page lie inside its displayed bounds
        info = doc.page_info(5)
        for w in doc.words(5):
            assert -1 <= w.rect.x0 <= info.width + 1
            assert -1 <= w.rect.y1 <= info.height + 1
    finally:
        doc.close()


def test_outline(fixture_paths):
    doc = PdfDocument.open(fixture_paths["sample.pdf"])
    try:
        outline = doc.outline()
        titles = [e.title for e in outline]
        assert titles[0] == "Cover"
        assert "Signatures" in titles
        images = next(e for e in outline if e.title == "Images")
        assert images.level == 2 and images.page_index == 9
        assert images.y == pytest.approx(300, abs=1)
    finally:
        doc.close()
    plain = PdfDocument.open(fixture_paths["plain.pdf"])
    try:
        assert plain.outline() == []
    finally:
        plain.close()


def test_open_errors(tmp_path, fixture_paths):
    with pytest.raises(PdfOpenError):
        PdfDocument.open(str(tmp_path / "missing.pdf"))
    bogus = tmp_path / "bogus.pdf"
    bogus.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfOpenError):
        PdfDocument.open(str(bogus))
    with pytest.raises(PasswordRequiredError) as info:
        PdfDocument.open(fixture_paths["encrypted.pdf"])
    assert not info.value.wrong_password


def _imports(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_layering_rules():
    """pdf/ and core/ are Qt-free; ui/ and core/ never touch PyMuPDF."""
    for sub in ("pdf", "core"):
        for f in (PKG / sub).rglob("*.py"):
            assert "PyQt6" not in _imports(f), f"{f} imports Qt"
    for sub in ("ui", "core"):
        for f in (PKG / sub).rglob("*.py"):
            bad = _imports(f) & {"fitz", "pymupdf"}
            assert not bad, f"{f} imports {bad}"
