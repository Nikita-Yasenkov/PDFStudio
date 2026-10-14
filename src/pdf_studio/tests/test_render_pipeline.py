"""Ticket 10: render pipeline integration (worker thread, cache, cancellation)."""

from __future__ import annotations

import time

import pytest

from pdf_studio.core import geometry as geo
from pdf_studio.core.render_cache import RenderCache, TileKey
from pdf_studio.core.render_queue import RenderRequest
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf.models import Rect
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.render_service import RenderService, tile_job


@pytest.fixture
def service(qapp):
    svc = RenderService(RenderCache(max_bytes=64 * 2 ** 20))
    yield svc
    svc.shutdown()


@pytest.fixture
def session(fixture_paths):
    s = DocumentSession.open(fixture_paths["sample.pdf"])
    yield s
    s.close()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        pump(10)
        if pred():
            return True
    return False


def test_worker_renders_tile_into_cache(service, session):
    pid, rev = session.render_key(0)
    scale = geo.device_scale(1.0, 1.0)
    key = TileKey(session.uid, pid, rev, geo.scale_key(scale), 0, 0)
    clip = geo.tile_clip(0, 0, *geo.tile_grid(*session.page_size(0), scale, 512)[:2], scale, 512)
    seen = []
    service.register("t", seen.append)
    service.submit("t", [RenderRequest("t", key, 0, tile_job(session, pid, rev, scale, clip), 1)], 1)
    assert _wait(lambda: key in service.cache)
    img = service.cache.get(key)
    assert img.width() == 512 and img.height() == 512
    assert seen == [key]


def test_results_for_obsolete_generation_are_discarded(service, session):
    pid, rev = session.render_key(1)
    started = []

    def slow_job():
        started.append(1)
        time.sleep(0.2)
        return tile_job(session, pid, rev, 0.5, None)()

    key = TileKey(session.uid, pid, rev, 500, -1, -1)
    service.submit("t", [RenderRequest("t", key, 0, slow_job, 1)], 1)
    assert _wait(lambda: started)
    # zoom changed while the job was running: generation 2 supersedes it
    service.submit("t", [], 2)
    assert _wait(lambda: service.discarded_results == 1)
    assert key not in service.cache


def test_queued_jobs_dropped_when_view_moves_on(service, session):
    blocker_started = []

    def blocker():
        blocker_started.append(1)
        time.sleep(0.3)
        return None

    service.submit("busy", [RenderRequest("busy", "block", 0, blocker, 0)], 0)
    assert _wait(lambda: blocker_started)
    ran = []
    reqs = [RenderRequest("t", f"old{i}", 1, lambda i=i: ran.append(i), 1) for i in range(20)]
    service.submit("t", reqs, 1)
    service.submit("t", [], 2)  # e.g. scrolled away before the worker got to them
    pump(500)
    assert ran == []
    assert service.queue.dropped >= 20


def test_stale_revision_is_not_rendered(service, session):
    pid, rev = session.render_key(2)
    job = tile_job(session, pid, rev, 0.5, None)
    session.touch_pages([pid])  # page content changed after the job was queued
    assert job() is None


def test_closed_document_renders_nothing(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    pid, rev = s.render_key(0)
    s.close()
    assert s.render_page_id(pid, rev, 1.0) is None


def test_tiles_cover_the_page_exactly(session):
    """Tiles rendered with tile_clip line up with the full-page raster."""
    scale = 1.7
    rw, rh, cols, rows = geo.tile_grid(*session.page_size(0), scale, 256)
    covered_w = 0
    for tx in range(cols):
        clip = geo.tile_clip(tx, 0, rw, rh, scale, 256)
        img = session.doc.render(0, scale, clip)
        assert img.x == tx * 256
        covered_w += img.width
    assert abs(covered_w - rw) <= cols  # at most 1px rounding per tile


def test_tile_clip_matches_pdf_space(session):
    scale = 2.0
    rw, rh, _c, _r = geo.tile_grid(*session.page_size(0), scale, 512)
    clip = geo.tile_clip(1, 1, rw, rh, scale, 512)
    assert clip == Rect(256, 256, 512, 512)
