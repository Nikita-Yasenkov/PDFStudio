"""Engine-level exceptions. Qt-free."""

from __future__ import annotations


class PdfError(Exception):
    """Base class for all engine errors."""


class PdfOpenError(PdfError):
    """The file could not be opened as a PDF (missing, corrupt, not a PDF)."""


class PasswordRequiredError(PdfOpenError):
    """The PDF is encrypted and a password is needed to open it."""

    def __init__(self, path: str, wrong_password: bool = False):
        self.path = path
        self.wrong_password = wrong_password
        msg = "Incorrect password" if wrong_password else "Password required"
        super().__init__(f"{msg}: {path}")


class PdfSaveError(PdfError):
    """Writing the document failed."""


class AnnotationError(PdfError):
    """An annotation could not be created, found, or modified."""


class PageOperationError(PdfError):
    """A page-structure operation was invalid (bad index, last page, ...)."""


class EditError(PdfError):
    """A content edit (redact/replace/image) failed."""
