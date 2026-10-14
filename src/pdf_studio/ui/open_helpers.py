"""Opening PDFs from the UI with password prompts and friendly errors."""

from __future__ import annotations

import os
from typing import Optional, Tuple

from PyQt6.QtWidgets import QWidget

from ..pdf import PasswordRequiredError, PdfDocument, PdfOpenError
from .dialogs import prompts

MAX_PASSWORD_ATTEMPTS = 5


def open_pdf_interactive(parent: QWidget, path: str,
                         purpose: str = "Could not open file") -> Optional[PdfDocument]:
    """Open ``path``; asks for a password when needed (retrying on a wrong
    one). Returns None if the user cancels or the file can't be read — in the
    latter case an error is shown. Never leaves a half-open document."""
    password: Optional[str] = None
    wrong = False
    for _attempt in range(MAX_PASSWORD_ATTEMPTS + 1):
        try:
            return PdfDocument.open(path, password)
        except PasswordRequiredError as exc:
            wrong = exc.wrong_password
            password = prompts.ask_password(parent, os.path.basename(path), wrong)
            if password is None:
                return None
        except PdfOpenError as exc:
            prompts.show_error(parent, purpose, str(exc))
            return None
    prompts.show_error(parent, purpose, "Too many incorrect password attempts.")
    return None


def read_pdf_bytes(parent: QWidget, path: str) -> Optional[Tuple[bytes, PdfDocument]]:
    """Open a PDF for page insertion: returns (file bytes, open document)."""
    doc = open_pdf_interactive(parent, path, "Could not read PDF")
    if doc is None:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        doc.close()
        prompts.show_error(parent, "Could not read PDF", str(exc))
        return None
    return data, doc
