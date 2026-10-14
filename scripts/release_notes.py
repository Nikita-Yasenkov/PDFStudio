"""Print the GitHub release notes for a version.

    python scripts/release_notes.py 0.1.1 dist/PDFStudio-win64.zip

The notes are the version's section of CHANGELOG.md, followed by install
steps and the SHA-256 of the zip. Exits with an error when CHANGELOG.md has
no section for the version, so a release can't go out without notes.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

INSTALL = """\
### Get started

1. Download **PDFStudio-win64.zip** below. It needs Windows 10 or 11, 64-bit.
2. Unzip it and run `PDFStudio\\PDFStudio.exe`. No install is needed.
3. Optional: open **Help → PDF Studio on This Computer…** and click **Install for this user**. \
This adds Desktop and Start-menu shortcuts and registers PDF Studio for `.pdf` files, \
without administrator rights.

**Updating:** close PDF Studio, then run the new version and install it the same way. \
Your settings and saved signatures are kept.

The app isn't code-signed yet, so Windows SmartScreen may show "Windows protected your PC" \
the first time. Click **More info → Run anyway**."""


def changelog_section(version: str, path: str = os.path.join(ROOT, "CHANGELOG.md")) -> str:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m is None or not m.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no '## {version}' section: add one before releasing.")
    return m.group(1).strip()


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def notes(version: str, zip_path: str) -> str:
    return (f"{changelog_section(version)}\n\n{INSTALL}\n\n"
            f"**SHA-256** `{os.path.basename(zip_path)}`: `{sha256(zip_path)}`\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(notes(sys.argv[1], sys.argv[2]))
