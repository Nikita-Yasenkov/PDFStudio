"""Ticket 05: multiple documents in tabs with isolated view state."""

from __future__ import annotations

import pytest

from pdf_studio.tests.conftest import pump


def test_each_tab_keeps_its_own_view_state(window, sample_pdf, plain_pdf, long_pdf):
    for p in (sample_pdf, plain_pdf, long_pdf):
        assert window.open_path(p)
    assert window.tabs.count() == 3
    a, b, c = window.views()
    assert window.view is c

    window.activate_view(a)
    a.set_zoom(0.75)
    a.go_to_page(5)
    scroll_a = a.canvas.verticalScrollBar().value()

    window.activate_view(c)
    c.set_zoom(1.5)
    c.go_to_page(200)
    pump(50)

    window.activate_view(a)
    pump(50)
    assert window.view is a
    assert a.zoom == pytest.approx(0.75)
    assert a.current_page == 5
    assert a.canvas.verticalScrollBar().value() == scroll_a
    assert window.status_page.text() == "Page 6 of 12"

    window.activate_view(c)
    assert c.current_page == 200 and c.zoom == pytest.approx(1.5)
    assert window.status_page.text() == "Page 201 of 400"


def test_sessions_are_independent_objects(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    window.open_path(plain_pdf)
    a, b = window.views()
    assert a.session is not b.session
    assert a.session.uid != b.session.uid
    assert a.session.stack is not b.session.stack


def test_closing_a_tab_leaves_others_intact(window, sample_pdf, plain_pdf, long_pdf):
    for p in (sample_pdf, plain_pdf, long_pdf):
        window.open_path(p)
    a, b, c = window.views()
    window.activate_view(c)
    c.go_to_page(10)
    window.close_view(b)
    assert window.views() == [a, c]
    assert not a.session.doc.closed and not c.session.doc.closed
    assert b.session.doc.closed
    assert c.current_page == 10
    window.close_view(c)
    assert window.view is a
    window.close_view(a)
    assert window.view is None
    assert not window.tab_strip.isVisible()


def test_reopening_same_file_activates_existing_tab(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    window.open_path(plain_pdf)
    window.open_path(sample_pdf)
    assert window.tabs.count() == 2
    assert window.view.session.path.endswith("sample.pdf")


def test_tab_cycling(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    window.open_path(plain_pdf)
    a, b = window.views()
    assert window.view is b
    window.actions["next_tab"].trigger()
    assert window.view is a
    window.actions["prev_tab"].trigger()
    assert window.view is b


def test_tool_choice_follows_active_tab(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    window.open_path(plain_pdf)
    a, b = window.views()
    window.set_tool("hand")
    assert b.tools.active_name == "hand"
    window.activate_view(a)
    assert a.tools.active_name == "hand"
