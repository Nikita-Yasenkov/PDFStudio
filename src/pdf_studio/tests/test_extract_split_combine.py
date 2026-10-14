"""Ticket 25: extract, split and combine (engine helpers + UI flows)."""

from __future__ import annotations

import os

import pytest

from pdf_studio.pdf import PageOperationError, PdfDocument, PdfSaveError
from pdf_studio.pdf.documents import (bookmark_groups, extract_pages, merge_files,
                                      safe_filename, split_document)
from pdf_studio.tests.test_page_ops_engine import markers
from pdf_studio.ui.dialogs import prompts


def _read(path):
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- engine
def test_extract_leaves_original_untouched(sample_pdf, tmp_path):
    before = _read(sample_pdf)
    doc = PdfDocument.open(sample_pdf)
    out = extract_pages(doc, [4, 1, 2], str(tmp_path / "ex.pdf"))
    doc.close()
    assert _read(sample_pdf) == before
    ex = PdfDocument.open(out)
    assert markers(ex) == [5, 2, 3]
    assert len(ex.annotations(2)) == 1  # annotation travelled with page 3
    ex.close()


def test_split_strategies(sample_pdf, tmp_path):
    doc = PdfDocument.open(sample_pdf)
    try:
        paths = split_document(doc, [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11]],
                               str(tmp_path), "sample")
        assert [os.path.basename(p) for p in paths] == ["sample-part1.pdf", "sample-part2.pdf",
                                                        "sample-part3.pdf"]
        counts = []
        for p in paths:
            d = PdfDocument.open(p)
            counts.append(d.page_count)
            d.close()
        assert counts == [5, 5, 2]
        groups = bookmark_groups(doc)
        assert groups[0] == [0] and groups[1] == [1, 2, 3] and sum(map(len, groups)) == 12
        with pytest.raises(PdfSaveError):
            split_document(doc, [[0]], str(tmp_path / "missing"), "x")
    finally:
        doc.close()


def test_merge_paths_and_bytes(fixture_paths):
    data = merge_files([(fixture_paths["plain.pdf"], None),
                        (open(fixture_paths["encrypted.pdf"], "rb").read(), "secret")],
                       titles=["Plain", "Secret"])
    d = PdfDocument.from_bytes(data, "merged.pdf")
    try:
        assert d.page_count == 5
        assert "Encrypted page 1" in d.page_text(2)
        titles = [e.title for e in d.outline() if e.level == 1]
        assert titles == ["Plain", "Secret"]
        assert d.path is None and d.name == "merged.pdf"
    finally:
        d.close()
    with pytest.raises(PageOperationError):
        merge_files([(fixture_paths["encrypted.pdf"], None)])
    with pytest.raises(PageOperationError):
        merge_files([])


def test_safe_filename():
    assert safe_filename('a/b:c*?"<>|.pdf') == "a_b_c_.pdf"
    assert safe_filename("...") == "document"


# --------------------------------------------------------------------- UI
def test_extract_flow_with_delete_and_open(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    out = str(tmp_path / "picked.pdf")
    monkeypatch.setattr(prompts, "ask_extract", lambda parent, doc, name, initial: ([2, 3], True, True))
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    assert window.extract_pages()
    assert v.page_count == 10 and v.session.dirty  # pages removed (undoable)
    assert window.view.session.path == out  # extracted file opened in a new tab
    assert markers(window.view.session.doc) == [3, 4]
    window.activate_view(v)
    window.actions["undo"].trigger()
    assert v.page_count == 12


def test_split_flow(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    monkeypatch.setattr(prompts, "ask_split",
                        lambda parent, count, name, folder, groups: ([[0, 1], [2, 3, 4], list(range(5, 12))],
                                                                     str(tmp_path)))
    assert window.split_document()
    names = sorted(n for n in os.listdir(tmp_path) if "-part" in n)
    assert names == ["sample-part1.pdf", "sample-part2.pdf", "sample-part3.pdf"]
    assert not window.view.session.dirty  # write-only: no undo entry


def test_combine_into_new_tab_includes_unsaved_edits(window, sample_pdf, fixture_paths,
                                                     monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    window.actions["delete_page"].trigger()  # unsaved change: 11 pages in memory
    monkeypatch.setattr(prompts, "ask_combine",
                        lambda parent, initial, cur, d, counter: ([sample_pdf, fixture_paths["plain.pdf"]], 0))
    assert window.combine_files()
    new = window.view
    assert new is not v and new.session.path is None
    assert new.session.page_count == 11 + 2
    assert new.session.dirty and new.title.startswith("Combined.pdf")
    assert window.tabs.count() == 2


def test_combine_append_is_one_undo_step(window, sample_pdf, fixture_paths, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    monkeypatch.setattr(prompts, "ask_combine",
                        lambda parent, initial, cur, d, counter: ([fixture_paths["plain.pdf"],
                                                                   fixture_paths["plain.pdf"]], 1))
    assert window.combine_files()
    assert v.page_count == 16
    assert len(v.session.stack) == 1
    window.actions["undo"].trigger()
    assert v.page_count == 12


def test_dialogs_construct(window, sample_pdf, fixture_paths):
    from pdf_studio.ui.dialogs.page_tools import CombineDialog, ExtractDialog, SplitDialog
    window.open_path(sample_pdf)
    v = window.view
    ex = ExtractDialog(window, v.session.doc, "sample.pdf", [2])
    assert ex.values() == ([2], True, False)
    sp = SplitDialog(window, 12, "sample.pdf", os.path.dirname(sample_pdf), bookmark_groups(v.session.doc))
    sp.every.setValue(5)
    assert [len(g) for g in sp.values()[0]] == [5, 5, 2]
    assert sp.ok.isEnabled() and "3 files" in sp.ok.text()
    sp.r_before.setChecked(True)
    sp.before.setText("4, 9")
    assert [len(g) for g in sp.groups()] == [3, 5, 4]
    cb = CombineDialog(window, [fixture_paths["plain.pdf"]], "sample.pdf", ".",
                       window._page_count_of)
    assert not cb.ok.isEnabled()  # need two files for a new document
    cb.add_path(fixture_paths["sample.pdf"])
    assert cb.ok.isEnabled() and "2 page" in cb.list.item(0).text()
    for d in (ex, sp, cb):
        d.close()
