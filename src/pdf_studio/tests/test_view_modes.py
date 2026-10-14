"""Tickets 36/42: continuous / single-page / two-page view modes."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from pdf_studio.core.layout import (GAP, SPREAD_GAP, ViewMode, compute_layout,
                                    fit_page_zoom, fit_width_zoom, spreads)
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.settings import AppSettings

A4 = (595.0, 842.0)


# ------------------------------------------------------------ layout policy
def test_single_page_layout_shows_only_current_page():
    lay = compute_layout([A4] * 10, 1.0, 1000, 1400, ViewMode.SINGLE, current=4)
    assert [s.index for s in lay.slots] == [4]
    s = lay.slots[0]
    assert s.y > GAP  # vertically centred when it fits
    assert lay.height >= 1400


def test_two_page_layout_pairs_pages_around_a_spine():
    lay = compute_layout([A4] * 5, 1.0, 2000, 900, ViewMode.TWO_PAGE)
    assert lay.rows == ((0, 1), (2, 3), (4,))
    left, right = lay.slot_for(0), lay.slot_for(1)
    assert left.y == right.y
    assert right.x - left.right == pytest.approx(SPREAD_GAP, abs=1)
    spine = (left.right + right.x) / 2
    assert spine == pytest.approx(lay.width / 2, abs=1)
    lone = lay.slot_for(4)
    assert lone.x + lone.w / 2 == pytest.approx(lay.width / 2, abs=1)
    assert spreads(3) == [(0, 1), (2,)]


def test_fit_zooms_per_mode():
    sizes = [A4] * 6
    z = fit_width_zoom(sizes, 1248, ViewMode.TWO_PAGE, 0)
    lay = compute_layout(sizes, z, 1248, 900, ViewMode.TWO_PAGE)
    row = [lay.slot_for(0), lay.slot_for(1)]
    assert row[1].right - row[0].x == pytest.approx(1248 - 48, abs=2)
    z = fit_page_zoom(sizes, 1248, 900, ViewMode.TWO_PAGE, 2)
    lay = compute_layout(sizes, z, 1248, 900, ViewMode.TWO_PAGE)
    assert lay.slot_for(2).h <= 900 - 48 + 1


# ------------------------------------------------------------------- UI
@pytest.fixture
def view(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(50)
    return window.view


def test_switch_modes_from_view_menu(window, view):
    window.actions["mode_two"].trigger()
    assert view.view_mode == ViewMode.TWO_PAGE
    assert view.canvas.layout_.rows[0] == (0, 1)
    window.actions["mode_single"].trigger()
    assert view.view_mode == ViewMode.SINGLE
    assert view.canvas.visible_pages() == [view.current_page]
    window.actions["mode_continuous"].trigger()
    assert view.view_mode == ViewMode.CONTINUOUS
    assert window.actions["mode_continuous"].isChecked()


def test_navigation_in_two_page_mode(window, view):
    window.actions["mode_two"].trigger()
    window.actions["next_page"].trigger()
    assert view.current_page == 2
    assert window.status_page.text() == "Pages 3–4 of 12"
    window.actions["next_page"].trigger()
    assert view.current_page == 4
    window.actions["prev_page"].trigger()
    assert view.current_page == 2
    view.go_to_page(9)  # odd page: lands on its spread (9-10)
    assert window.status_page.text() == "Pages 9–10 of 12"


def test_navigation_in_single_page_mode(window, view):
    window.actions["mode_single"].trigger()
    window.actions["next_page"].trigger()
    window.actions["next_page"].trigger()
    assert view.current_page == 2 and view.canvas.visible_pages() == [2]
    window.actions["last_page"].trigger()
    assert view.canvas.visible_pages() == [11]
    view.go_to_page(4)
    assert view.canvas.visible_pages() == [4]
    window.actions["fit_page"].trigger()
    slot = view.canvas.layout_.slot_for(4)
    assert slot.h <= view.canvas.viewport().height() and slot.w <= view.canvas.viewport().width()


def test_single_page_wheel_flips_at_edges(window, view):
    window.actions["mode_single"].trigger()
    window.actions["fit_page"].trigger()  # page fits: scrollbar is at its end immediately
    vp = view.canvas.viewport()
    pos = QPointF(vp.width() / 2, vp.height() / 2)

    def wheel(dy):
        ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(vp, ev)

    wheel(-120)
    assert view.current_page == 1
    wheel(-60)
    assert view.current_page == 1  # half a notch past the edge: no flip yet
    wheel(-60)
    assert view.current_page == 2
    wheel(120)
    assert view.current_page == 1


def test_mode_persists_across_restarts(qapp, tmp_path, sample_pdf, monkeypatch):
    from pdf_studio.ui.dialogs import prompts
    from pdf_studio.ui.main_window import MainWindow
    monkeypatch.setattr(prompts, "show_error", lambda *a, **k: None)
    ini = str(tmp_path / "modes.ini")
    w = MainWindow(settings=AppSettings.from_file(ini))
    w.show()
    w.open_path(sample_pdf)
    w.actions["mode_two"].trigger()
    w.close()
    w.deleteLater()
    pump()
    assert AppSettings.from_file(ini).view_mode == "two_page"
    w2 = MainWindow(settings=AppSettings.from_file(ini))
    w2.show()
    w2.open_path(sample_pdf)
    assert w2.view.view_mode == ViewMode.TWO_PAGE
    assert w2.actions["mode_two"].isChecked()
    w2.close()
    w2.deleteLater()
    pump()


def test_view_mode_is_per_tab(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    a = window.view
    window.actions["mode_single"].trigger()
    window.open_path(plain_pdf)  # new tabs use the last chosen mode
    b = window.view
    assert b.view_mode == ViewMode.SINGLE
    window.actions["mode_continuous"].trigger()
    window.activate_view(a)
    assert a.view_mode == ViewMode.SINGLE and window.actions["mode_single"].isChecked()
