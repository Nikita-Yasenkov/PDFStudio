"""Ticket 12: Save / Save As (engine + session) and close prompts (UI)."""

from __future__ import annotations

import os

import pytest

from pdf_studio.core.commands import Command
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PasswordRequiredError, PdfDocument, PdfSaveError
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.dialogs import prompts


class SetTitle(Command):
    label = "Set title"

    def __init__(self, title):
        self.title = title
        self.old = None

    def execute(self, s):
        self.old = s.doc.set_metadata({"title": self.title})["title"]

    def undo(self, s):
        s.doc.set_metadata({"title": self.old})


def _read(path):
    with open(path, "rb") as f:
        return f.read()


# ------------------------------------------------------------------ engine
def test_incremental_save_to_backing_file(sample_pdf):
    size0 = os.path.getsize(sample_pdf)
    doc = PdfDocument.open(sample_pdf)
    doc.set_metadata({"title": "Changed"})
    res = doc.save()
    assert res.incremental and res.path == os.path.abspath(sample_pdf)
    assert os.path.getsize(sample_pdf) > size0  # appended update
    doc.close()
    again = PdfDocument.open(sample_pdf)
    assert again.metadata()["title"] == "Changed"
    again.close()


def test_save_as_leaves_original_untouched(sample_pdf, tmp_path):
    before = _read(sample_pdf)
    doc = PdfDocument.open(sample_pdf)
    doc.set_metadata({"title": "Copy"})
    out = str(tmp_path / "out.pdf")
    res = doc.save(out)
    assert not res.incremental and doc.path == out
    assert _read(sample_pdf) == before
    doc.close()
    copy = PdfDocument.open(out)
    assert copy.metadata()["title"] == "Copy" and copy.page_count == 12
    copy.close()


def test_full_save_over_open_file_keeps_document_usable(sample_pdf):
    doc = PdfDocument.open(sample_pdf)
    doc.requires_full_save = True  # as after a redaction
    doc.set_metadata({"title": "Rewritten"})
    res = doc.save()
    assert not res.incremental
    # the open document still works after swapping to its in-memory copy
    assert doc.render(0, 0.25).width > 0
    doc.set_metadata({"title": "Twice"})
    doc.save()
    doc.close()
    again = PdfDocument.open(sample_pdf)
    assert again.metadata()["title"] == "Twice"
    again.close()
    assert not any(n.endswith(".tmp") for n in os.listdir(os.path.dirname(sample_pdf)))


def test_save_keeps_encryption(encrypted_pdf, tmp_path):
    doc = PdfDocument.open(encrypted_pdf, "secret")
    doc.set_metadata({"title": "Still secret"})
    out = str(tmp_path / "enc-out.pdf")
    doc.save(out)
    doc.close()
    with pytest.raises(PasswordRequiredError):
        PdfDocument.open(out)
    again = PdfDocument.open(out, "secret")
    assert again.metadata()["title"] == "Still secret"
    again.close()


def test_save_to_missing_folder_fails_cleanly(sample_pdf, tmp_path):
    doc = PdfDocument.open(sample_pdf)
    with pytest.raises(PdfSaveError):
        doc.save(str(tmp_path / "nope" / "x.pdf"))
    doc.close()


# ----------------------------------------------------------------- session
def test_session_save_clears_dirty_and_save_as_updates_path(sample_pdf, tmp_path):
    s = DocumentSession.open(sample_pdf)
    s.stack.push(SetTitle("A"))
    assert s.dirty
    s.save()
    assert not s.dirty
    s.stack.push(SetTitle("B"))
    out = str(tmp_path / "renamed.pdf")
    s.save(out)
    assert s.path == out and s.display_name == "renamed.pdf" and not s.dirty
    # undo history survives saving
    s.stack.undo()
    assert s.dirty and s.doc.metadata()["title"] == "A"
    s.close()


# ---------------------------------------------------------------------- UI
def _dirty(window):
    v = window.view
    v.execute(SetTitle("dirty"))
    assert v.session.dirty
    return v


def test_close_prompt_cancel_keeps_tab(window, sample_pdf, monkeypatch):
    window.open_path(sample_pdf)
    v = _dirty(window)
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.CANCEL)
    assert not window.close_view(v)
    assert window.view is v and not v.session.doc.closed


def test_close_prompt_discard_closes_without_saving(window, sample_pdf, monkeypatch):
    before = _read(sample_pdf)
    window.open_path(sample_pdf)
    v = _dirty(window)
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.DISCARD)
    assert window.close_view(v)
    assert window.view is None
    assert _read(sample_pdf) == before


def test_close_prompt_save_writes_file(window, sample_pdf, monkeypatch):
    window.open_path(sample_pdf)
    v = _dirty(window)
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.SAVE)
    assert window.close_view(v)
    doc = PdfDocument.open(sample_pdf)
    assert doc.metadata()["title"] == "dirty"
    doc.close()


def test_save_as_updates_tab_and_recent(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = _dirty(window)
    out = str(tmp_path / "saved-as.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    window.actions["save_as"].trigger()
    assert v.session.path == out
    assert window.tabs.tabText(window.tabs.currentIndex()) == "saved-as.pdf"
    assert window.settings.recent_files[0] == out
    assert not v.session.dirty
    assert not window.actions["save"].isEnabled()
    assert "Saved" in v.toast_widget.label.text()


def test_save_action_enabled_only_when_dirty(window, sample_pdf):
    window.open_path(sample_pdf)
    assert not window.actions["save"].isEnabled()
    _dirty(window)
    assert window.actions["save"].isEnabled()
    window.actions["save"].trigger()
    assert not window.view.session.dirty


def test_window_close_with_dirty_doc_can_be_cancelled(window, sample_pdf, monkeypatch):
    window.open_path(sample_pdf)
    _dirty(window)
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.CANCEL)
    window.close()
    pump()
    assert window.isVisible()
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.DISCARD)
