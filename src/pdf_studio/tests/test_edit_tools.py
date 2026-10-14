"""Ticket 29: Edit Text (redact & replace) and Redact tools."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.core.selection import select_between
from pdf_studio.pdf.models import Point
from pdf_studio.tests.conftest import pump
from pdf_studio.ui.dialogs import prompts


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
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)


def _words(view, page, first, last):
    words = view.session.words(page)
    return (next(w for w in words if w.text == first), next(w for w in words if w.text == last))


def test_edit_text_by_dragging_over_words(window, view, monkeypatch):
    seen = {}

    def fake(parent, original, style):
        seen["original"] = original
        seen["style"] = style
        return "key finding", style, 0

    monkeypatch.setattr(prompts, "ask_replace_text", fake)
    window.set_tool("edit_text")
    a, b = _words(view, 2, "important", "sentence")
    rev = view.session.render_key(2)[1]
    drag(view, wp(view, 2, a.rect.center), wp(view, 2, b.rect.center))
    assert seen["original"] == "important sentence"
    assert seen["style"].font_size == pytest.approx(12, abs=0.5)
    text = view.session.doc.page_text(2)
    assert "key finding" in text and "important sentence" not in text
    assert view.session.dirty
    assert view.session.render_key(2)[1] > rev  # page raster refreshes (content edit)
    assert window.actions["undo"].text().startswith("&Undo Replace text")
    window.actions["undo"].trigger()
    assert "important sentence" in view.session.doc.page_text(2)
    assert not view.session.dirty


def test_edit_text_cancel_does_nothing(window, view, monkeypatch):
    monkeypatch.setattr(prompts, "ask_replace_text", lambda *a: None)
    window.set_tool("edit_text")
    a, b = _words(view, 2, "important", "sentence")
    drag(view, wp(view, 2, a.rect.center), wp(view, 2, b.rect.center))
    assert not view.session.dirty and len(view.session.stack) == 0


def test_edit_text_in_a_drawn_box(window, view, monkeypatch):
    monkeypatch.setattr(prompts, "ask_replace_text",
                        lambda parent, original, style: ("Added label", style, 1))
    window.set_tool("edit_text")
    drag(view, wp(view, 2, Point(60, 700)), wp(view, 2, Point(260, 730)))
    assert "Added label" in view.session.doc.page_text(2)


def test_existing_selection_then_edit_tool_is_one_shot(window, view, monkeypatch):
    monkeypatch.setattr(prompts, "ask_replace_text",
                        lambda parent, original, style: (original.upper(), style, 0))
    words = view.session.words(2)
    a, b = _words(view, 2, "important", "sentence")
    view.set_text_selection(select_between(2, words, a.rect.center, b.rect.center))
    window.set_tool("edit_text")
    assert "IMPORTANT SENTENCE" in view.session.doc.page_text(2)
    assert view.tools.active_name == "select"


def test_redact_selection_and_area(window, view):
    window.set_tool("redact")
    a, b = _words(view, 2, "important", "sentence")
    drag(view, wp(view, 2, a.rect.center), wp(view, 2, b.rect.center))
    assert "important" not in view.session.doc.page_text(2)
    assert "Redacted" in view.toast_widget.label.text()
    marker = next(w for w in view.session.words(2) if w.text == "PAGE-MARKER-3")
    drag(view, wp(view, 2, Point(marker.rect.x0 - 2, marker.rect.y0 - 2)),
         wp(view, 2, Point(marker.rect.x1 + 2, marker.rect.y1 + 2)))
    assert "PAGE-MARKER-3" not in view.session.doc.page_text(2)
    assert len(view.session.stack) == 2
    window.actions["undo"].trigger()
    window.actions["undo"].trigger()
    assert "PAGE-MARKER-3" in view.session.doc.page_text(2)
    assert "important" in view.session.doc.page_text(2)


def test_content_tools_are_exclusive(window, view):
    window.set_tool("redact")
    assert view.tools.active_name == "redact"
    window.set_tool("edit_text")
    assert view.tools.active_name == "edit_text"
    assert [a for a in window.tool_group.actions() if a.isChecked()] == [window.actions["tool_edit_text"]]


def test_tiny_area_is_ignored(window, view):
    window.set_tool("redact")
    drag(view, wp(view, 2, Point(60, 700)), wp(view, 2, Point(61, 701)))
    assert len(view.session.stack) == 0


def test_replace_dialog_values(window, sample_pdf):
    from pdf_studio.pdf.models import TextStyle
    from pdf_studio.ui.dialogs.replace_text import ReplaceTextDialog
    dlg = ReplaceTextDialog(window, "old", TextStyle(font_size=14, font_name="tibo",
                                                     color=(1, 0, 0)))
    dlg.edit.setPlainText("new words")
    text, style, align = dlg.values()
    assert text == "new words" and style.font_name == "tibo" and style.font_size == 14
    dlg.bold_btn.setChecked(False)
    dlg.family.setCurrentIndex(dlg.family.findData("mono"))
    assert dlg.values()[1].font_name == "cour"
    dlg.close()


# ---------------------------------------------------------------- ticket 31
def _png_file(tmp_path, color=(20, 120, 40)):
    from pdf_studio.tests.test_editing_engine import _png
    p = tmp_path / "pic.png"
    p.write_bytes(_png(60, 40, color))
    return str(p)


def test_image_tool_places_image_in_dragged_box(window, view, tmp_path, monkeypatch):
    path = _png_file(tmp_path)
    monkeypatch.setattr(prompts, "ask_open_image", lambda *a, **k: path)
    window.set_tool("image")
    before = len(view.session.image_rects(2))
    rev = view.session.render_key(2)[1]
    drag(view, wp(view, 2, Point(60, 650)), wp(view, 2, Point(240, 770)))
    assert len(view.session.image_rects(2)) == before + 1
    assert view.session.dirty and view.session.render_key(2)[1] > rev
    window.actions["undo"].trigger()
    assert len(view.session.image_rects(2)) == before and not view.session.dirty


def test_image_tool_click_replaces_existing_image(window, sample_pdf, tmp_path, monkeypatch):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(3)
    pump(50)
    path = _png_file(tmp_path, (200, 0, 0))
    monkeypatch.setattr(prompts, "ask_open_image", lambda *a, **k: path)
    window.set_tool("image")
    old = v.session.image_rects(3)[0].rect
    QTest.mouseClick(v.canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=wp(v, 3, old.center))
    imgs = v.session.image_rects(3)
    assert len(imgs) == 2  # original (covered) + replacement on top
    new = imgs[-1].rect
    assert old.inflated(1).contains(new.center)
    assert new.width == pytest.approx(old.width, abs=1) or new.height == pytest.approx(old.height, abs=1)


def test_image_tool_cancel_and_bad_file(window, view, tmp_path, monkeypatch):
    window.set_tool("image")
    monkeypatch.setattr(prompts, "ask_open_image", lambda *a, **k: None)
    drag(view, wp(view, 2, Point(60, 650)), wp(view, 2, Point(240, 770)))
    assert len(view.session.stack) == 0
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    monkeypatch.setattr(prompts, "ask_open_image", lambda *a, **k: str(bad))
    drag(view, wp(view, 2, Point(60, 650)), wp(view, 2, Point(240, 770)))
    assert len(view.session.stack) == 0 and window.errors
