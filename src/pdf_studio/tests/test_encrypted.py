"""Tickets 38/42: encrypted PDFs — engine unlock + password prompt UX."""

from __future__ import annotations

import os

import pytest

from pdf_studio.pdf import PasswordRequiredError, PdfDocument
from pdf_studio.ui.dialogs import prompts


# ------------------------------------------------------------------ engine
def test_engine_unlock_success_and_failure(encrypted_pdf):
    with pytest.raises(PasswordRequiredError) as info:
        PdfDocument.open(encrypted_pdf)
    assert not info.value.wrong_password
    with pytest.raises(PasswordRequiredError) as info:
        PdfDocument.open(encrypted_pdf, "wrong")
    assert info.value.wrong_password
    doc = PdfDocument.open(encrypted_pdf, "secret")
    assert doc.page_count == 3 and doc.is_encrypted and doc.password == "secret"
    assert "Encrypted page 1" in doc.page_text(0)
    doc.close()
    owner = PdfDocument.open(encrypted_pdf, "owner")  # owner password works too
    assert owner.page_count == 3
    owner.close()


def test_empty_user_password_opens_without_prompt(tmp_path):
    import pymupdf as fitz  # fixture helper only
    path = str(tmp_path / "owner-only.pdf")
    d = fitz.open()
    d.new_page().insert_text((72, 72), "owner protected only")
    d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
           permissions=fitz.PDF_PERM_PRINT)
    d.close()
    doc = PdfDocument.open(path)  # no password needed
    assert doc.is_encrypted and doc.password is None
    assert "owner protected only" in doc.page_text(0)
    doc.close()


# ---------------------------------------------------------------------- UI
def test_ui_prompts_and_opens_with_correct_password(window, encrypted_pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(prompts, "ask_password",
                        lambda parent, name, wrong=False: calls.append((name, wrong)) or "secret")
    assert window.open_path(encrypted_pdf)
    assert calls == [(os.path.basename(encrypted_pdf), False)]
    v = window.view
    assert v.page_count == 3 and not v.session.dirty
    # the tab shows a lock icon for encrypted documents
    assert not window.tabs.tabIcon(window.tabs.currentIndex()).isNull()


def test_ui_wrong_password_reprompts(window, encrypted_pdf, monkeypatch):
    answers = iter(["nope", "still-no", "secret"])
    wrong_flags = []
    monkeypatch.setattr(prompts, "ask_password",
                        lambda parent, name, wrong=False: wrong_flags.append(wrong) or next(answers))
    assert window.open_path(encrypted_pdf)
    assert wrong_flags == [False, True, True]
    assert window.tabs.count() == 1


def test_ui_cancel_leaves_no_tab(window, encrypted_pdf, monkeypatch):
    monkeypatch.setattr(prompts, "ask_password", lambda parent, name, wrong=False: None)
    assert not window.open_path(encrypted_pdf)
    assert window.tabs.count() == 0 and window.view is None
    assert not window.errors  # cancelling is not an error
    assert encrypted_pdf not in window.settings.recent_files


def test_ui_gives_up_after_repeated_wrong_passwords(window, encrypted_pdf, monkeypatch):
    monkeypatch.setattr(prompts, "ask_password", lambda parent, name, wrong=False: "bad")
    assert not window.open_path(encrypted_pdf)
    assert window.tabs.count() == 0
    assert window.errors  # explains what happened


def test_encrypted_document_edit_and_save_keeps_encryption(window, encrypted_pdf, tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(prompts, "ask_password", lambda parent, name, wrong=False: "secret")
    window.open_path(encrypted_pdf)
    window.actions["rotate_right"].trigger()
    out = str(tmp_path / "enc-saved.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    assert window.save_as()
    with pytest.raises(PasswordRequiredError):
        PdfDocument.open(out)
    d = PdfDocument.open(out, "secret")
    assert d.page_info(0).rotation == 90
    d.close()


def test_password_dialog_widget(window):
    from pdf_studio.ui.dialogs.password import PasswordDialog
    dlg = PasswordDialog(window, "locked.pdf", wrong=True)
    assert not dlg.error.isHidden()
    dlg.edit.setText("pw")
    assert dlg.password() == "pw"
    dlg.close()
