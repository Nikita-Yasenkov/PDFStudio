"""Ticket 16: sticky notes, free text, ink (and shape tools)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import Point
from pdf_studio.tests.conftest import pump


@pytest.fixture
def view(window, plain_pdf):
    window.open_path(plain_pdf)
    pump(50)
    return window.view


def wp(view, x, y, page=0):
    return view.canvas.pdf_to_widget(page, Point(x, y)).toPoint()


def kinds(view, page=0):
    return [a.kind for a in view.session.annotations(page)]


def drag(view, points):
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=points[0])
    for q in points[1:]:
        QTest.mouseMove(vp, q)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=points[-1])


def test_sticky_note_place_and_contents(window, view):
    window.set_tool("note")
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 300))
    popup = view.tools.tools["note"].popup
    assert popup is not None and popup.isVisible()
    QTest.keyClicks(popup.edit, "Check the numbers")
    popup.commit()
    notes = [a for a in view.session.annotations(0) if a.kind == K.NOTE]
    assert len(notes) == 1 and notes[0].contents == "Check the numbers"
    assert notes[0].rect.contains(Point(300, 300))
    assert view.tools.active_name == "select"  # notes are one-shot
    window.actions["undo"].trigger()
    assert kinds(view) == []


def test_empty_note_is_not_created(window, view):
    window.set_tool("note")
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 300))
    view.tools.tools["note"].popup.commit()
    assert kinds(view) == [] and not view.session.dirty


def test_free_text_create_and_edit(window, view):
    window.set_tool("free_text")
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 100, 500))
    ed = view.tools.tools["free_text"].editor
    assert ed is not None and ed.hasFocus()
    QTest.keyClicks(ed, "Hello there")
    ed.commit()
    ft = [a for a in view.session.annotations(0) if a.kind == K.FREE_TEXT]
    assert len(ft) == 1 and ft[0].contents == "Hello there"
    assert ft[0].rect.width > 40
    # edit the existing box through the same editor
    from pdf_studio.ui.annotation_tools import open_free_text_editor
    ed2 = open_free_text_editor(view, 0, ft[0], ft[0].rect, auto_width=False)
    ed2.selectAll()
    QTest.keyClicks(ed2, "Changed")
    ed2.commit()
    ft = [a for a in view.session.annotations(0) if a.kind == K.FREE_TEXT]
    assert ft[0].contents == "Changed"
    window.actions["undo"].trigger()
    ft = [a for a in view.session.annotations(0) if a.kind == K.FREE_TEXT]
    assert ft[0].contents == "Hello there"


def test_switching_tool_keeps_typed_text(window, view):
    window.set_tool("free_text")
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 100, 500))
    QTest.keyClicks(view.tools.tools["free_text"].editor, "Keep me")
    window.set_tool("hand")
    pump(20)
    assert [a.contents for a in view.session.annotations(0)] == ["Keep me"]
    assert view.tools.active_name == "hand"


def test_ink_stroke_persists_and_undoes(window, view):
    window.set_tool("ink")
    pts = [wp(view, 80 + i * 5, 420 + (15 if i % 2 else 0)) for i in range(20)]
    drag(view, pts)
    ink = [a for a in view.session.annotations(0) if a.kind == K.INK]
    assert len(ink) == 1 and len(ink[0].strokes[0]) >= 3
    assert view.tools.active_name == "ink"  # sticky for multiple strokes
    assert view.session.dirty
    window.actions["undo"].trigger()
    assert kinds(view) == [] and not view.session.dirty
    window.actions["redo"].trigger()
    assert kinds(view) == [K.INK]


@pytest.mark.parametrize("tool,kind", [("rectangle", K.RECTANGLE), ("ellipse", K.ELLIPSE),
                                       ("line", K.LINE), ("arrow", K.ARROW)])
def test_shape_tools(window, view, tool, kind):
    window.set_tool(tool)
    drag(view, [wp(view, 100, 400), wp(view, 160, 430), wp(view, 220, 470)])
    got = view.session.annotations(0)
    assert [a.kind for a in got] == [kind]
    if kind in (K.RECTANGLE, K.ELLIPSE):
        assert got[0].rect.x0 == pytest.approx(100, abs=1.5)
        assert got[0].rect.y1 == pytest.approx(470, abs=1.5)
    else:
        a, b = got[0].strokes[0]
        assert (a.x, b.x) == pytest.approx((100, 220), abs=1.5)


def test_tiny_drag_does_not_create_shape(window, view):
    window.set_tool("rectangle")
    drag(view, [wp(view, 100, 400), wp(view, 101, 401)])
    assert kinds(view) == []


def test_escape_cancels_ink_and_returns_to_select(window, view):
    window.set_tool("ink")
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=wp(view, 100, 400))
    QTest.mouseMove(vp, wp(view, 150, 420))
    view.canvas.setFocus()
    QTest.keyClick(view.canvas, Qt.Key.Key_Escape)  # cancels the stroke
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=wp(view, 150, 420))
    assert kinds(view) == []
    QTest.keyClick(view.canvas, Qt.Key.Key_Escape)  # back to Select
    assert view.tools.active_name == "select"
