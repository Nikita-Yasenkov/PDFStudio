"""Tickets 22–24: Pages menu, viewer sync, thumbnail reorder & context menu,
insert blank / insert from PDF (UI level)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QItemSelectionModel, QPoint, Qt

from pdf_studio.tests.conftest import pump
from pdf_studio.tests.test_page_ops_engine import markers


@pytest.fixture
def view(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(50)
    return window.view


def test_rotate_current_page_from_menu(window, view):
    view.go_to_page(1)
    w0 = view.canvas.layout_.slot_for(1).w
    window.actions["rotate_right"].trigger()
    assert view.session.page_info(1).rotation == 90
    assert view.canvas.layout_.slot_for(1).w > w0  # layout rebuilt (landscape now)
    window.actions["rotate_left"].trigger()
    assert view.session.page_info(1).rotation == 0
    assert view.session.dirty
    window.actions["undo"].trigger()
    window.actions["undo"].trigger()
    assert not view.session.dirty


def test_delete_current_page_clamps_and_refreshes(window, view):
    view.go_to_page(11)
    window.actions["delete_page"].trigger()
    assert view.page_count == 11
    assert view.current_page == 10
    assert window.status_page.text() == "Page 11 of 11"
    assert window.thumbnails.model.rowCount() == 11
    assert view.bar.count_label.text() == "/ 11"
    window.actions["undo"].trigger()
    assert view.page_count == 12 and markers(view.session.doc)[-1] == 12


def test_duplicate_current_page(window, view):
    view.go_to_page(3)
    window.actions["duplicate_page"].trigger()
    assert markers(view.session.doc)[:6] == [1, 2, 3, 4, 4, 5]
    assert view.current_page == 4


def test_delete_disabled_for_last_page(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    assert window.actions["delete_page"].isEnabled()
    window.actions["delete_page"].trigger()
    assert v.page_count == 1
    assert not window.actions["delete_page"].isEnabled()


def test_page_actions_need_a_document(window):
    for key in ("rotate_left", "rotate_right", "delete_page", "duplicate_page",
                "insert_blank_after"):
        assert not window.actions[key].isEnabled()


def test_actions_apply_to_multi_selected_thumbnails(window, view):
    sm = window.thumbnails.list.selectionModel()
    for row in (2, 5, 7):
        sm.select(window.thumbnails.model.index(row), QItemSelectionModel.SelectionFlag.Select)
    assert window.target_page_ids() == [view.session.page_id(r) for r in (2, 5, 7)]
    assert "(3)" in window.actions["delete_page"].text()
    window.actions["rotate_right"].trigger()
    assert [view.session.page_info(i).rotation for i in (2, 5, 7)] == [90, 180, 90]
    window.actions["delete_page"].trigger()
    assert view.page_count == 9
    assert markers(view.session.doc)[:6] == [1, 2, 4, 5, 7, 9]


def test_search_results_refresh_after_structure_change(window, view):
    from pdf_studio.core.search import SearchOptions
    import time
    view.search.search(SearchOptions("PAGE-MARKER-12"))
    end = time.time() + 5
    while view.search.searching and time.time() < end:
        pump(20)
    assert [h.page_index for h in view.search.hits] == [11]
    window.actions["first_page"].trigger()
    window.actions["delete_page"].trigger()
    end = time.time() + 5
    pump(400)
    while view.search.searching and time.time() < end:
        pump(20)
    assert [h.page_index for h in view.search.hits] == [10]


def test_insert_blank_before_after_end(window, view):
    view.go_to_page(2)
    window.actions["insert_blank_after"].trigger()
    assert markers(view.session.doc)[:5] == [1, 2, 3, 0, 4]
    assert view.current_page == 3
    window.actions["insert_blank_before"].trigger()
    assert markers(view.session.doc)[:5] == [1, 2, 3, 0, 0]
    window.actions["insert_blank_end"].trigger()
    assert markers(view.session.doc)[-1] == 0 and view.page_count == 15
    for _ in range(3):
        window.actions["undo"].trigger()
    assert view.page_count == 12


# ---------------------------------------------------------------- ticket 23
def test_drop_target_geometry(window, view):
    lst = window.thumbnails.list
    lst.scrollToTop()
    pump(50)
    r1 = lst.visualRect(window.thumbnails.model.index(1))
    assert lst.drop_target(r1.center() - QPoint(0, r1.height() // 4)) == 1  # upper half
    assert lst.drop_target(r1.center() + QPoint(0, r1.height() // 4)) == 2  # lower half


def test_drag_drop_reorders_pages_undoably(window, view):
    panel = window.thumbnails
    sm = panel.list.selectionModel()
    sm.select(panel.model.index(0), QItemSelectionModel.SelectionFlag.ClearAndSelect)
    panel.list.rowsDropped.emit([0], 4)  # what dropEvent emits
    assert markers(view.session.doc)[:5] == [2, 3, 4, 1, 5]
    assert view.session.dirty
    assert panel.selected_rows() == [3]  # moved page stays selected
    window.actions["undo"].trigger()
    assert markers(view.session.doc)[:5] == [1, 2, 3, 4, 5]


def test_drag_multiple_pages_keeps_relative_order(window, view):
    window.thumbnails.list.rowsDropped.emit([1, 3], 12)  # to the end
    assert markers(view.session.doc)[-3:] == [12, 2, 4]


def test_reorder_does_not_rerender_thumbnails(window, view):
    pump(600)  # let visible thumbnails render
    cache = window.render_service.cache
    uid = view.session.uid
    before = {k for k in cache if k.doc == uid and k.tx < 0}
    assert before
    reqs = window.thumbnails.requests_submitted
    window.thumbnails.list.rowsDropped.emit([0], 3)
    pump(300)
    after = {k for k in cache if k.doc == uid and k.tx < 0}
    assert before <= after  # same (page id, revision) keys still valid
    # nothing new needed for pages that were already rendered
    assert window.thumbnails.requests_submitted - reqs <= 2


def test_page_context_menu_actions(window, view, monkeypatch):
    from PyQt6.QtWidgets import QMenu
    seen = {}

    def fake_exec(menu, *args, **kwargs):
        seen["texts"] = [a.text() for a in menu.actions() if a.text()]
        for a in menu.actions():
            if a.text().startswith("Rotate Right"):
                a.trigger()
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    ids = [view.session.page_id(2)]
    window._page_context_menu(ids, QPoint(0, 0))
    assert {"Rotate Left", "Rotate Right", "Duplicate", "Delete",
            "Insert Blank Page Before", "Insert Blank Page After"} <= set(seen["texts"])
    assert view.session.page_info(2).rotation == 90


def test_delete_key_in_thumbnails_deletes_selected_pages(window, view):
    panel = window.thumbnails
    sm = panel.list.selectionModel()
    sm.select(panel.model.index(4), QItemSelectionModel.SelectionFlag.ClearAndSelect)
    sm.select(panel.model.index(5), QItemSelectionModel.SelectionFlag.Select)
    panel.list.deletePressed.emit()
    assert view.page_count == 10
    assert markers(view.session.doc)[:6] == [1, 2, 3, 4, 7, 8]
