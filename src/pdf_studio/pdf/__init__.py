"""Qt-free PDF engine facade (the only package that imports PyMuPDF)."""

from .engine import ENGINE_LOCK, PdfDocument, library_versions, open_document
from .errors import (AnnotationError, EditError, PageOperationError,
                     PasswordRequiredError, PdfError, PdfOpenError, PdfSaveError)
from .models import (AnnotationKind, AnnotationModel, Color, ImageInfo,
                     OutlineEntry, PageInfo, PlacedObject, Point, Rect, RenderedImage,
                     SaveResult, SearchHit, TextStyle, Word)

__all__ = [
    "ENGINE_LOCK", "PdfDocument", "library_versions", "open_document",
    "AnnotationError", "EditError", "PageOperationError", "PasswordRequiredError",
    "PdfError", "PdfOpenError", "PdfSaveError",
    "AnnotationKind", "AnnotationModel", "Color", "ImageInfo", "OutlineEntry",
    "PageInfo", "PlacedObject", "Point", "Rect", "RenderedImage", "SaveResult", "SearchHit",
    "TextStyle", "Word",
]
