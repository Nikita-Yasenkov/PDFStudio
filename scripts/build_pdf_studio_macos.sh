#!/usr/bin/env bash
# Build the macOS app of PDF Studio.
#
# From the repo root, on a Mac:
#   scripts/build_pdf_studio_macos.sh           # build dist/PDF Studio.app + dist/PDFStudio-macos-<arch>.dmg
#   scripts/build_pdf_studio_macos.sh --smoke   # ...and run the bundled app's self-test first
#
# Uses a dedicated uv virtual environment (build/venv) containing only PyQt6,
# PyMuPDF and PyInstaller at the versions pinned in
# src/pdf_studio/packaging/requirements-build.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv > /dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
[ -x build/venv/bin/python ] || uv venv build/venv --python 3.12
uv pip install --python build/venv/bin/python -r src/pdf_studio/packaging/requirements-build.txt -q
PYTHONPATH=src build/venv/bin/python -m pdf_studio.packaging.build_macos --work build/pyinstaller "$@"
