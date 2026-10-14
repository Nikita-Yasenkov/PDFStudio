"""Print the GitHub release notes for a version.

    python scripts/release_notes.py 0.1.2 dist/PDFStudio-win64.zip dist/PDFStudio-macos-arm64.dmg
    python scripts/release_notes.py --check 0.1.2

The notes are the version's section of CHANGELOG.md, followed by install
steps and the SHA-256 of each file. Exits with an error when CHANGELOG.md has
no section for the version, so a release can't go out without notes;
``--check`` only does that test.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

INSTALL = """\
### Get started

**Windows** (10 or 11, 64-bit): download **PDFStudio-win64.zip**, unzip it and run \
`PDFStudio\\PDFStudio.exe`. To add shortcuts and open PDFs with it, use \
**Help → PDF Studio on This Computer… → Install for this user** (no administrator rights \
needed). If SmartScreen shows "Windows protected your PC", click **More info → Run anyway**.

**macOS** (13 Ventura or later): download the disk image for your Mac \
(**arm64** for Apple silicon, **x86_64** for Intel), open it and drag PDF Studio to \
Applications. The app isn't notarized by Apple yet, so the first time macOS says it can't \
verify it: open **System Settings → Privacy & Security**, scroll down and click \
**Open Anyway**.

**Updating:** close PDF Studio and install the new version the same way. Your settings and \
saved signatures are kept."""


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


def notes(version: str, files: list) -> str:
    sums = "\n".join(f"- `{os.path.basename(p)}`: `{sha256(p)}`" for p in sorted(files))
    return f"{changelog_section(version)}\n\n{INSTALL}\n\n**SHA-256**\n\n{sums}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Release notes from CHANGELOG.md")
    ap.add_argument("version")
    ap.add_argument("files", nargs="*", help="release assets to list with their SHA-256")
    ap.add_argument("--check", action="store_true", help="only check that the notes exist")
    args = ap.parse_args()
    if args.check:
        changelog_section(args.version)
        print(f"CHANGELOG.md has notes for {args.version}")
        return 0
    if not args.files:
        ap.error("give the release files, or use --check")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(notes(args.version, args.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
