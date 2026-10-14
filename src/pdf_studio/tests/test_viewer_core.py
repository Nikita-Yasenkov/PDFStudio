"""Tickets 03/10: coordinates, layout, render cache and render queue."""

from __future__ import annotations

import threading

import pytest

from pdf_studio.core import geometry as geo
from pdf_studio.core.layout import (GAP, MARGIN, ViewMode, compute_layout,
                                    current_page_for_view, fit_page_zoom,
                                    fit_width_zoom, next_page_index)
from pdf_studio.core.render_cache import WHOLE_PAGE, RenderCache, TileKey
from pdf_studio.core.render_queue import RenderQueue, RenderRequest
from pdf_studio.pdf.models import Point, Rect

A4 = (595.0, 842.0)


# ------------------------------------------------------------ coordinates
def test_pdf_widget_round_trip():
    t = geo.PageTransform(x=100, y=50, scale=2.0)
    scroll = geo.Scroll(30, 400)
    p = Point(72.5, 300.25)
    wx, wy = geo.pdf_to_widget(p, scroll, t)
    assert (wx, wy) == pytest.approx((100 + 145 - 30, 50 + 600.5 - 400))
    back = geo.widget_to_pdf(wx, wy, scroll, t)
    assert back.x == pytest.approx(p.x) and back.y == pytest.approx(p.y)


def test_rect_transforms():
    t = geo.PageTransform(10, 20, 1.5)
    r = Rect(10, 10, 110, 60)
    x, y, w, h = geo.pdf_rect_to_widget(r, geo.Scroll(0, 0), t)
    assert (x, y, w, h) == pytest.approx((25, 35, 150, 75))
    assert geo.widget_rect_to_pdf(x, y, w, h, geo.Scroll(0, 0), t) == pytest.approx(r)


def test_device_scale_and_tiles():
    s = geo.device_scale(1.0, 2.0)
    assert s == pytest.approx(geo.PX_PER_PT * 2)
    rw, rh, cols, rows = geo.tile_grid(595, 842, s, 512)
    assert (rw, rh) == (round(595 * s), round(842 * s))
    assert cols == -(-rw // 512) and rows == -(-rh // 512)
    clip = geo.tile_clip(1, 0, rw, rh, s, 512)
    assert clip.x0 == pytest.approx(512 / s)
    # only tiles in view are returned
    tiles = geo.visible_tiles(0, 0, 0, 0, 100, 100, 2.0, rw, rh, 512)
    assert tiles == [(0, 0)]
    assert geo.visible_tiles(0, 0, 5000, 5000, 10, 10, 2.0, rw, rh, 512) == []


# ----------------------------------------------------------------- layout
def test_continuous_layout_stacks_and_centres():
    sizes = [A4, A4, (842.0, 595.0)]
    lay = compute_layout(sizes, 1.0, 1200, 800, ViewMode.CONTINUOUS)
    assert len(lay.slots) == 3
    s0, s1, s2 = lay.slots
    assert s0.y == MARGIN
    assert s1.y == pytest.approx(s0.bottom + GAP, abs=1)
    # centred horizontally
    assert s0.x + s0.w / 2 == pytest.approx(lay.width / 2, abs=1)
    assert s2.w == pytest.approx(842 * geo.PX_PER_PT)
    assert lay.height == pytest.approx(s2.bottom + MARGIN, abs=1)


def test_layout_wider_than_viewport_scrolls_horizontally():
    lay = compute_layout([A4], 3.0, 600, 400)
    assert lay.width > 600
    assert lay.slots[0].x == MARGIN


def test_visible_and_current_page():
    sizes = [A4] * 50
    lay = compute_layout(sizes, 1.0, 1000, 800)
    vis = lay.visible_slots(0, 0, 1000, 800)
    assert [s.index for s in vis] == [0]
    y = lay.slots[10].y + 100
    assert current_page_for_view(lay, 0, y, 1000, 800) == 10


def test_fit_zooms():
    sizes = [A4]
    z = fit_width_zoom(sizes, 1000, ViewMode.CONTINUOUS, 0)
    lay = compute_layout(sizes, z, 1000, 800)
    assert lay.slots[0].w == pytest.approx(1000 - 2 * MARGIN, abs=1)
    z = fit_page_zoom(sizes, 1000, 800, ViewMode.CONTINUOUS, 0)
    lay = compute_layout(sizes, z, 1000, 800)
    assert lay.slots[0].h == pytest.approx(800 - 2 * MARGIN, abs=1)


def test_next_page_index():
    assert next_page_index(ViewMode.CONTINUOUS, 10, 3, 1) == 4
    assert next_page_index(ViewMode.CONTINUOUS, 10, 0, -1) == 0
    assert next_page_index(ViewMode.TWO_PAGE, 10, 3, 1) == 4
    assert next_page_index(ViewMode.TWO_PAGE, 10, 4, -1) == 2
    assert next_page_index(ViewMode.SINGLE, 10, 9, 1) == 9


# ------------------------------------------------------------ render cache
def _key(page=1, rev=0, scale=1000, tx=0, ty=0, doc=1):
    return TileKey(doc, page, rev, scale, tx, ty)


def test_cache_lru_and_byte_budget():
    c = RenderCache(max_bytes=1000, max_entries=100)
    for i in range(5):
        c.put(_key(page=i), f"img{i}", 300)
    assert c.total_bytes <= 1000
    assert len(c) == 3
    assert _key(page=0) not in c and _key(page=4) in c
    # touching an entry protects it from eviction
    c.get(_key(page=2))
    c.put(_key(page=9), "x", 300)
    assert _key(page=2) in c and _key(page=3) not in c


def test_cache_entry_limit():
    c = RenderCache(max_bytes=10 ** 9, max_entries=10)
    for i in range(50):
        c.put(_key(tx=i), i, 1)
    assert len(c) == 10


def test_cache_new_revision_supersedes_old_tile():
    c = RenderCache()
    c.put(_key(rev=0), "old", 10)
    c.put(_key(rev=1), "new", 10)
    assert _key(rev=0) not in c


def test_cache_fallback_prefers_current_revision_and_close_scale():
    c = RenderCache()
    c.put(_key(scale=500, tx=WHOLE_PAGE, ty=WHOLE_PAGE), "thumb", 10)
    c.put(_key(scale=1800), "near", 10)
    c.put(_key(scale=4000), "far", 10)
    got = c.fallback(1, 1, 0, 2000, exclude_scale=2000)
    assert [v for _k, v in got] == ["near"]
    # an older revision is used when nothing current exists
    c2 = RenderCache()
    c2.put(_key(rev=3, scale=1000), "stale", 10)
    assert [v for _k, v in c2.fallback(1, 1, 4, 1000)] == ["stale"]


def test_cache_invalidation():
    c = RenderCache()
    c.put(_key(page=1), "a", 1)
    c.put(_key(page=2), "b", 1)
    c.put(_key(page=1, doc=2), "c", 1)
    c.invalidate_page(1, 1)
    assert _key(page=1) not in c and _key(page=2) in c
    c.invalidate_doc(1)
    assert len(c) == 1


# ------------------------------------------------------------ render queue
def _req(owner, key, prio=0, gen=0, job=None):
    return RenderRequest(owner, key, prio, job or (lambda: key), gen)


def test_queue_priority_order():
    q = RenderQueue()
    q.submit("a", [_req("a", "k2", 2), _req("a", "k0", 0), _req("a", "k1", 1)])
    assert [q.take(0).key for _ in range(3)] == ["k0", "k1", "k2"]


def test_queue_resubmit_drops_obsolete_jobs():
    q = RenderQueue()
    q.submit("canvas", [_req("canvas", f"zoom1-{i}") for i in range(10)], generation=1)
    dropped = q.submit("canvas", [_req("canvas", "zoom2-0", gen=2)], generation=2)
    assert dropped == 10
    taken = q.take(0)
    assert taken.key == "zoom2-0"
    assert q.take(0) is None


def test_queue_owners_are_independent():
    q = RenderQueue()
    q.submit("a", [_req("a", "a1", 5)])
    q.submit("b", [_req("b", "b1", 1)])
    q.submit("a", [_req("a", "a2", 0)])  # replaces a1 only
    assert {q.take(0).key, q.take(0).key} == {"a2", "b1"}


def test_queue_generation_marks_inflight_results_stale():
    q = RenderQueue()
    q.submit("c", [_req("c", "t", gen=1)], generation=1)
    r = q.take(0)
    q.submit("c", [], generation=2)  # zoom changed while rendering
    assert not q.is_current(r)
    q.done(r)


def test_queue_skips_keys_in_flight_and_blocks_until_work():
    q = RenderQueue()
    q.submit("c", [_req("c", "t")])
    r = q.take(0)
    q.submit("c", [_req("c", "t")])  # same key while rendering: not duplicated
    assert q.pending_count("c") == 0
    q.done(r)
    got = []
    th = threading.Thread(target=lambda: got.append(q.take(2.0)))
    th.start()
    q.submit("c", [_req("c", "late")])
    th.join(3)
    assert got and got[0].key == "late"
    q.close()
    assert q.take(0.01) is None
