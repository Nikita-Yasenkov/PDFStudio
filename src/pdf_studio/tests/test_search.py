"""Tickets 07/10: text search (core runner + async UI controller)."""

from __future__ import annotations

import time

import pytest

from pdf_studio.core.search import SearchOptions, SearchRun, search_all
from pdf_studio.core.session import DocumentSession
from pdf_studio.tests.conftest import pump


@pytest.fixture
def session(fixture_paths):
    s = DocumentSession.open(fixture_paths["sample.pdf"])
    yield s
    s.close()


def test_case_insensitive_by_default(session):
    hits = search_all(session, SearchOptions("page-marker-7"))
    assert [h.page_index for h in hits] == [6]
    assert "PAGE-MARKER-7" in hits[0].snippet
    snippet = hits[0].snippet
    assert snippet[hits[0].match_start:hits[0].match_end].lower() == "page-marker-7"


def test_case_sensitive_and_whole_words(session):
    assert search_all(session, SearchOptions("page-marker-7", case_sensitive=True)) == []
    assert len(search_all(session, SearchOptions("PAGE-MARKER-7", case_sensitive=True))) == 1
    # "PAGE-MARKER-1" also prefixes PAGE-MARKER-10/11/12 unless whole words
    loose = {h.page_index for h in search_all(session, SearchOptions("PAGE-MARKER-1"))}
    strict = {h.page_index for h in search_all(session, SearchOptions("PAGE-MARKER-1",
                                                                       whole_words=True))}
    assert loose == {0, 9, 10, 11}
    assert strict == {0}


def test_phrase_across_whitespace_and_rects(session):
    hits = search_all(session, SearchOptions("important   sentence"))
    assert len(hits) == 1 and hits[0].page_index == 2
    r = hits[0].bounds
    w, h = session.page_size(2)
    assert 0 < r.x0 < r.x1 < w and 0 < r.y0 < r.y1 < h


def test_hits_on_rotated_page_are_in_display_space(session):
    hits = search_all(session, SearchOptions("PAGE-MARKER-6"))
    assert hits and hits[0].page_index == 5
    w, h = session.page_size(5)
    r = hits[0].bounds
    assert w > h  # landscape when displayed
    assert r.height > r.width  # vertical text after rotation
    assert 0 <= r.x0 and r.x1 <= w + 1 and r.y1 <= h + 1


def test_empty_query_returns_nothing(session):
    assert search_all(session, SearchOptions("   ")) == []


def test_run_can_be_cancelled(fixture_paths):
    s = DocumentSession.open(fixture_paths["long.pdf"])
    try:
        run = SearchRun(s, SearchOptions("lazy"))
        seen = []

        def on_batch(batch):
            seen.extend(batch)
            run.cancel()

        assert run.run(on_batch) is False
        assert 0 < len(seen) < 400
    finally:
        s.close()


def test_ui_search_streams_results_without_blocking(window, long_pdf):
    window.open_path(long_pdf)
    v = window.view
    t0 = time.perf_counter()
    window.actions["find"].trigger()
    assert window.side.currentWidget() is window.search_panel
    assert window.search_panel.field.hasFocus()
    window.search_panel.field.setText("needle in a haystack")
    window.search_panel._run_now(jump=True)
    started = time.perf_counter() - t0
    assert started < 0.5  # search runs off the UI thread
    deadline = time.time() + 20
    while v.search.searching and time.time() < deadline:
        pump(30)
    assert not v.search.searching
    assert len(v.search.hits) == 8  # every 50th page
    assert {h.page_index for h in v.search.hits} == {49, 99, 149, 199, 249, 299, 349, 399}
    # first result was revealed automatically
    assert v.search.current == 0
    assert v.current_page == 49
    window.actions["find_next"].trigger()
    assert v.search.current == 1 and v.current_page == 99
    window.actions["find_prev"].trigger()
    window.actions["find_prev"].trigger()
    assert v.search.current == 7  # wraps
    assert "8 of 8" in window.search_panel.status.text()


def test_new_query_discards_previous_results(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.search.search(SearchOptions("lazy dog"))
    v.search.search(SearchOptions("PAGE-MARKER-3", whole_words=True))
    deadline = time.time() + 10
    while v.search.searching and time.time() < deadline:
        pump(20)
    assert [h.page_index for h in v.search.hits] == [2]


def test_selecting_a_result_row_jumps(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    window.find()
    window.search_panel.field.setText("Figure 1")
    window.search_panel._run_now(jump=False)
    deadline = time.time() + 10
    while v.search.searching and time.time() < deadline:
        pump(20)
    panel = window.search_panel
    assert panel.model.rowCount() == 2  # page header + hit
    panel.list.setCurrentIndex(panel.model.index(1))
    assert v.search.current == 0
    assert v.current_page == 3
