"""Content edits: redact, redact-and-replace text, cover-and-place image
(Qt-free).

All rects are displayed page space. Redaction is real: text under the box
is removed from the content stream (images and vector art are left in place
and covered by the fill). Replacement text is box-bound (no reflow).

Undo uses :class:`ContentSnapshot`: the page's /Contents and /Resources
entries plus every content stream, resource sub-dictionary and form XObject
they reference, captured before the edit and written back on undo. Object
numbers are stable for the document's lifetime (saves never renumber the
in-memory document, see ``PdfDocument.save``).
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import pymupdf as fitz

from .errors import EditError
from .models import Color, Rect, TextStyle

_REF = re.compile(r"(\d+) 0 R")
_RES_KEYS = ("Font", "XObject", "ExtGState", "ColorSpace", "Pattern", "Shading", "Properties")

#: base-14 font aliases used for replacement text: (regular, bold, italic, bold-italic)
_FAMILIES = {
    "sans": ("helv", "hebo", "heit", "hebi"),
    "serif": ("tiro", "tibo", "tiit", "tibi"),
    "mono": ("cour", "cobo", "coit", "cobi"),
}
FONT_CHOICES = (("Sans-serif (Helvetica)", "sans"), ("Serif (Times)", "serif"),
                ("Monospace (Courier)", "mono"))


# ---------------------------------------------------------------- geometry
def _unrot(page: fitz.Page, r: Rect) -> fitz.Rect:
    fr = fitz.Rect(r.as_tuple())
    if page.rotation:
        fr = fr * page.derotation_matrix
    return fr.normalize()


def _disp(page: fitz.Page, r) -> Rect:
    fr = fitz.Rect(r)
    if page.rotation:
        fr = fr * page.rotation_matrix
    fr = fr.normalize()
    return Rect(fr.x0, fr.y0, fr.x1, fr.y1)


# ---------------------------------------------------------------- snapshot
@dataclass
class ContentSnapshot:
    page_keys: Dict[str, Tuple[str, str]]
    objects: Dict[int, Tuple[str, Optional[bytes]]] = field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        return sum(len(s) + (len(b) if b else 0) for s, b in self.objects.values())


def snapshot_content(doc: fitz.Document, page: fitz.Page) -> ContentSnapshot:
    snap = ContentSnapshot({k: doc.xref_get_key(page.xref, k) for k in ("Contents", "Resources")})

    def grab(x: int) -> None:
        if x in snap.objects or x <= 0:
            return
        stream = doc.xref_stream(x) if doc.xref_is_stream(x) else None
        snap.objects[x] = (doc.xref_object(x, compressed=False), stream)

    def refs_of(t: str, v: str) -> List[int]:
        if t in ("xref", "array", "dict"):
            return [int(m) for m in _REF.findall(v)]
        return []

    def walk_resources(owner: int, depth: int) -> None:
        t, v = doc.xref_get_key(owner, "Resources")
        if t == "xref":
            grab(int(_REF.findall(v)[0]))
        for key in _RES_KEYS:
            t2, v2 = doc.xref_get_key(owner, f"Resources/{key}")
            if t2 == "xref":
                grab(int(_REF.findall(v2)[0]))
        t3, v3 = doc.xref_get_key(owner, "Resources/XObject")
        source = v3 if t3 == "dict" else (doc.xref_object(int(_REF.findall(v3)[0]))
                                          if t3 == "xref" else "")
        for x in (int(m) for m in _REF.findall(source)):
            if doc.xref_get_key(x, "Subtype")[1] == "/Form" and depth < 4 and x not in snap.objects:
                grab(x)
                walk_resources(x, depth + 1)

    t, v = snap.page_keys["Contents"]
    if t == "xref":
        x = int(_REF.findall(v)[0])
        grab(x)
        if not doc.xref_is_stream(x):  # indirect array of streams
            for y in _REF.findall(doc.xref_object(x)):
                grab(int(y))
    else:
        for x in refs_of(t, v):
            grab(x)
    walk_resources(page.xref, 0)
    return snap


def restore_content(doc: fitz.Document, page: fitz.Page, snap: ContentSnapshot) -> None:
    for x, (source, stream) in snap.objects.items():
        doc.update_object(x, source)
        if stream is not None:
            doc.update_stream(x, stream)
    for key, (t, v) in snap.page_keys.items():
        doc.xref_set_key(page.xref, key, v if t != "null" else "null")


# ------------------------------------------------------------------ probing
def _rgb_from_int(c: int) -> Color:
    return (((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0)


def _family(fontname: str) -> str:
    n = (fontname or "").lower()
    if any(s in n for s in ("courier", "mono", "consol", "menlo")):
        return "mono"
    if "sans" not in n and any(s in n for s in ("times", "serif", "roman", "georgia", "garamond",
                                                 "minion", "cambria", "book", "palatino")):
        return "serif"
    return "sans"


def base14_font(family: str, bold: bool = False, italic: bool = False) -> str:
    names = _FAMILIES.get(family, _FAMILIES["sans"])
    return names[(1 if bold else 0) + (2 if italic else 0)]


def probe_background(page: fitz.Page, rect: Rect) -> Color:
    """Most common colour on a thin ring just outside ``rect``."""
    clip = fitz.Rect(rect.inflated(2.5).as_tuple()) & page.rect  # displayed space
    if clip.is_empty:
        return (1.0, 1.0, 1.0)
    try:
        pix = page.get_pixmap(clip=clip, annots=False, alpha=False)
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0)
    w, h = pix.width, pix.height
    if w < 3 or h < 3:
        return (1.0, 1.0, 1.0)
    counts: collections.Counter = collections.Counter()
    for x in range(w):
        counts[pix.pixel(x, 0)] += 1
        counts[pix.pixel(x, h - 1)] += 1
    for y in range(h):
        counts[pix.pixel(0, y)] += 1
        counts[pix.pixel(w - 1, y)] += 1
    (r_, g, b), _n = counts.most_common(1)[0]
    return (r_ / 255.0, g / 255.0, b / 255.0)


def probe_text_style(page: fitz.Page, rect: Rect) -> TextStyle:
    """Approximate size/colour/font of the text inside ``rect``."""
    clip = _unrot(page, rect)
    best = None
    best_area = 0.0
    for block in page.get_text("dict", clip=clip).get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if not span.get("text", "").strip():
                    continue
                sb = fitz.Rect(span["bbox"]) & clip
                area = max(0.0, sb.width) * max(0.0, sb.height)
                if area > best_area:
                    best, best_area = span, area
    text = page.get_textbox(clip).strip()
    background = probe_background(page, rect)
    if best is None:
        return TextStyle(text=text, background=background)
    flags = int(best.get("flags", 0))
    fontname = best.get("font", "")
    bold = "bold" in fontname.lower() or bool(flags & 16)
    italic = any(s in fontname.lower() for s in ("italic", "oblique")) or bool(flags & 2)
    family = _family(fontname)
    return TextStyle(font_size=round(float(best.get("size", 11.0)), 1),
                     color=_rgb_from_int(int(best.get("color", 0))),
                     font_name=base14_font(family, bold, italic), source_font=fontname,
                     text=text, background=background)


# ------------------------------------------------------------------- edits
def _apply_redaction(page: fitz.Page, rect: Rect, fill: Optional[Color]) -> None:
    annot = page.add_redact_annot(_unrot(page, rect), fill=fill if fill is not None else False)
    if annot is None:
        raise EditError("Could not create the redaction")
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)


def redact(page: fitz.Page, rect: Rect, fill: Optional[Color] = (0.0, 0.0, 0.0)) -> None:
    """Remove text under ``rect`` and paint the box with ``fill``."""
    if rect.is_empty:
        raise EditError("Nothing to redact")
    _apply_redaction(page, rect, fill)


def replace_text(page: fitz.Page, rect: Rect, text: str, style: TextStyle,
                 fill: Optional[Color] = None, align: int = 0, min_size: float = 4.0) -> float:
    """Redact ``rect`` and set ``text`` inside it. The font shrinks until the
    text fits the box. Returns the font size used."""
    if rect.is_empty:
        raise EditError("Nothing to replace")
    _apply_redaction(page, rect, fill if fill is not None else style.background)
    if not text.strip():
        return style.font_size
    box = _unrot(page, rect.inflated(0.6))
    size = max(min_size, float(style.font_size))
    while True:
        # dry run in a scratch shape so a failed fit leaves no trace
        shape = page.new_shape()
        rc = shape.insert_textbox(box, text, fontsize=size, fontname=style.font_name,
                                  color=style.color, align=align, rotate=page.rotation)
        if rc >= 0 or size <= min_size:
            shape.commit()
            return size
        size = max(min_size, round(size * 0.92, 1))


def load_image_bytes(image: Union[bytes, str]) -> bytes:
    """Image data from bytes or a file path, validated and normalised: formats
    PDF can embed directly (PNG/JPEG) pass through, others become PNG."""
    if isinstance(image, str):
        try:
            with open(image, "rb") as f:
                image = f.read()
        except OSError as exc:
            raise EditError(f"Could not read the image: {exc.strerror or exc}") from exc
    if not image:
        raise EditError("No image data")
    try:
        pix = fitz.Pixmap(image)
        if pix.width < 1 or pix.height < 1:
            raise ValueError("empty image")
    except Exception as exc:  # noqa: BLE001
        raise EditError(f"Unsupported or damaged image: {exc}") from exc
    if image[:8] == b"\x89PNG\r\n\x1a\n" or image[:3] == b"\xff\xd8\xff":
        return image
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("png")


def place_image(page: fitz.Page, rect: Rect, image: Union[bytes, str], keep_aspect: bool = True,
                cover: bool = True, fill: Optional[Color] = (1.0, 1.0, 1.0)) -> None:
    """Optionally redact/whiten ``rect``, then draw ``image`` (bytes or a
    file path) into it, fitted (``keep_aspect``) or stretched."""
    if rect.is_empty:
        raise EditError("Choose an area for the image")
    data = load_image_bytes(image)
    if cover:
        _apply_redaction(page, rect, fill)
    page.insert_image(_unrot(page, rect), stream=data, keep_proportion=keep_aspect,
                      rotate=page.rotation)


def image_rects(page: fitz.Page) -> List[Tuple[Rect, int, int]]:
    """Bounding boxes (displayed space) of images drawn on the page."""
    out = []
    try:
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001
        return out
    # images inside annotation appearances (e.g. signatures) aren't page content
    annot_boxes = [fitz.Rect(a.rect) for a in page.annots()]
    for info in infos:
        bbox = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
        if bbox.is_empty or bbox.is_infinite:
            continue
        area = bbox.get_area()
        if any((fitz.Rect(bbox) & box).get_area() > 0.9 * area for box in annot_boxes):
            continue
        out.append((_disp(page, bbox), int(info.get("width", 0)), int(info.get("height", 0))))
    return out
