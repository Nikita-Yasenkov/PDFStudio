"""Replacement text in any script, and placed text/images that can be moved,
resized and deleted afterwards (engine, commands, UI)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest

from pdf_studio.core.edit_commands import (DeleteObjectCommand, PlaceImageCommand,
                                           ReplaceTextCommand, TransformObjectCommand)
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PdfDocument, Rect
from pdf_studio.pdf.models import Point, TextStyle
from pdf_studio.tests.conftest import pump
from pdf_studio.tests.test_editing_engine import _png
from pdf_studio.ui.dialogs import prompts

RUSSIAN = "Привет, мир — «ёлка»"


@pytest.fixture
def doc(sample_pdf):
    d = PdfDocument.open(sample_pdf)
    yield d
    d.close()


def _span(words, first, last) -> Rect:
    a = next(w for w in words if w.text == first)
    b = next(w for w in words if w.text == last)
    return a.rect.union(b.rect)


def _texts_in(doc, page, rect):
    return [w.text for w in doc.words(page) if rect.inflated(1).contains(w.rect.center)]


# ------------------------------------------------------------------ engine
def test_cyrillic_replacement_is_written_not_question_marks(doc, tmp_path):
    rect = _span(doc.words(2), "important", "sentence")
    doc.replace_text(2, rect, RUSSIAN, doc.probe_text_style(2, rect))
    assert RUSSIAN in doc.page_text(2)
    assert "??" not in doc.page_text(2)
    out = str(tmp_path / "ru.pdf")
    doc.save(out)
    again = PdfDocument.open(out)
    assert RUSSIAN in again.page_text(2)
    again.close()


def test_latin_text_keeps_the_base14_font(doc):
    rect = _span(doc.words(2), "important", "sentence")
    size = doc.replace_text(2, rect, "café déjà vu", doc.probe_text_style(2, rect))
    assert "café déjà vu" in doc.page_text(2)
    assert size == pytest.approx(12, abs=0.01)  # a similar-length phrase is not shrunk


def test_replacement_is_a_movable_object_hugging_the_text(doc):
    rect = _span(doc.words(2), "important", "sentence")
    doc.replace_text(2, rect, "moved words", doc.probe_text_style(2, rect), object_id="t1")
    (obj,) = doc.placed_objects(2)
    assert obj.id == "t1" and obj.kind == "text"
    assert rect.inflated(3).contains(Point(obj.rect.x0, obj.rect.y0))
    assert obj.rect.width < rect.width  # cut to the text, not the whole box
    snap = doc.snapshot_page_content(2)
    target = obj.rect.translated(40, 200)
    doc.transform_object(2, "t1", target)
    assert doc.placed_objects(2)[0].rect.as_tuple() == pytest.approx(target.as_tuple(), abs=0.05)
    assert _texts_in(doc, 2, target) == ["moved", "words"]
    assert _texts_in(doc, 2, obj.rect) == []
    doc.restore_page_content(2, snap)
    assert doc.placed_objects(2)[0].rect.as_tuple() == pytest.approx(obj.rect.as_tuple(), abs=0.05)


def test_resizing_text_scales_it(doc):
    doc.replace_text(0, Rect(60, 600, 300, 640), "Scale me", TextStyle(font_size=12),
                     object_id="t")
    r = doc.placed_objects(0)[0].rect
    doc.transform_object(0, "t", Rect(r.x0, r.y0, r.x0 + r.width * 2, r.y0 + r.height * 2))
    big = doc.placed_objects(0)[0].rect
    assert doc.probe_text_style(0, big).font_size == pytest.approx(24, abs=0.3)


def test_moving_on_a_rotated_page_stays_upright(doc):
    doc.replace_text(5, Rect(500, 400, 760, 430), "Повернутый текст", TextStyle(font_size=14),
                     object_id="r")
    r = doc.placed_objects(5)[0].rect
    doc.transform_object(5, "r", r.translated(-150, 60))
    words = [w for w in doc.words(5) if w.text in ("Повернутый", "текст")]
    assert len(words) == 2
    for w in words:
        assert w.rect.width > w.rect.height
        assert r.translated(-150, 60).inflated(2).contains(w.rect.center)


def test_delete_object_removes_it_from_the_page(doc):
    doc.replace_text(0, Rect(60, 600, 300, 640), "Goodbye", TextStyle(), object_id="g")
    doc.delete_object(0, "g")
    assert doc.placed_objects(0) == []
    assert "Goodbye" not in doc.page_text(0)


def test_objects_survive_a_later_redaction_and_save(doc, tmp_path):
    doc.replace_text(0, Rect(60, 600, 300, 640), "Keep me", TextStyle(font_size=14),
                     object_id="k")
    # MuPDF rewrites the page (and renames forms) for this unrelated redaction
    marker = next(w for w in doc.words(0) if w.text == "PAGE-MARKER-1")
    doc.redact(0, marker.rect)
    assert [o.id for o in doc.placed_objects(0)] == ["k"]
    out = str(tmp_path / "kept.pdf")
    doc.save(out)
    again = PdfDocument.open(out)
    (obj,) = again.placed_objects(0)
    again.transform_object(0, "k", obj.rect.translated(0, 100))
    assert {"Keep", "me"} <= set(_texts_in(again, 0, obj.rect.translated(0, 100)))
    assert "Keep" not in _texts_in(again, 0, obj.rect)
    again.close()


def test_placed_images_replace_and_redaction(doc):
    before = len(doc.image_rects(3))
    doc.place_image(3, Rect(56, 500, 296, 640), _png(), cover=False, object_id="a")
    (obj,) = doc.placed_objects(3)
    assert obj.kind == "image"
    assert obj.rect.as_tuple() == pytest.approx(Rect(56, 510, 296, 630).as_tuple(), abs=0.5)  # 2:1, centred
    doc.place_image(3, obj.rect, _png(30, 30), cover=False, object_id="b", replaces="a")
    assert [o.id for o in doc.placed_objects(3)] == ["b"]
    assert len(doc.image_rects(3)) == before + 1
    doc.redact(3, doc.placed_objects(3)[0].rect.inflated(1))  # fully covered: removed
    assert doc.placed_objects(3) == [] and len(doc.image_rects(3)) == before


def test_duplicated_page_gets_its_own_objects(doc):
    doc.replace_text(0, Rect(60, 600, 300, 640), "Twin", TextStyle(font_size=14),
                     object_id="o")
    n = doc.duplicate_page(0)
    (copy,) = doc.placed_objects(n)
    assert copy.id != "o"
    doc.transform_object(n, copy.id, copy.rect.translated(0, -300))
    assert doc.placed_objects(0)[0].rect.y0 == pytest.approx(copy.rect.y0, abs=0.05)
    doc.delete_object(n, copy.id)
    assert [o.id for o in doc.placed_objects(0)] == ["o"]


# ---------------------------------------------------------------- commands
def test_transform_and_delete_commands_undo_redo(sample_pdf):
    s = DocumentSession.open(sample_pdf)
    pid = s.page_id(0)
    add = ReplaceTextCommand(pid, Rect(60, 600, 300, 640), "Movable", TextStyle())
    assert s.stack.push(add)
    obj = s.placed_object(pid, add.object_id)
    assert obj is not None
    assert not s.stack.push(TransformObjectCommand(pid, obj.id, obj.rect))  # no-op
    for _ in range(3):  # nudges merge into one step
        cur = s.placed_object(pid, obj.id).rect
        s.stack.push(TransformObjectCommand(pid, obj.id, cur.translated(1, 0), merge_key="n"))
    assert len(s.stack) == 2
    assert s.placed_object(pid, obj.id).rect.x0 == pytest.approx(obj.rect.x0 + 3, abs=0.05)
    s.stack.undo()
    assert s.placed_object(pid, obj.id).rect.x0 == pytest.approx(obj.rect.x0, abs=0.05)
    s.stack.redo()
    assert s.placed_object(pid, obj.id).rect.x0 == pytest.approx(obj.rect.x0 + 3, abs=0.05)
    assert s.stack.push(DeleteObjectCommand(pid, obj.id))
    assert s.placed_object(pid, obj.id) is None and "Movable" not in s.doc.page_text(0)
    s.stack.undo()
    assert s.placed_object(pid, obj.id) is not None
    # redoing the placement recreates the object under the same id
    s.stack.undo()
    s.stack.undo()
    assert s.placed_objects(0) == []
    s.stack.redo()
    assert s.placed_object(pid, add.object_id) is not None
    s.close()


def test_image_command_replaces_a_placed_image(sample_pdf):
    s = DocumentSession.open(sample_pdf)
    pid = s.page_id(0)
    first = PlaceImageCommand(pid, Rect(60, 600, 200, 680), _png(), cover=False)
    s.stack.push(first)
    second = PlaceImageCommand(pid, Rect(60, 600, 200, 680), _png(20, 20), cover=False,
                               replaces=first.object_id)
    s.stack.push(second)
    assert [o.id for o in s.placed_objects(0)] == [second.object_id]
    s.stack.undo()
    assert [o.id for o in s.placed_objects(0)] == [first.object_id]
    s.close()


# ---------------------------------------------------------------------- UI
@pytest.fixture
def view(window, sample_pdf):
    window.open_path(sample_pdf)
    window.view.go_to_page(2)
    pump(50)
    return window.view


def wp(view, page, p):
    return view.canvas.pdf_to_widget(page, p).toPoint()


def drag(view, a, b):
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, a + (b - a) / 3)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)


def _place_text(window, view, text=RUSSIAN):
    window.set_tool("edit_text")
    rect = _span(view.session.words(2), "important", "sentence")
    drag(view, wp(view, 2, Point(rect.x0 + 2, rect.center.y)),
         wp(view, 2, Point(rect.x1 - 2, rect.center.y)))
    sel = view.selected_placed_object()
    assert sel is not None
    return sel


def test_edit_text_in_russian_then_drag_it(window, view, monkeypatch):
    monkeypatch.setattr(prompts, "ask_replace_text",
                        lambda parent, original, style: (RUSSIAN, style, 0))
    # the fixture highlights this phrase: the new text still drags, not the highlight
    page, obj = _place_text(window, view)
    assert RUSSIAN in view.session.doc.page_text(2)
    assert view.tools.active_name == "select"  # ready to drag, as after signing
    assert window.actions["delete"].isEnabled()
    start = obj.rect.center
    drag(view, wp(view, 2, start), wp(view, 2, start.translated(30, 150)))
    moved = view.session.placed_object(view.session.page_id(2), obj.id).rect
    assert moved.x0 == pytest.approx(obj.rect.x0 + 30, abs=1.5)
    assert moved.y0 == pytest.approx(obj.rect.y0 + 150, abs=1.5)
    assert window.actions["undo"].text().startswith("&Undo Move text")
    window.actions["undo"].trigger()
    back = view.session.placed_object(view.session.page_id(2), obj.id).rect
    assert back.y0 == pytest.approx(obj.rect.y0, abs=0.1)


def test_keyboard_nudge_delete_and_escape(window, view, monkeypatch):
    monkeypatch.setattr(prompts, "ask_replace_text",
                        lambda parent, original, style: ("nudge me", style, 0))
    page, obj = _place_text(window, view)
    sel = view.tools.tools["select"]
    for _ in range(3):
        sel.key_press(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
                                Qt.KeyboardModifier.ShiftModifier))
    pid = view.session.page_id(2)
    assert view.session.placed_object(pid, obj.id).rect.y0 == pytest.approx(obj.rect.y0 + 30,
                                                                             abs=0.1)
    assert len(view.session.stack) == 2  # replace + one merged nudge
    view.tools.escape()
    assert view.selected_object is None
    view.select_object(2, obj.id)
    view.canvas.setFocus()
    QTest.keyClick(view.canvas, Qt.Key.Key_Delete)
    assert view.session.placed_object(pid, obj.id) is None
    assert "nudge me" not in view.session.doc.page_text(2)
    assert view.selected_object is None


def test_image_tool_places_then_resize_by_corner(window, view, tmp_path, monkeypatch):
    path = tmp_path / "pic.png"
    path.write_bytes(_png(60, 40))
    monkeypatch.setattr(prompts, "ask_open_image", lambda *a, **k: str(path))
    window.set_tool("image")
    drag(view, wp(view, 2, Point(60, 650)), wp(view, 2, Point(240, 770)))
    page, obj = view.selected_placed_object()
    assert obj.kind == "image" and view.tools.active_name == "select"
    assert window.actions["undo"].text().startswith("&Undo Place image")
    # drag the bottom-right handle: the aspect ratio is kept
    se = Point(obj.rect.x1, obj.rect.y1)
    drag(view, wp(view, 2, se), wp(view, 2, Point(se.x + 60, se.y + 10)))
    r = view.session.placed_object(view.session.page_id(2), obj.id).rect
    assert (r.x0, r.y0) == pytest.approx((obj.rect.x0, obj.rect.y0), abs=1)
    assert r.width / r.height == pytest.approx(obj.rect.width / obj.rect.height, abs=0.02)
    assert r.width > obj.rect.width + 5
    # clicking the placed image with the Image tool swaps it in place
    window.set_tool("image")
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=wp(view, 2, r.center))
    (swapped,) = view.session.placed_objects(2)
    assert swapped.id != obj.id
    assert swapped.rect.as_tuple() == pytest.approx(r.as_tuple(), abs=1)
