"""Ticket 18: annotations are rendered, stay aligned, and re-render on change."""

from __future__ import annotations

import time

import pytest
from PyQt6.QtGui import QColor

from pdf_studio.core import geometry as geo
from pdf_studio.core.annotation_commands import AddAnnotationCommand
from pdf_studio.core.render_cache import TileKey
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import AnnotationModel, Point, Rect
from pdf_studio.tests.conftest import pump


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        pump(15)
        if pred():
            return True
    return False


def _pixel_at(view, page, p: Point) -> QColor:
    """Colour of the rendered tile covering PDF point ``p`` (current revision)."""
    canvas = view.canvas
    dpr = canvas.viewport().devicePixelRatioF()
    s = geo.device_scale(view.zoom, dpr)
    pid, rev = view.session.render_key(page)
    tx, ty = int(p.x * s // 512), int(p.y * s // 512)
    key = TileKey(view.session.uid, pid, rev, geo.scale_key(s), tx, ty)
    assert _wait(lambda: key in canvas.service.cache), "tile never rendered"
    img = canvas.service.cache.peek(key)
    off = img.offset()
    return img.pixelColor(int(p.x * s) - off.x(), int(p.y * s) - off.y())


def test_new_annotation_appears_in_rendered_page(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    v.set_zoom(1.0)
    center = Point(150, 700)  # blank area on the page
    before = _pixel_at(v, 0, center)
    assert before.name() == "#ffffff"
    rev0 = v.session.render_key(0)[1]
    v.execute(AddAnnotationCommand(v.session.page_id(0), AnnotationModel(
        K.RECTANGLE, Rect(100, 650, 200, 750), color=(0, 0, 1), fill=(1, 1, 0), width=2)))
    assert v.session.render_key(0)[1] == rev0 + 1
    v.canvas.viewport().update()
    after = _pixel_at(v, 0, center)
    assert after.red() > 200 and after.green() > 200 and after.blue() < 60  # yellow
    # undo re-renders without the annotation
    window.actions["undo"].trigger()
    v.canvas.viewport().update()
    assert _pixel_at(v, 0, center).name() == "#ffffff"


def test_existing_annotations_render_on_open(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(2)
    hl = v.session.annotations(2)[0]
    c = _pixel_at(v, 2, hl.quads[0].center)
    assert c.blue() < c.red() - 60  # highlighted (yellowish), not white


def test_overlays_stay_aligned_through_zoom_and_scroll(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    v.execute(AddAnnotationCommand(v.session.page_id(0), AnnotationModel(
        K.RECTANGLE, Rect(100, 650, 200, 750), color=(0, 0, 1))))
    a = v.session.annotations(0)[0]
    for zoom in (0.5, 1.0, 2.37):
        v.set_zoom(zoom)
        v.canvas.verticalScrollBar().setValue(v.canvas.verticalScrollBar().maximum() // 3)
        r = v.canvas.pdf_rect_to_widget(0, a.rect)
        slot = v.canvas.page_rect_widget(0)
        scale = v.canvas.layout_.scale
        assert r.left() == pytest.approx(slot.left() + 100 * scale, abs=0.01)
        assert r.width() == pytest.approx(100 * scale, abs=0.01)


def test_stale_tiles_are_superseded_after_change(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    v.set_zoom(1.0)
    _pixel_at(v, 0, Point(150, 700))
    cache = v.canvas.service.cache
    pid = v.session.page_id(0)
    v.execute(AddAnnotationCommand(pid, AnnotationModel(K.RECTANGLE, Rect(100, 650, 200, 750))))
    v.canvas.viewport().update()
    _pixel_at(v, 0, Point(150, 700))
    rev = v.session.revision(pid)
    stale = [k for k in cache.keys_for_page(v.session.uid, pid)
             if k.revision < rev and k.tx >= 0 and k.scale == geo.scale_key(geo.device_scale(1.0, 1.0))]
    covering = {(k.tx, k.ty) for k in cache.keys_for_page(v.session.uid, pid) if k.revision == rev}
    assert not [k for k in stale if (k.tx, k.ty) in covering]
