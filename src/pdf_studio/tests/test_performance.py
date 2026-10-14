"""Ticket 35/42: bounded caches and responsiveness under zoom/scroll churn."""

from __future__ import annotations

import time

import pytest

from pdf_studio.core.render_cache import WHOLE_PAGE, RenderCache, TileKey
from pdf_studio.tests.conftest import pump


def _settle(window, ms=40, rounds=10):
    for _ in range(rounds):
        pump(ms)


def test_cache_never_exceeds_budget_under_churn(window, long_pdf):
    cache = window.render_service.cache
    cache.max_bytes = 24 * 1024 * 1024
    cache.max_entries = 200
    window.open_path(long_pdf)
    v = window.view
    bar = v.canvas.verticalScrollBar()
    peak_bytes = peak_entries = 0
    for i, zoom in enumerate((0.5, 1.0, 1.5, 2.0, 3.0, 0.75, 4.0, 1.25)):
        v.set_zoom(zoom)
        bar.setValue(int(bar.maximum() * (i + 1) / 9))
        pump(60)
        peak_bytes = max(peak_bytes, cache.total_bytes)
        peak_entries = max(peak_entries, len(cache))
    assert peak_bytes <= cache.max_bytes
    assert peak_entries <= cache.max_entries
    assert cache.stats().evictions > 0 or peak_bytes < cache.max_bytes


def test_zoom_sheds_stale_scale_levels(window, long_pdf):
    window.open_path(long_pdf)
    v = window.view
    cache = window.render_service.cache
    uid = v.session.uid
    for zoom in (1.0, 1.5, 2.0, 2.5):
        v.set_zoom(zoom)
        _settle(window, 30, 6)
    scales = {k.scale for k in cache if k.doc == uid and k.tx != WHOLE_PAGE}
    assert len(scales) <= 2  # current + previous (fallback while zooming)


def test_no_full_document_rasterisation(window, long_pdf):
    window.open_path(long_pdf)
    v = window.view
    bar = v.canvas.verticalScrollBar()
    for frac in (0.1, 0.3, 0.6, 0.95):
        bar.setValue(int(bar.maximum() * frac))
        pump(80)
    uid = v.session.uid
    pages = {k.page_id for k in window.render_service.cache if k.doc == uid and k.tx != WHOLE_PAGE}
    # only pages near the 4 visited positions (+ prefetch), not 400
    assert len(pages) <= 40


def test_paint_stays_fast_on_long_document(window, long_pdf):
    window.open_path(long_pdf)
    v = window.view
    v.set_zoom(1.0)
    _settle(window, 40, 8)
    vp = v.canvas.viewport()
    t0 = time.perf_counter()
    for _ in range(20):
        vp.repaint()
    per_paint = (time.perf_counter() - t0) / 20
    assert per_paint < 0.05  # painting cached tiles is cheap


def test_closing_a_document_frees_its_tiles(window, long_pdf, sample_pdf):
    window.open_path(long_pdf)
    window.open_path(sample_pdf)
    pump(300)
    cache = window.render_service.cache
    long_uid = window.views()[0].session.uid
    window.close_view(window.views()[0])
    assert not [k for k in cache if k.doc == long_uid]


def test_side_caches_are_capped(fixture_paths):
    from pdf_studio.core.session import DocumentSession
    s = DocumentSession.open(fixture_paths["long.pdf"])
    try:
        for i in range(0, 400, 3):
            s.words(i)
            s.annotations(i)
        assert len(s._words) <= 65 and len(s._annots) <= 65
        for i in range(30):
            s.doc.render(i, 0.2)
        assert len(s.doc._display_lists) <= s.doc._DISPLAY_LIST_CAPACITY
    finally:
        s.close()


def test_lru_eviction_order_unit():
    c = RenderCache(max_bytes=300, max_entries=100)
    keys = [TileKey(1, p, 0, 1000, 0, 0) for p in range(4)]
    for k in keys[:3]:
        c.put(k, "img", 100)
    c.get(keys[0])          # most recently used now
    c.put(keys[3], "img", 100)
    assert keys[1] not in c and keys[0] in c and keys[3] in c
