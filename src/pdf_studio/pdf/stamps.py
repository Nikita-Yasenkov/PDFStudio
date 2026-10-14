"""Visual stamps and signatures (Qt-free).

A signature is an *image stamp annotation*: it can be selected, moved,
resized and deleted like any other annotation, and survives save/reopen.
This is a visual signature only; no certificates or cryptographic signing.
"""

from __future__ import annotations

from typing import Optional, Tuple

import pymupdf as fitz

from .annotations import STAMP_NAMES
from .errors import EditError
from .models import AnnotationKind, AnnotationModel, Point, Rect

DEFAULT_SIGNATURE_WIDTH = 170.0   # pt
DEFAULT_STAMP_WIDTH = 150.0       # pt
#: aspect ratio (w/h) of MuPDF's built-in rubber stamps
NAMED_STAMP_RATIO = 3.8

STAMP_LABELS = {
    "Approved": "Approved", "AsIs": "As Is", "Confidential": "Confidential",
    "Departmental": "Departmental", "Experimental": "Experimental", "Expired": "Expired",
    "Final": "Final", "ForComment": "For Comment", "ForPublicRelease": "For Public Release",
    "NotApproved": "Not Approved", "NotForPublicRelease": "Not for Public Release",
    "Sold": "Sold", "TopSecret": "Top Secret", "Draft": "Draft",
}


def image_size(data: bytes) -> Tuple[int, int]:
    try:
        pix = fitz.Pixmap(data)
    except Exception as exc:  # noqa: BLE001
        raise EditError(f"Unsupported or damaged image: {exc}") from exc
    if pix.width < 1 or pix.height < 1:
        raise EditError("The image is empty")
    return pix.width, pix.height


def fit_rect(center: Point, aspect: float, width: float, page_w: float, page_h: float) -> Rect:
    """A ``width``-wide rect with the given aspect (w/h), centred at
    ``center`` and kept fully on the page."""
    aspect = max(0.05, aspect)
    width = min(width, page_w - 4)
    height = width / aspect
    if height > page_h - 4:
        height = page_h - 4
        width = height * aspect
    x0 = min(max(2.0, center.x - width / 2), page_w - width - 2)
    y0 = min(max(2.0, center.y - height / 2), page_h - height - 2)
    return Rect(x0, y0, x0 + width, y0 + height)


def fit_into(box: Rect, aspect: float) -> Rect:
    """Largest rect of ``aspect`` centred in ``box``."""
    w, h = box.width, box.height
    if w <= 0 or h <= 0:
        return box
    if w / h > aspect:
        w = h * aspect
    else:
        h = w / aspect
    c = box.center
    return Rect(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2)


def trim_transparent(data: bytes, pad: int = 4) -> bytes:
    """Crop fully transparent margins of a PNG (drawn signatures)."""
    try:
        pix = fitz.Pixmap(data)
    except Exception as exc:  # noqa: BLE001
        raise EditError(f"Unsupported or damaged image: {exc}") from exc
    if not pix.alpha:
        return data
    w, h, n = pix.width, pix.height, pix.n
    alpha = bytes(pix.samples)[n - 1::n]
    rows = [y for y in range(h) if alpha[y * w:(y + 1) * w].strip(b"\x00")]
    if not rows:
        return data
    cols = [x for x in range(w) if alpha[x::w].strip(b"\x00")]
    x0, x1 = max(0, cols[0] - pad), min(w, cols[-1] + 1 + pad)
    y0, y1 = max(0, rows[0] - pad), min(h, rows[-1] + 1 + pad)
    if (x0, y0, x1, y1) == (0, 0, w, h):
        return data
    samples = bytes(pix.samples)
    stride = pix.stride
    cropped = b"".join(samples[y * stride + x0 * n:y * stride + x1 * n] for y in range(y0, y1))
    out = fitz.Pixmap(pix.colorspace, x1 - x0, y1 - y0, cropped, True)
    return out.tobytes("png")


def signature_model(rect: Rect, image: bytes, author: str = "", opacity: float = 1.0) -> AnnotationModel:
    return AnnotationModel(AnnotationKind.STAMP, rect, stamp_image=image, contents="Signature",
                           author=author, opacity=opacity, color=None)


def stamp_model(rect: Rect, name: str, author: str = "") -> AnnotationModel:
    if name not in STAMP_NAMES:
        raise EditError(f"Unknown stamp: {name}")
    return AnnotationModel(AnnotationKind.STAMP, rect, stamp_name=name,
                           contents=STAMP_LABELS.get(name, name), author=author, color=None)


def default_rect(page_w: float, page_h: float, center: Point, image: Optional[bytes] = None,
                 stamp_name: Optional[str] = None, width: Optional[float] = None) -> Rect:
    """Placement rect for a click at ``center``."""
    if image is not None:
        iw, ih = image_size(image)
        return fit_rect(center, iw / ih, width or DEFAULT_SIGNATURE_WIDTH, page_w, page_h)
    return fit_rect(center, NAMED_STAMP_RATIO, width or DEFAULT_STAMP_WIDTH, page_w, page_h)


def remove_white_background(data: bytes, threshold: int = 225) -> bytes:
    """Make near-white pixels transparent (scanned/photographed signatures).

    Darker pixels fade in smoothly so pen strokes keep anti-aliased edges.
    Per-pixel work happens in C via ``bytes.translate``.
    """
    try:
        pix = fitz.Pixmap(data)
    except Exception as exc:  # noqa: BLE001
        raise EditError(f"Unsupported or damaged image: {exc}") from exc
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)  # drop existing alpha
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    gray = fitz.Pixmap(fitz.csGRAY, pix)
    span = max(1, threshold - 90)
    table = bytes(0 if v >= threshold else min(255, int((threshold - v) * 255 / span))
                  for v in range(256))
    alpha = bytes(gray.samples).translate(table)
    out = fitz.Pixmap(pix, 1)
    out.set_alpha(alpha)
    return out.tobytes("png")
