"""Generate the sample PDFs used by tests and demos.

Run: ``python -m pdf_studio.tests.fixtures.make_fixtures`` (from ``src``).
Files are written next to this script. Generation is deterministic.

* ``sample.pdf``    12 pages, mixed sizes (A4, landscape, Letter), one page
                    with /Rotate 90, nested outline, an image, one highlight.
                    Every page contains the token ``PAGE-MARKER-<n>``.
* ``plain.pdf``     2 pages, no outline, no annotations.
* ``long.pdf``      400 pages for performance/virtualization tests.
* ``encrypted.pdf`` 3 pages, user password ``secret`` (owner ``owner``).
"""

from __future__ import annotations

import os
import sys

import pymupdf as fitz

HERE = os.path.dirname(os.path.abspath(__file__))

LOREM = (
    "Portable Document Format files carry fixed-layout pages: every glyph, "
    "line and image sits at an exact position. Editing therefore means "
    "working with boxes rather than flowing paragraphs. This sample text "
    "gives the viewer something realistic to render, select and search. "
    "The quick brown fox jumps over the lazy dog."
)

A4 = fitz.paper_rect("a4")
LETTER = fitz.paper_rect("letter")
A4_LANDSCAPE = fitz.paper_rect("a4-l")

INK = (0.13, 0.15, 0.19)
MUTED = (0.42, 0.45, 0.5)
ACCENT = (0.15, 0.39, 0.92)


def _gradient_png(w: int = 240, h: int = 140) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    for y in range(h):
        for x in range(w):
            r = int(40 + 180 * x / w)
            g = int(90 + 120 * y / h)
            b = 200
            pix.set_pixel(x, y, (r, g, b))
    return pix.tobytes("png")


def _page_heading(page: fitz.Page, n: int, title: str) -> None:
    w = page.rect.width
    page.draw_rect(fitz.Rect(0, 0, w, 6), color=None, fill=ACCENT)
    page.insert_text((56, 70), title, fontsize=22, fontname="hebo", color=INK)
    page.insert_text((56, 92), f"Section {n}  ·  PAGE-MARKER-{n}", fontsize=10,
                     fontname="helv", color=MUTED)
    page.draw_line((56, 104), (w - 56, 104), color=(0.85, 0.86, 0.88), width=0.8)


def _body(page: fitz.Page, top: float, paragraphs: int = 4) -> float:
    w = page.rect.width
    y = top
    for i in range(paragraphs):
        doubled = bool(i % 2)
        h = 150 if doubled else 80
        rect = fitz.Rect(56, y, w - 56, y + h)
        page.insert_textbox(rect, LOREM + (" " + LOREM if doubled else ""),
                            fontsize=10.5, fontname="helv", color=INK, lineheight=1.45)
        y += h + 8
    return y


def _footer(page: fitz.Page, n: int, total: int) -> None:
    r = page.rect
    page.insert_text((r.width / 2 - 20, r.height - 30), f"{n} / {total}",
                     fontsize=9, fontname="helv", color=MUTED)


def make_sample(path: str) -> None:
    doc = fitz.open()
    total = 12
    png = _gradient_png()
    for n in range(1, total + 1):
        if n == 5:
            rect = A4_LANDSCAPE
        elif n == 8:
            rect = LETTER
        else:
            rect = A4
        page = doc.new_page(width=rect.width, height=rect.height)
        if n == 1:
            page.draw_rect(fitz.Rect(0, 0, rect.width, 260), color=None, fill=(0.94, 0.95, 0.97))
            page.insert_text((56, 150), "PDF Studio", fontsize=40, fontname="hebo", color=INK)
            page.insert_text((56, 185), "Sample document for viewing, annotating and editing",
                             fontsize=14, fontname="helv", color=MUTED)
            page.insert_text((56, 230), "PAGE-MARKER-1", fontsize=10, fontname="helv", color=MUTED)
            _body(page, 300, 4)
        else:
            _page_heading(page, n, f"Chapter {n - 1}: " + [
                "Rendering", "Annotations", "Search", "Wide layouts", "Rotation",
                "Page tools", "Letter paper", "Forms of text", "Images",
                "Redaction", "Signatures"][n - 2])
            y = _body(page, 120, 3 if n != 5 else 2)
            if n == 4:
                page.insert_image(fitz.Rect(56, y + 10, 296, y + 150), stream=png)
                page.insert_text((310, y + 40), "Figure 1. A generated gradient image.",
                                 fontsize=10, fontname="helv", color=MUTED)
            if n == 3:
                page.insert_text((56, y + 30), "Highlight me: important sentence here.",
                                 fontsize=12, fontname="helv", color=INK)
            if n == 6:
                # a small bar chart drawn with vector graphics
                for i, h in enumerate([60, 110, 80, 140, 100]):
                    x = 70 + i * 50
                    page.draw_rect(fitz.Rect(x, y + 170 - h, x + 32, y + 170),
                                   color=None, fill=(0.15 + i * 0.12, 0.39, 0.92 - i * 0.1))
        _footer(page, n, total)

    # one pre-existing annotation on page 3
    p3 = doc[2]
    hits = p3.search_for("important sentence")
    if hits:
        a = p3.add_highlight_annot(hits[0])
        a.set_info(content="Pre-existing highlight", title="Fixture")
        a.update()
    # page 6 is stored rotated
    doc[5].set_rotation(90)

    toc = [
        [1, "Cover", 1],
        [1, "Part I — Viewing", 2],
        [2, "Rendering", 2],
        [2, "Annotations", 3],
        [2, "Search", 4],
        [1, "Part II — Layout", 5],
        [2, "Wide layouts", 5],
        [2, "Rotation", 6],
        [1, "Part III — Editing", 7],
        [2, "Page tools", 7],
        [2, "Images", 10, {"kind": fitz.LINK_GOTO, "to": fitz.Point(0, 300), "page": 9}],
        [2, "Signatures", 12],
    ]
    doc.set_toc(toc)
    doc.set_metadata({"title": "PDF Studio Sample", "author": "PDF Studio tests"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_plain(path: str) -> None:
    doc = fitz.open()
    for n in range(1, 3):
        page = doc.new_page(width=A4.width, height=A4.height)
        _page_heading(page, n, f"Plain page {n}")
        _body(page, 120, 2)
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_long(path: str, pages: int = 400) -> None:
    doc = fitz.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=A4.width, height=A4.height)
        page.insert_text((56, 70), f"Long document page {n}", fontsize=18, fontname="hebo", color=INK)
        page.insert_text((56, 92), f"PAGE-MARKER-{n}", fontsize=10, fontname="helv", color=MUTED)
        page.insert_textbox(fitz.Rect(56, 120, A4.width - 56, 400), LOREM,
                            fontsize=10.5, fontname="helv", color=INK)
        if n % 50 == 0:
            page.insert_text((56, 430), "needle in a haystack", fontsize=11, fontname="helv")
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_encrypted(path: str) -> None:
    doc = fitz.open()
    for n in range(1, 4):
        page = doc.new_page(width=A4.width, height=A4.height)
        _page_heading(page, n, f"Encrypted page {n}")
        _body(page, 120, 1)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret",
             owner_pw="owner", permissions=-1, garbage=3, deflate=True)
    doc.close()


FIXTURES = {
    "sample.pdf": make_sample,
    "plain.pdf": make_plain,
    "long.pdf": make_long,
    "encrypted.pdf": make_encrypted,
}


def ensure_fixtures(directory: str = HERE, force: bool = False) -> dict:
    paths = {}
    for name, fn in FIXTURES.items():
        p = os.path.join(directory, name)
        if force or not os.path.exists(p):
            fn(p)
        paths[name] = p
    return paths


if __name__ == "__main__":
    out = ensure_fixtures(force="--force" in sys.argv)
    for name, p in out.items():
        print(f"{name}: {os.path.getsize(p):,} bytes")
