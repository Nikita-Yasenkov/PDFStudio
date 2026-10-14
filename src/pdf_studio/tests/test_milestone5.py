"""Ticket 42: Milestone 5 integration — long document in every view mode,
bounded caches, encrypted open + edit + save, packaging smoke seam."""

from __future__ import annotations

import os

import pytest

from pdf_studio.core.layout import ViewMode
from pdf_studio.core.render_cache import WHOLE_PAGE
from pdf_studio.pdf import PasswordRequiredError, PdfDocument
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.dialogs import prompts


def test_long_document_in_all_view_modes(window, long_pdf):
    cache = window.render_service.cache
    cache.max_bytes = 48 * 1024 * 1024
    window.open_path(long_pdf)
    v = window.view
    for mode, key in ((ViewMode.CONTINUOUS, "mode_continuous"), (ViewMode.SINGLE, "mode_single"),
                      (ViewMode.TWO_PAGE, "mode_two")):
        window.actions[key].trigger()
        assert v.view_mode == mode
        v.go_to_page(199)
        pump(80)
        assert v.current_page in (198, 199)  # spreads start on even pages
        window.actions["next_page"].trigger()
        assert v.current_page == 200  # next page, or the next spread (200-201)
        window.actions["fit_page"].trigger()
        window.actions["last_page"].trigger()
        pump(80)
        visible = v.canvas.visible_pages()
        assert 399 in visible or (mode == ViewMode.TWO_PAGE and 398 in visible)
        assert cache.total_bytes <= cache.max_bytes
    uid = v.session.uid
    tiles = {k.page_id for k in cache if k.doc == uid and k.tx != WHOLE_PAGE}
    assert len(tiles) < 60  # never rasterised the whole 400-page document
    assert not window.errors


def test_encrypted_open_edit_save_cycle(window, encrypted_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "ask_password", lambda parent, name, wrong=False: "secret")
    assert window.open_path(encrypted_pdf)
    v = window.view
    assert v.session.is_encrypted and not v.session.dirty
    window.actions["insert_blank_end"].trigger()
    assert v.session.dirty and v.title.endswith("*")
    window.actions["save"].trigger()  # incremental save into the encrypted file
    assert not v.session.dirty
    with pytest.raises(PasswordRequiredError):
        PdfDocument.open(encrypted_pdf)
    d = PdfDocument.open(encrypted_pdf, "secret")
    assert d.page_count == 4
    d.close()


def test_packaging_smoke_entry_point(qapp, sample_pdf, tmp_path):
    """The frozen app's --smoke path, exercised in-process (no dialogs)."""
    from pdf_studio.ui.app import smoke
    log = str(tmp_path / "smoke.txt")
    assert smoke([sample_pdf], log) == 0
    text = open(log, encoding="utf-8").read()
    assert "OK sample.pdf: 12 pages" in text and "PyMuPDF" in text
    assert smoke([str(tmp_path / "missing.pdf")], log) == 1
    assert "FAIL" in open(log, encoding="utf-8").read()


def test_build_script_strips_conflicting_dlls(tmp_path):
    from pdf_studio.packaging.build_windows import remove_conflicting_dlls
    internal = tmp_path / "_internal"
    internal.mkdir()
    for name in ("icuuc.dll", "icudt58.dll", "python312.dll"):
        (internal / name).write_bytes(b"x")
    (internal / "_tcl_data").mkdir()
    removed = remove_conflicting_dlls(str(internal))
    assert set(removed) == {"icuuc.dll", "icudt58.dll", "_tcl_data"}
    assert os.listdir(internal) == ["python312.dll"]
