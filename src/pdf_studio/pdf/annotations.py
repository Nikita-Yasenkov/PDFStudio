"""Annotation CRUD on PyMuPDF pages (Qt-free).

All public functions take/return :class:`AnnotationModel` in *displayed*
page space and convert to MuPDF's unrotated space internally. Annotations
are identified by their ``/NM`` name; when an annotation must be recreated
(undo of delete, moving ink/line), the original name is re-applied so that
later commands referring to it keep working.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

import pymupdf as fitz

from .errors import AnnotationError
from .models import AnnotationKind, AnnotationModel, Color, Point, Rect

K = AnnotationKind

_SUBTYPE_KIND = {
    "Highlight": K.HIGHLIGHT,
    "Underline": K.UNDERLINE,
    "StrikeOut": K.STRIKEOUT,
    "Squiggly": K.SQUIGGLY,
    "Text": K.NOTE,
    "FreeText": K.FREE_TEXT,
    "Ink": K.INK,
    "Square": K.RECTANGLE,
    "Circle": K.ELLIPSE,
    "Line": K.LINE,
    "Stamp": K.STAMP,
}
_SKIP_SUBTYPES = {"Popup", "Link", "Widget"}
_DA_COLOR = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")
_DA_GRAY = re.compile(r"([\d.]+)\s+g(?:\s|$)")
_DA_FONT = re.compile(r"/([A-Za-z0-9_+-]+)\s+([\d.]+)\s+Tf")

STAMP_NAMES = ("Approved", "AsIs", "Confidential", "Departmental", "Experimental", "Expired",
               "Final", "ForComment", "ForPublicRelease", "NotApproved",
               "NotForPublicRelease", "Sold", "TopSecret", "Draft")


# ----------------------------------------------------------------- geometry
def _to_unrot_rect(page: fitz.Page, r: Rect) -> fitz.Rect:
    fr = fitz.Rect(r.as_tuple())
    if page.rotation:
        fr = fr * page.derotation_matrix
    return fr.normalize()


def _to_unrot_point(page: fitz.Page, p: Point) -> fitz.Point:
    fp = fitz.Point(p.x, p.y)
    return fp * page.derotation_matrix if page.rotation else fp


def _to_disp_rect(page: fitz.Page, r) -> Rect:
    fr = fitz.Rect(r)
    if page.rotation:
        fr = fr * page.rotation_matrix
    fr = fr.normalize()
    return Rect(fr.x0, fr.y0, fr.x1, fr.y1)


def _to_disp_point(page: fitz.Page, p) -> Point:
    fp = fitz.Point(p)
    if page.rotation:
        fp = fp * page.rotation_matrix
    return Point(float(fp.x), float(fp.y))


def _quad_for(page: fitz.Page, r: Rect) -> fitz.Quad:
    """Quad for a text line box given in displayed space.

    The quad's orientation decides where underline/strikeout are drawn, so it
    must follow the *text* direction. On a rotated page, text that is
    horizontal in content space (the usual case) appears vertical to the
    reader; text that was counter-rotated (e.g. scanned landscape pages)
    appears horizontal. A line box is wider than tall along its writing
    direction, which tells the two apart.
    """
    if not page.rotation:
        return fitz.Rect(r.as_tuple()).quad
    unrot = (fitz.Rect(r.as_tuple()) * page.derotation_matrix).normalize()
    if unrot.width >= unrot.height:
        return unrot.quad                      # horizontal in content space
    return fitz.Rect(r.as_tuple()).quad * page.derotation_matrix  # horizontal on screen


# ------------------------------------------------------------------ colours
def _rgb(c) -> Optional[Color]:
    if c is None:
        return None
    c = list(c)
    if not c:
        return None
    if len(c) == 1:
        return (float(c[0]),) * 3  # gray
    if len(c) == 4:  # cmyk → rgb (approximate)
        cc, m, y, k = c
        return ((1 - cc) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (float(c[0]), float(c[1]), float(c[2]))


def _parse_da(da: str) -> Tuple[Optional[Color], Optional[float], Optional[str]]:
    color = None
    m = _DA_COLOR.search(da or "")
    if m:
        color = tuple(float(x) for x in m.groups())  # type: ignore[assignment]
    else:
        g = _DA_GRAY.search(da or "")
        if g:
            v = float(g.group(1))
            color = (v, v, v)
    f = _DA_FONT.search(da or "")
    size = float(f.group(2)) if f else None
    font = f.group(1) if f else None
    return color, size, font


# ------------------------------------------------------------------ reading
def _ensure_id(doc: fitz.Document, annot: fitz.Annot) -> str:
    aid = (annot.info or {}).get("id") or ""
    if not aid:
        aid = f"ps-{annot.xref}"
        doc.xref_set_key(annot.xref, "NM", fitz.get_pdf_str(aid))
    return aid


def _subtype(annot: fitz.Annot) -> str:
    return annot.type[1] if annot.type else ""


def model_from_annot(page: fitz.Page, annot: fitz.Annot, index: int,
                     with_image: bool = False) -> AnnotationModel:
    doc = page.parent
    subtype = _subtype(annot)
    kind = _SUBTYPE_KIND.get(subtype, K.OTHER)
    info = annot.info or {}
    colors = annot.colors or {}
    stroke = _rgb(colors.get("stroke"))
    fill = _rgb(colors.get("fill"))
    opacity = annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0
    border = annot.border or {}
    width = float(border.get("width", 1.0) or 0.0)
    if width < 0:
        width = 1.0
    unrot_rect = fitz.Rect(annot.rect)
    if kind in (K.RECTANGLE, K.ELLIPSE):
        # /Rect includes the stroke (/RD); the model holds the drawn shape box,
        # which is also what set_rect() expects.
        rd = _rd(doc, annot)
        if rd is not None:
            l, t, r_, b = rd
            inner = fitz.Rect(unrot_rect.x0 + l, unrot_rect.y0 + t,
                              unrot_rect.x1 - r_, unrot_rect.y1 - b)
            if not inner.is_empty:
                unrot_rect = inner
    rect = _to_disp_rect(page, unrot_rect)
    quads: Tuple[Rect, ...] = ()
    strokes: Tuple[Tuple[Point, ...], ...] = ()
    font_size = 12.0
    text_color: Color = (0.0, 0.0, 0.0)
    stamp_name = None
    stamp_image = None
    icon = "Note"

    if kind.is_text_markup:
        verts = annot.vertices or []
        qs = []
        for i in range(0, len(verts) - 3, 4):
            pts = [_to_disp_point(page, v) for v in verts[i:i + 4]]
            xs = [p.x for p in pts]
            ys = [p.y for p in pts]
            qs.append(Rect(min(xs), min(ys), max(xs), max(ys)))
        quads = tuple(qs)
    elif kind == K.INK:
        strokes = tuple(tuple(_to_disp_point(page, p) for p in s) for s in (annot.vertices or []))
    elif kind == K.LINE:
        verts = annot.vertices or []
        if len(verts) >= 2:
            strokes = ((_to_disp_point(page, verts[0]), _to_disp_point(page, verts[1])),)
        try:
            ends = annot.line_ends
            if ends and ends[1] not in (fitz.PDF_ANNOT_LE_NONE, None):
                kind = K.ARROW
        except Exception:  # noqa: BLE001
            pass
    elif kind == K.FREE_TEXT:
        da = doc.xref_get_key(annot.xref, "DA")[1]
        tc, fs, _font = _parse_da(da)
        if tc is not None:
            text_color = tc
        if fs:
            font_size = fs
        stroke = None
    elif kind == K.NOTE:
        icon = doc.xref_get_key(annot.xref, "Name")[1].lstrip("/") or "Note"
    elif kind == K.STAMP:
        name = doc.xref_get_key(annot.xref, "Name")[1].lstrip("/")
        if name and name != "ImageStamp":
            stamp_name = name
        if with_image and stamp_name is None:
            stamp_image = _stamp_png(doc, annot)

    return AnnotationModel(
        kind=kind, rect=rect, id=_ensure_id(doc, annot), page_index=index, quads=quads,
        strokes=strokes, color=stroke, fill=fill, opacity=float(opacity), width=width,
        contents=info.get("content", "") or "", font_size=font_size, text_color=text_color,
        author=info.get("title", "") or "", icon=icon, stamp_image=stamp_image,
        stamp_name=stamp_name, subtype=subtype, read_only=kind == K.OTHER,
    )


def _rd(doc: fitz.Document, annot: fitz.Annot) -> Optional[Tuple[float, float, float, float]]:
    t, v = doc.xref_get_key(annot.xref, "RD")
    if t != "array":
        return None
    try:
        l, top, r, b = (float(x) for x in v.strip("[]").split())
    except ValueError:
        return None
    return (l, top, r, b)


def _stamp_png(doc: fitz.Document, annot: fitz.Annot) -> Optional[bytes]:
    """Recover the image of an image stamp (for recreating it on undo)."""
    try:
        t, ap = doc.xref_get_key(annot.xref, "AP/N")
        if t == "xref":
            apx = int(ap.split()[0])
            for img_xref in _image_xrefs(doc, apx):
                pix = fitz.Pixmap(doc, img_xref)
                st, sm = doc.xref_get_key(img_xref, "SMask")
                if st == "xref":
                    mask = fitz.Pixmap(doc, int(sm.split()[0]))
                    pix = fitz.Pixmap(pix, mask)
                if pix.colorspace and pix.colorspace.n not in (1, 3):
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                return pix.tobytes("png")
    except Exception:  # noqa: BLE001 - fall back to rasterising the appearance
        pass
    try:
        return annot.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=True).tobytes("png")
    except Exception:  # noqa: BLE001
        return None


def _image_xrefs(doc: fitz.Document, form_xref: int, depth: int = 0) -> List[int]:
    out: List[int] = []
    t, v = doc.xref_get_key(form_xref, "Resources/XObject")
    if t == "dict":
        for ref in re.findall(r"(\d+) 0 R", v):
            x = int(ref)
            sub = doc.xref_get_key(x, "Subtype")[1]
            if sub == "/Image":
                out.append(x)
            elif sub == "/Form" and depth < 3:
                out.extend(_image_xrefs(doc, x, depth + 1))
    return out


def list_annotations(page: fitz.Page, index: int) -> List[AnnotationModel]:
    out = []
    for annot in page.annots():
        if _subtype(annot) in _SKIP_SUBTYPES:
            continue
        out.append(model_from_annot(page, annot, index))
    return out


def find_annot(page: fitz.Page, annot_id: str) -> fitz.Annot:
    try:
        annot = page.load_annot(annot_id)
    except Exception:  # noqa: BLE001
        annot = None
    if annot is None:
        for a in page.annots():
            if (a.info or {}).get("id") == annot_id:
                return a
        raise AnnotationError(f"Annotation {annot_id!r} not found on page")
    return annot


# ----------------------------------------------------------------- creation
def create_annotation(page: fitz.Page, index: int, m: AnnotationModel) -> AnnotationModel:
    if m.read_only or m.kind == K.OTHER:
        raise AnnotationError("Cannot create this annotation type")
    doc = page.parent
    k = m.kind
    if k.is_text_markup:
        if not m.quads:
            raise AnnotationError("Text markup needs at least one quad")
        quads = [_quad_for(page, r) for r in m.quads]
        fn = {K.HIGHLIGHT: page.add_highlight_annot, K.UNDERLINE: page.add_underline_annot,
              K.STRIKEOUT: page.add_strikeout_annot, K.SQUIGGLY: page.add_squiggly_annot}[k]
        annot = fn(quads=quads)
    elif k == K.NOTE:
        p = Point(m.rect.x0, m.rect.y0)
        annot = page.add_text_annot(_to_unrot_point(page, p), m.contents or "",
                                    icon=m.icon or "Note")
    elif k == K.FREE_TEXT:
        if m.rect.is_empty:
            raise AnnotationError("Text box is empty")
        # borderless text box (MuPDF only supports border colours with rich text)
        annot = page.add_freetext_annot(
            _to_unrot_rect(page, m.rect), m.contents or "", fontsize=m.font_size,
            fontname="helv", text_color=m.text_color, fill_color=m.fill,
            opacity=m.opacity, rotate=page.rotation)
    elif k == K.INK:
        strokes = [[tuple(_to_unrot_point(page, p)) for p in s] for s in m.strokes if len(s) >= 2]
        if not strokes:
            raise AnnotationError("Ink needs at least one stroke")
        annot = page.add_ink_annot(strokes)
    elif k in (K.RECTANGLE, K.ELLIPSE):
        if m.rect.is_empty:
            raise AnnotationError("Shape is empty")
        fn = page.add_rect_annot if k == K.RECTANGLE else page.add_circle_annot
        annot = fn(_to_unrot_rect(page, m.rect))
    elif k in (K.LINE, K.ARROW):
        if not m.strokes or len(m.strokes[0]) < 2:
            raise AnnotationError("Line needs two points")
        a, b = m.strokes[0][0], m.strokes[0][-1]
        annot = page.add_line_annot(_to_unrot_point(page, a), _to_unrot_point(page, b))
        if k == K.ARROW:
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    elif k == K.STAMP:
        rect = _to_unrot_rect(page, m.rect)
        if m.stamp_image:
            annot = page.add_stamp_annot(rect, stamp=m.stamp_image)
        else:
            name = m.stamp_name or "Approved"
            idx = STAMP_NAMES.index(name) if name in STAMP_NAMES else 0
            annot = page.add_stamp_annot(rect, stamp=idx)
        if page.rotation:
            annot.set_rotation(page.rotation)
    else:  # pragma: no cover
        raise AnnotationError(f"Unsupported annotation kind {k}")

    _apply_properties(page, annot, m, creating=True)
    if m.id:
        doc.xref_set_key(annot.xref, "NM", fitz.get_pdf_str(m.id))
    annot = find_annot(page, m.id) if m.id else annot
    return model_from_annot(page, annot, index)


def _apply_properties(page: fitz.Page, annot: fitz.Annot, m: AnnotationModel,
                      creating: bool = False) -> None:
    k = m.kind
    info = {"content": m.contents or ""}
    if m.author:
        info["title"] = m.author
    annot.set_info(**info)
    if k == K.FREE_TEXT:
        if m.fill is None:
            # update() only sets a fill when given; clear an existing one explicitly
            page.parent.xref_set_key(annot.xref, "IC", "null")
        annot.update(fontsize=m.font_size, text_color=m.text_color,
                     fill_color=m.fill, opacity=m.opacity, rotate=page.rotation)
        return
    if k == K.STAMP:
        annot.set_opacity(m.opacity)
        annot.update()
        return
    if k == K.NOTE:
        if m.color:
            annot.set_colors(stroke=m.color)
        annot.set_opacity(m.opacity)
        annot.update()
        return
    # PyMuPDF: None = leave unchanged, [] = remove the colour
    if m.has_fill:
        annot.set_colors(stroke=m.color if m.color is not None else [],
                         fill=m.fill if m.fill is not None else [])
    elif m.color is not None:
        annot.set_colors(stroke=m.color)
    if m.has_width:
        annot.set_border(width=max(0.0, m.width))
    annot.set_opacity(max(0.0, min(1.0, m.opacity)))
    annot.update()
    if k in (K.RECTANGLE, K.ELLIPSE) and not m.rect.is_empty:
        # a new stroke width changes /RD; keep the drawn shape box exact
        annot.set_rect(_to_unrot_rect(page, m.rect))
        annot.update()


# ------------------------------------------------------------------- update
def update_annotation(page: fitz.Page, index: int, annot_id: str,
                      m: AnnotationModel) -> AnnotationModel:
    """Make annotation ``annot_id`` match ``m`` (geometry + properties)."""
    annot = find_annot(page, annot_id)
    current = model_from_annot(page, annot, index)
    if current.read_only:
        raise AnnotationError("This annotation cannot be edited")
    m = m.with_changes(id=annot_id, kind=m.kind)
    geometry_changed = (m.rect != current.rect or m.strokes != current.strokes
                        or m.quads != current.quads)
    # MuPDF can't set /Rect on vertex-based annots, and markup geometry is
    # defined by quads: recreate those (keeping the /NM id).
    needs_recreate = (m.kind != current.kind
                      or (geometry_changed and (m.kind.is_vertex_based or m.kind.is_text_markup)))
    if needs_recreate:
        if m.kind == K.STAMP and m.stamp_image is None and m.stamp_name is None:
            m = m.with_changes(stamp_image=_stamp_png(page.parent, annot))
        page.delete_annot(annot)
        return create_annotation(page, index, m)
    if geometry_changed:
        if m.kind == K.NOTE:
            r = _to_unrot_rect(page, m.rect)
            annot.set_rect(fitz.Rect(r.x0, r.y0, r.x0 + annot.rect.width, r.y0 + annot.rect.height))
        else:
            annot.set_rect(_to_unrot_rect(page, m.rect))
    _apply_properties(page, annot, m)
    return model_from_annot(page, find_annot(page, annot_id), index)


def delete_annotation(page: fitz.Page, annot_id: str) -> None:
    page.delete_annot(find_annot(page, annot_id))


def get_annotation(page: fitz.Page, index: int, annot_id: str,
                   with_image: bool = False) -> AnnotationModel:
    return model_from_annot(page, find_annot(page, annot_id), index, with_image=with_image)


def markup_rects_from_lines(lines: Sequence[Rect], pad: float = 0.0) -> Tuple[Rect, ...]:
    return tuple(r.inflated(pad) if pad else r for r in lines)
