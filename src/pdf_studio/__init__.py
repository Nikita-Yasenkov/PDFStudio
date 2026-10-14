"""PDF Studio — a desktop PDF editor built on PyQt6 and PyMuPDF.

Layers (imports only flow downwards):

* ``pdf_studio.ui``   — PyQt6 widgets, dialogs, tools, theming.
* ``pdf_studio.core`` — Qt-free application logic: sessions, commands,
  layout, coordinates, render cache/queue, selection, search.
* ``pdf_studio.pdf``  — Qt-free PyMuPDF facade. The only package that
  imports ``pymupdf``.
"""

APP_NAME = "PDF Studio"
APP_ID = "PDFStudio"
ORG_NAME = "codekg"
__version__ = "0.1.1"
