"""Ticket 17: select / move / resize / delete annotations + properties inspector."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest

from pdf_studio.core.annotation_commands import AddAnnotationCommand
from pdf_studio.core.annotation_geometry import (apply_handle, clamp_delta,
                                                 handle_at, hit_test, resize_rect)
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import AnnotationModel, Point, Rect
from pdf_studio.tests.conftest import pump


# ------------------------------------------------------------ geometry core
def test_hit_testing_by_kind():
    rect = AnnotationModel(K.RECTANGLE, Rect(100, 100, 200, 160), width=2)
    filled = rect.with_changes(fill=(1, 1, 0))
    ink = AnnotationModel(K.INK, Rect(0, 0, 0, 0), strokes=((Point(0, 0), Point(100, 100)),))
    hl = AnnotationModel(K.HIGHLIGHT, Rect(10, 300, 90, 312), quads=(Rect(10, 300, 90, 312),))
    assert hit_test([rect], Point(100.5, 130), 2) is rect      # on the border
    assert hit_test([rect], Point(150, 130), 2) is None         # hollow inside
    assert hit_test([filled], Point(150, 130), 2) is filled     # filled inside
    assert hit_test([ink], Point(51, 49), 2) is ink
    assert hit_test([ink], Point(80, 20), 2) is None
    assert hit_test([hl], Point(50, 305), 2) is hl
    # topmost wins
    top = filled.with_changes(id="top")
    assert hit_test([filled, top], Point(150, 130), 2) is top


def test_resize_and_handles():
    r = Rect(0, 0, 100, 50)
    assert resize_rect(r, "se", Point(150, 80)) == Rect(0, 0, 150, 80)
    assert resize_rect(r, "w", Point(-20, 999)) == Rect(-20, 0, 100, 50)
    # minimum size and keep-aspect
    assert resize_rect(r, "e", Point(-50, 0)).width >= 6
    keep = resize_rect(r, "se", Point(300, 60), keep_aspect=True)
    assert keep.width / keep.height == pytest.approx(2.0)
    m = AnnotationModel(K.RECTANGLE, r)
    assert handle_at(m, Point(100, 50), 2) == "se"
    line = AnnotationModel(K.LINE, r, strokes=((Point(0, 0), Point(100, 50)),))
    moved = apply_handle(line, "p1", Point(10, 90))
    assert moved.strokes[0][1] == Point(10, 90)
    assert clamp_delta(r, -50, 900, 595, 842) == (0, 792)


# ---------------------------------------------------------------------- UI
@pytest.fixture
def view(window, plain_pdf):
    window.open_path(plain_pdf)
    pump(30)
    v = window.view
    v.execute(AddAnnotationCommand(v.session.page_id(0),
                                   AnnotationModel(K.RECTANGLE, Rect(100, 300, 200, 360),
                                                   color=(1, 0, 0), fill=(1, 1, 0), width=2)))
    v.session.stack.set_clean()
    return v


def wp(view, x, y):
    return view.canvas.pdf_to_widget(0, Point(x, y)).toPoint()


def the_annot(view):
    annots = view.session.annotations(0)
    assert len(annots) == 1
    return annots[0]


def test_click_selects_and_inspector_shows(window, view):
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 150, 330))
    sel = view.selected_annotation()
    assert sel is not None and sel[1].kind == K.RECTANGLE
    assert window.inspector.isVisible()
    assert window.inspector.title.text() == "Rectangle"
    assert window.actions["delete"].isEnabled()
    # clicking blank page area deselects
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 500, 780))
    assert view.selected_annotation() is None
    assert not window.inspector.isVisible()


def test_drag_moves_in_pdf_coordinates_and_undoes(window, view):
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=wp(view, 150, 330))
    QTest.mouseMove(vp, wp(view, 160, 340))
    QTest.mouseMove(vp, wp(view, 190, 370))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=wp(view, 190, 370))
    r = the_annot(view).rect
    assert r.x0 == pytest.approx(140, abs=1.5) and r.y0 == pytest.approx(340, abs=1.5)
    assert view.session.dirty
    window.actions["undo"].trigger()
    r = the_annot(view).rect
    assert r.x0 == pytest.approx(100, abs=1) and not view.session.dirty


def test_resize_with_handle(window, view):
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 150, 330))
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=wp(view, 200, 360))  # se handle
    QTest.mouseMove(vp, wp(view, 230, 380))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=wp(view, 230, 380))
    r = the_annot(view).rect
    assert (r.x0, r.y0) == pytest.approx((100, 300), abs=1)
    assert (r.x1, r.y1) == pytest.approx((230, 380), abs=1.5)


def test_delete_key_and_action_are_undoable(window, view):
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 150, 330))
    window.actions["delete"].trigger()
    assert view.session.annotations(0) == []
    assert view.selected_annotation() is None
    window.actions["undo"].trigger()
    a = the_annot(view)
    assert a.fill == pytest.approx((1, 1, 0), abs=0.01)
    # keyboard Delete through the canvas
    view.select_annotation(0, a.id)
    view.canvas.setFocus()
    QTest.keyClick(view.canvas, Qt.Key.Key_Delete)
    assert view.session.annotations(0) == []


def test_arrow_keys_nudge(window, view):
    a = the_annot(view)
    view.select_annotation(0, a.id)
    view.canvas.setFocus()
    sel = view.tools.tools["select"]
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    for _ in range(3):
        sel.key_press(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    assert the_annot(view).rect.x0 == pytest.approx(103, abs=0.5)
    assert len(view.session.stack) == 2  # add + one merged nudge


def test_inspector_edits_properties(window, view):
    a = the_annot(view)
    view.select_annotation(0, a.id)
    insp = window.inspector
    insp.color_row.colorChosen.emit(QColor("#007AFF"))
    assert the_annot(view).color == pytest.approx((0, 122 / 255, 1), abs=0.01)
    for v in (80, 60, 40):
        insp.opacity_slider.slider.setValue(v)
    assert the_annot(view).opacity == pytest.approx(0.4, abs=0.01)
    insp.width_slider.slider.setValue(8)
    assert the_annot(view).width == pytest.approx(4.0, abs=0.01)
    insp.fill_row.colorChosen.emit(QColor())  # "no fill"
    assert the_annot(view).fill is None
    insp.contents.setPlainText("Looks off")
    insp._commit_contents()
    assert the_annot(view).contents == "Looks off"
    # opacity drags merged into one undo step: color, opacity, width, fill, comment
    assert len(view.session.stack) == 1 + 5
    for _ in range(5):
        window.actions["undo"].trigger()
    back = the_annot(view)
    assert back.color == pytest.approx((1, 0, 0), abs=0.01) and back.opacity == pytest.approx(1.0)


def test_inspector_edits_tool_defaults(window, view):
    window.set_tool("ink")
    insp = window.inspector
    assert insp.isVisible() and insp.title.text() == "Draw"
    insp.width_slider.slider.setValue(10)
    assert window.tool_defaults.get("ink", "width") == pytest.approx(5.0)
    assert window.settings.tool_value("ink", "width") in (5.0, "5.0", 5)


def test_double_click_opens_editor(window, view):
    a = the_annot(view)
    QTest.mouseDClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 150, 330))
    pump(30)
    from pdf_studio.ui.annotation_editors import NotePopup
    popups = view.findChildren(NotePopup)
    assert popups and popups[-1].isVisible()
    popups[-1].edit.setPlainText("comment via popup")
    popups[-1].commit()
    assert the_annot(view).contents == "comment via popup"
    assert the_annot(view).id == a.id
