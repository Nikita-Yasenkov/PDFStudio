"""Content edits: redact, redact-and-replace text, cover-and-place image
(Qt-free).

All rects are displayed page space. Redaction is real: text under the box
is removed from the content stream (images and vector art are left in place
and covered by the fill). Replacement text is box-bound (no reflow).

Replacement text and placed images are *placed objects*: each is drawn as
its own Form XObject, tagged with PDF Studio keys, so it stays page content
(searchable, printed, flattened) yet can be moved, resized or deleted later
by rewriting the form's /Matrix or dropping its ``Do``.

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
from .models import Color, PlacedObject, Point, Rect, TextStyle, new_object_id
from .stamps import fit_into

_REF = re.compile(r"(\d+) 0 R")
_RES_KEYS = ("Font", "XObject", "ExtGState", "ColorSpace", "Pattern", "Shading", "Properties")

#: keys tagging a Form XObject as a placed object. MuPDF renames or copies
#: forms when it rewrites a page (a later redaction does); the keys travel
#: with the copy, so objects are found by id, never by name or xref.
_OBJ_KIND = "PDFStudioKind"   # /Text or /Image
_OBJ_ID = "PDFStudioId"       # a name, stable for the object's lifetime

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


# ----------------------------------------------------------- placed objects
def _numbers(value: str) -> List[float]:
    return [float(v) for v in value.strip("[] ").split()]


def _pdf_num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _to_display(page: fitz.Page) -> fitz.Matrix:
    """PDF space → displayed page space."""
    return page.transformation_matrix * page.rotation_matrix


def _form_matrix(doc: fitz.Document, xref: int) -> fitz.Matrix:
    t, v = doc.xref_get_key(xref, "Matrix")
    return fitz.Matrix(*_numbers(v)) if t == "array" else fitz.Matrix()


def _form_rect(page: fitz.Page, xref: int) -> Rect:
    doc = page.parent
    bbox = fitz.Rect(_numbers(doc.xref_get_key(xref, "BBox")[1]))
    r = (bbox * _form_matrix(doc, xref) * _to_display(page)).normalize()
    return Rect(r.x0, r.y0, r.x1, r.y1)


def _do_pattern(name: str) -> "re.Pattern[bytes]":
    return re.compile(rb"/" + re.escape(name.encode("latin-1")) + rb"\s+Do\b")


def _objects(page: fitz.Page) -> List[Tuple[str, str, int, str]]:
    """(id, kind, xref, resource name) of the objects drawn on ``page``,
    in drawing order (topmost last)."""
    doc = page.parent
    tagged = []
    for xref, name, invoker, _bbox in page.get_xobjects():
        if invoker == 0:
            t, oid = doc.xref_get_key(xref, _OBJ_ID)
            if t == "name":
                tagged.append((oid[1:], xref, name))
    if not tagged:
        return []
    content = page.read_contents()
    found = []
    for oid, xref, name in tagged:
        hits = [m.start() for m in _do_pattern(name).finditer(content)]
        if hits:  # a deleted object is no longer drawn
            kind = doc.xref_get_key(xref, _OBJ_KIND)[1].lstrip("/").lower() or "text"
            found.append((hits[-1], oid, kind, xref, name))
    found.sort()
    return [(oid, kind, xref, name) for _pos, oid, kind, xref, name in found]


def placed_objects(page: fitz.Page) -> List[PlacedObject]:
    return [PlacedObject(oid, kind, _form_rect(page, xref))
            for oid, kind, xref, _name in _objects(page)]


def _find_object(page: fitz.Page, object_id: str) -> Tuple[int, str]:
    for oid, _kind, xref, name in _objects(page):
        if oid == object_id:
            return xref, name
    raise EditError("This item is no longer on the page")


def _show_object(page: fitz.Page, src: fitz.Document, clip: fitz.Rect, target: Rect,
                 kind: str, object_id: str) -> None:
    """Draw ``clip`` of page 0 of ``src`` (laid out upright, in displayed
    orientation) into ``target`` as a tagged form."""
    doc = page.parent
    before = {name for _x, name, invoker, _b in page.get_xobjects() if invoker == 0}
    page.show_pdf_page(_unrot(page, target), src, 0, clip=clip, keep_proportion=False,
                       rotate=page.rotation)
    xref = next(x for x, name, invoker, _b in page.get_xobjects()
                if invoker == 0 and name not in before)
    doc.xref_set_key(xref, _OBJ_KIND, f"/{kind}")
    doc.xref_set_key(xref, _OBJ_ID, f"/{object_id}")
    # PyMuPDF keeps a graft map per source document; this one is single-use
    getattr(doc, "Graftmaps", {}).pop(src._graft_id, None)
    getattr(doc, "ShownPages", {}).pop((src._graft_id, 0), None)


def transform_object(page: fitz.Page, object_id: str, rect: Rect) -> None:
    """Move/scale a placed object so it fills ``rect``."""
    if rect.is_empty:
        raise EditError("The new size is empty")
    doc = page.parent
    xref, _name = _find_object(page, object_id)
    old = _form_rect(page, xref)
    if old.is_empty:
        raise EditError("This item has no size")
    sx, sy = rect.width / old.width, rect.height / old.height
    t = fitz.Matrix(sx, 0, 0, sy, rect.x0 - old.x0 * sx, rect.y0 - old.y0 * sy)
    d = _to_display(page)
    m = _form_matrix(doc, xref) * d * t * ~d
    doc.xref_set_key(xref, "Matrix", "[" + " ".join(_pdf_num(v) for v in m) + "]")


def _xobject_dict(doc: fitz.Document, page: fitz.Page,
                  copy=None) -> Optional[Tuple[int, str]]:
    """(xref, key prefix) addressing the page's /XObject resources without
    passing through an indirect object (``xref_set_key`` paths can't), or
    None for inherited resources. ``copy`` (xref → new xref) first gives the
    page its own /Resources and /XObject objects."""
    t, v = doc.xref_get_key(page.xref, "Resources")
    if t == "xref":
        owner, prefix = int(_REF.findall(v)[0]), ""
        if copy is not None:
            owner = copy(owner)
            doc.xref_set_key(page.xref, "Resources", f"{owner} 0 R")
    elif t == "dict":
        owner, prefix = page.xref, "Resources/"
    else:
        return None
    t, v = doc.xref_get_key(owner, prefix + "XObject")
    if t == "xref":
        x = int(_REF.findall(v)[0])
        if copy is not None:
            x = copy(x)
            doc.xref_set_key(owner, prefix + "XObject", f"{x} 0 R")
        return x, ""
    return owner, prefix + "XObject/"


def delete_object(page: fitz.Page, object_id: str) -> None:
    doc = page.parent
    _xref, name = _find_object(page, object_id)
    pattern = _do_pattern(name)
    for x in page.get_contents():
        data = doc.xref_stream(x)
        stripped = pattern.sub(b"", data)
        if stripped != data:
            doc.update_stream(x, stripped)
    where = _xobject_dict(doc, page)
    if where is not None:
        doc.xref_set_key(where[0], where[1] + name, "null")


def unshare_objects(page: fitz.Page) -> None:
    """Give ``page`` its own copies of the objects it draws. A duplicated
    page shares /Resources (and so the forms) with its original, and moving
    an object on one would move it on both."""
    doc = page.parent
    objects = _objects(page)
    if not objects:
        return

    def copy(xref: int) -> int:
        new = doc.get_new_xref()
        doc.update_object(new, doc.xref_object(xref, compressed=False))
        if doc.xref_is_stream(xref):
            doc.update_stream(new, doc.xref_stream(xref))
        return new

    where = _xobject_dict(doc, page, copy)
    if where is None:
        return
    for _oid, _kind, xref, name in objects:
        form = copy(xref)
        doc.xref_set_key(form, _OBJ_ID, f"/{new_object_id()}")
        doc.xref_set_key(where[0], where[1] + name, f"{form} 0 R")


# ------------------------------------------------------------------- edits
def _apply_redaction(page: fitz.Page, rect: Rect, fill: Optional[Color]) -> None:
    # objects entirely under the box go away instead of lingering as empty forms
    covering = rect.inflated(2.0)
    for obj in placed_objects(page):
        if covering.contains(Point(obj.rect.x0, obj.rect.y0)) and \
                covering.contains(Point(obj.rect.x1, obj.rect.y1)):
            delete_object(page, obj.id)
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


def _text_font(page: fitz.Page, alias: str, text: str) -> str:
    """Font to set ``text`` in: the base-14 font itself when its one-byte
    encoding covers the text, else the same typeface embedded as a Unicode
    font (MuPDF's base-14 fonts have Latin, Greek and Cyrillic glyphs; the
    one-byte encoding would turn e.g. Cyrillic into "?")."""
    if all(ord(c) < 256 for c in text):
        return alias
    name = f"{alias}-u"
    page.insert_font(fontname=name, fontbuffer=fitz.Font(alias).buffer)
    return name


def _text_bounds(page: fitz.Page) -> Optional[fitz.Rect]:
    box = None
    for block in page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if span.get("text", "").strip():
                    r = fitz.Rect(span["bbox"])
                    box = r if box is None else box | r
    if box is None:
        return None
    return fitz.Rect(box.x0 - 1.5, box.y0 - 1, box.x1 + 1.5, box.y1 + 1) & page.rect


def replace_text(page: fitz.Page, rect: Rect, text: str, style: TextStyle,
                 fill: Optional[Color] = None, align: int = 0, min_size: float = 4.0,
                 object_id: Optional[str] = None) -> float:
    """Redact ``rect`` and set ``text`` inside it, as a placed object
    (``object_id``) cut to the text's extent. The font shrinks until the
    text fits the box. Returns the font size used."""
    if rect.is_empty:
        raise EditError("Nothing to replace")
    _apply_redaction(page, rect, fill if fill is not None else style.background)
    if not text.strip():
        return style.font_size
    box = rect.inflated(0.6)
    size = max(min_size, float(style.font_size))
    # laid out upright on a scratch page: the box plus a margin, so that
    # descenders and overhangs past the box are not cut off
    margin = 4.0 + size
    descender = abs(fitz.Font(style.font_name).descender)
    src = fitz.open()
    try:
        scratch = src.new_page(width=box.width + 2 * margin, height=box.height + 2 * margin)
        font = _text_font(scratch, style.font_name, text)
        while True:
            # the box is a line box, but PyMuPDF also reserves a descender
            # below the last line: give that back
            area = fitz.Rect(margin, margin, margin + box.width,
                             margin + box.height + descender * size)
            # dry run in a scratch shape so a failed fit leaves no trace
            shape = scratch.new_shape()
            rc = shape.insert_textbox(area, text, fontsize=size, fontname=font,
                                      color=style.color, align=align)
            if rc >= 0 or size <= min_size:
                shape.commit()
                break
            size = max(min_size, round(size * 0.92, 1))
        ink = _text_bounds(scratch)
        if ink is not None:
            dx, dy = box.x0 - margin, box.y0 - margin
            target = Rect(ink.x0 + dx, ink.y0 + dy, ink.x1 + dx, ink.y1 + dy)
            _show_object(page, src, ink, target, "Text", object_id or new_object_id())
    finally:
        src.close()
    return size


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
                cover: bool = True, fill: Optional[Color] = (1.0, 1.0, 1.0),
                object_id: Optional[str] = None, replaces: Optional[str] = None) -> None:
    """Draw ``image`` (bytes or a file path) into ``rect`` as a placed
    object, fitted (``keep_aspect``) or stretched. ``cover`` first redacts
    and whitens ``rect`` (hiding an image being replaced); ``replaces``
    deletes that placed object first."""
    if rect.is_empty:
        raise EditError("Choose an area for the image")
    data = load_image_bytes(image)
    if replaces:
        delete_object(page, replaces)
    if cover:
        _apply_redaction(page, rect, fill)
    if keep_aspect:
        pix = fitz.Pixmap(data)
        rect = fit_into(rect, pix.width / pix.height)
    src = fitz.open()
    try:
        scratch = src.new_page(width=rect.width, height=rect.height)
        scratch.insert_image(scratch.rect, stream=data, keep_proportion=False)
        _show_object(page, src, scratch.rect, rect, "Image", object_id or new_object_id())
    finally:
        src.close()


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
