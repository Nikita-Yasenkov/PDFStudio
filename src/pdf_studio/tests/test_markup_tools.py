"""Ticket 15: ToolController exclusivity + highlight/underline/strikeout tools."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.core.annotation_commands import (AddAnnotationCommand,
                                                 DeleteAnnotationCommand,
                                                 UpdateAnnotationCommand)
from pdf_studio.core.selection import select_between
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import AnnotationModel, Rect
from pdf_studio.tests.conftest import pump


def _kinds(view, page):
    return sorted(a.kind.value for a in view.session.annotations(page))


def _drag_words(view, page, first, last):
    words = view.session.words(page)
    a = view.canvas.pdf_to_widget(page, words[first].rect.center).toPoint()
    b = view.canvas.pdf_to_widget(page, words[last].rect.center).toPoint()
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)


def test_tools_are_exclusive(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    for name in ("hand", "highlight", "underline", "strikeout", "select"):
        window.set_tool(name)
        assert v.tools.active_name == name
        checked = [a for a in window.tool_group.actions() if a.isChecked()]
        assert len(checked) == 1 and checked[0] is window.actions[f"tool_{name}"]


def test_drag_with_highlight_tool_creates_annotation(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(1)
    pump(50)
    window.set_tool("highlight")
    rev0 = v.session.render_key(1)[1]
    _drag_words(v, 1, 3, 12)
    assert _kinds(v, 1) == ["highlight"]
    a = v.session.annotations(1)[0]
    assert len(a.quads) >= 1
    assert v.session.dirty
    assert v.session.render_key(1)[1] > rev0  # page re-renders with the annotation
    assert v.text_selection is None
    assert v.tools.active_name == "highlight"  # sticky without a prior selection


def test_selection_then_tool_applies_once(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    words = v.session.words(1)
    v.set_text_selection(select_between(1, words, words[3].rect.center, words[9].rect.center))
    window.set_tool("underline")
    assert _kinds(v, 1) == ["underline"]
    assert v.tools.active_name == "select"
    assert window.actions["tool_select"].isChecked()


def test_markup_undo_redo_and_dirty(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    words = v.session.words(1)
    v.set_text_selection(select_between(1, words, words[3].rect.center, words[9].rect.center))
    window.set_tool("strikeout")
    assert _kinds(v, 1) == ["strikeout"]
    window.actions["undo"].trigger()
    assert _kinds(v, 1) == []
    assert not v.session.dirty
    window.actions["redo"].trigger()
    assert _kinds(v, 1) == ["strikeout"]
    assert v.session.dirty


# -------------------------------------------------------- commands (Qt-free)
def test_annotation_commands_round_trip(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    try:
        pid = s.page_id(0)
        add = AddAnnotationCommand(pid, AnnotationModel(K.RECTANGLE, Rect(50, 50, 150, 120),
                                                        color=(1, 0, 0)))
        s.stack.push(add)
        aid = add.annot_id
        assert [a.id for a in s.annotations(0)] == [aid]
        cur = s.annotation_by_id(pid, aid)
        s.stack.push(UpdateAnnotationCommand(pid, aid, cur.translated(10, 20), "Move"))
        assert s.annotation_by_id(pid, aid).rect.x0 > cur.rect.x0 + 9
        s.stack.push(DeleteAnnotationCommand(pid, aid))
        assert s.annotations(0) == []
        s.stack.undo()  # restore (same id)
        assert s.annotation_by_id(pid, aid) is not None
        s.stack.undo()  # un-move
        assert abs(s.annotation_by_id(pid, aid).rect.x0 - cur.rect.x0) < 1
        s.stack.undo()  # un-add
        assert s.annotations(0) == [] and not s.dirty
        s.stack.redo(); s.stack.redo(); s.stack.redo()
        assert s.annotations(0) == []
        s.stack.undo()
        assert s.annotation_by_id(pid, aid) is not None
    finally:
        s.close()


def test_update_merge_collapses_slider_edits(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    try:
        pid = s.page_id(0)
        add = AddAnnotationCommand(pid, AnnotationModel(K.RECTANGLE, Rect(50, 50, 150, 120)))
        s.stack.push(add)
        base = s.annotation_by_id(pid, add.annot_id)
        for op in (0.9, 0.7, 0.5):
            s.stack.push(UpdateAnnotationCommand(pid, add.annot_id, base.with_changes(opacity=op),
                                                 "Opacity", merge_key="opacity"))
        assert len(s.stack) == 2
        assert abs(s.annotation_by_id(pid, add.annot_id).opacity - 0.5) < 0.01
        s.stack.undo()
        assert abs(s.annotation_by_id(pid, add.annot_id).opacity - 1.0) < 0.01
    finally:
        s.close()


def test_noop_update_not_recorded(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    try:
        pid = s.page_id(0)
        add = AddAnnotationCommand(pid, AnnotationModel(K.RECTANGLE, Rect(50, 50, 150, 120)))
        s.stack.push(add)
        cur = s.annotation_by_id(pid, add.annot_id)
        assert not s.stack.push(UpdateAnnotationCommand(pid, add.annot_id, cur))
        assert len(s.stack) == 1
    finally:
        s.close()
