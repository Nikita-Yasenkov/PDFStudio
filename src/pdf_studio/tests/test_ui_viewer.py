"""Tickets 02–04: shell, viewer, zoom, pan, navigation (headless UI)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from pdf_studio.core import zoom as zm
from pdf_studio.core.layout import ViewMode


from pdf_studio.tests.conftest import pump


def test_open_shows_first_page_and_close_clears(window, sample_pdf):
    assert window.open_path(sample_pdf)
    pump(200)
    view = window.view
    assert view is not None and view.current_page == 0
    assert view.canvas.visible_pages()[0] == 0
    assert "Page 1 of 12" in window.status_page.text()
    window.close_document()
    assert window.view is None
    assert window.stack.currentWidget() is window.empty


def test_open_error_is_reported(window, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    assert not window.open_path(str(bad))
    assert window.errors and window.view is None


def test_zoom_actions(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(100)
    v = window.view
    window.actions["actual_size"].trigger()
    assert v.zoom == pytest.approx(1.0)
    window.actions["zoom_in"].trigger()
    assert v.zoom == pytest.approx(1.1)
    window.actions["zoom_out"].trigger()
    window.actions["zoom_out"].trigger()
    assert v.zoom == pytest.approx(0.9)
    assert v.zoom_mode == zm.ZoomMode.CUSTOM
    window.actions["fit_width"].trigger()
    slot = v.canvas.layout_.slot_for(0)
    assert slot.w == pytest.approx(v.canvas.viewport().width() - 48, abs=2)
    window.actions["fit_page"].trigger()
    slot = v.canvas.layout_.slot_for(v.current_page)
    assert slot.h <= v.canvas.viewport().height() - 40
    assert window.status_zoom.text() == zm.format_percent(v.zoom)


def test_ctrl_wheel_zooms_around_cursor(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(100)
    v = window.view
    v.set_zoom(1.0)
    vp = v.canvas.viewport()
    pos = QPointF(vp.width() / 2, 200)
    before = v.canvas.page_at(pos)
    ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(v.canvas.viewport(), ev)
    assert v.zoom == pytest.approx(zm.WHEEL_STEP)
    after = v.canvas.page_at(pos)
    assert after[0] == before[0]
    assert after[1].x == pytest.approx(before[1].x, abs=1.5)
    assert after[1].y == pytest.approx(before[1].y, abs=1.5)


def test_hand_tool_pans(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(100)
    v = window.view
    v.set_zoom(2.0)
    window.actions["tool_hand"].trigger()
    assert v.tools.active_name == "hand"
    vp = v.canvas.viewport()
    y0 = v.canvas.verticalScrollBar().value()
    x0 = v.canvas.horizontalScrollBar().value()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=QPoint(400, 400))
    QTest.mouseMove(vp, QPoint(350, 300))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=QPoint(350, 300))
    assert v.canvas.verticalScrollBar().value() == y0 + 100
    assert v.canvas.horizontalScrollBar().value() == x0 + 50


def test_page_navigation_and_status(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(100)
    v = window.view
    window.actions["next_page"].trigger()
    window.actions["next_page"].trigger()
    assert v.current_page == 2
    assert window.status_page.text() == "Page 3 of 12"
    window.actions["last_page"].trigger()
    assert v.current_page == 11
    assert not window.actions["next_page"].isEnabled()
    window.actions["prev_page"].trigger()
    assert v.current_page == 10
    window.actions["first_page"].trigger()
    assert v.current_page == 0
    v.go_to_page(4)
    assert "297 × 210" in window.status_size.text() or "A4" in window.status_size.text()


def test_only_visible_pages_render(window, long_pdf):
    window.open_path(long_pdf)
    v = window.view
    v.set_zoom(1.0)
    pump(600)
    cache = window.render_service.cache
    pages = {k.page_id for k in cache if k.doc == v.session.uid}
    assert 0 < len(pages) <= 6  # visible pages + prefetch buffer, not 400
