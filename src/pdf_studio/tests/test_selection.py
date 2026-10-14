"""Ticket 13: word-based text selection (core + UI)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtTest import QTest

from pdf_studio.core.selection import (select_all, select_between, select_in_rect,
                                       select_line, select_word, word_at)
from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf.models import Point, Rect, Word
from pdf_studio.tests.conftest import pump


def W(text, x0, y0, x1, y1, block=0, line=0, word=0):
    return Word(Rect(x0, y0, x1, y1), text, block, line, word)


WORDS = [
    W("Hello", 10, 10, 40, 20, 0, 0, 0), W("big", 45, 10, 60, 20, 0, 0, 1),
    W("world", 65, 10, 95, 20, 0, 0, 2),
    W("second", 10, 25, 50, 35, 0, 1, 0), W("line", 55, 25, 75, 35, 0, 1, 1),
    W("Para", 10, 50, 35, 60, 1, 0, 0),
]


def test_drag_selects_words_in_reading_order():
    sel = select_between(0, WORDS, Point(46, 15), Point(60, 30))
    assert [w.text for w in sel.words] == ["big", "world", "second", "line"]
    assert sel.text == "big world\nsecond line"
    assert len(sel.line_rects) == 2
    assert sel.line_rects[0] == Rect(45, 10, 95, 20)


def test_backwards_drag_and_past_line_end():
    sel = select_between(0, WORDS, Point(70, 30), Point(0, 12))
    assert sel.words[0].text == "Hello" and sel.words[-1].text == "line"
    # dragging far to the right of a line ends at its last word
    sel = select_between(0, WORDS, Point(12, 15), Point(300, 15))
    assert sel.text == "Hello big world"


def test_word_line_all_and_area():
    assert select_word(0, WORDS, Point(50, 15)).text == "big"
    assert select_line(0, WORDS, Point(12, 30)).text == "second line"
    assert select_all(0, WORDS).text.endswith("Para")
    assert select_in_rect(0, WORDS, Rect(0, 22, 100, 40)).text == "second line"
    assert word_at(WORDS, Point(500, 500)) is None


def test_selection_on_real_page(fixture_paths):
    s = DocumentSession.open(fixture_paths["sample.pdf"])
    try:
        words = s.words(2)
        target = next(w for w in words if w.text == "important")
        end = next(w for w in words if w.text == "sentence")
        sel = select_between(2, words, target.rect.center, end.rect.center)
        assert sel.text == "important sentence"
    finally:
        s.close()


# ---------------------------------------------------------------------- UI
def _word_widget_pos(view, page, text):
    w = next(w for w in view.session.words(page) if w.text == text)
    return view.canvas.pdf_to_widget(page, w.rect.center).toPoint(), w


def test_drag_selection_and_copy(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(2)
    pump(50)
    a, _ = _word_widget_pos(v, 2, "important")
    b, _ = _word_widget_pos(v, 2, "sentence")
    vp = v.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    assert v.text_selection is not None
    assert v.selected_text() == "important sentence"
    assert window.actions["copy"].isEnabled()
    window.actions["copy"].trigger()
    assert QGuiApplication.clipboard().text() == "important sentence"


def test_selection_stays_aligned_after_zoom(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(2)
    pos, word = _word_widget_pos(v, 2, "important")
    v.set_text_selection(select_word(2, v.session.words(2), word.rect.center))
    v.set_zoom(2.0)
    r = v.canvas.pdf_rect_to_widget(2, v.text_selection.line_rects[0])
    expected = v.canvas.pdf_rect_to_widget(2, word.rect)
    assert r.center().x() == pytest.approx(expected.center().x(), abs=0.5)
    assert r.width() == pytest.approx(word.rect.width * v.canvas.layout_.scale, abs=0.5)


def test_double_click_selects_word_and_escape_clears(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.go_to_page(2)
    pos, _ = _word_widget_pos(v, 2, "Highlight")  # not covered by an annotation
    QTest.mouseDClick(v.canvas.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert v.selected_text() == "Highlight"
    v.canvas.setFocus()
    QTest.keyClick(v.canvas, Qt.Key.Key_Escape)
    assert v.text_selection is None


def test_click_on_blank_area_clears_selection(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.select_all_text()
    assert v.text_selection is not None
    rect = v.canvas.page_rect_widget(v.current_page)
    QTest.mouseClick(v.canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(int(rect.left() + 5), int(rect.top() + 5)))
    assert v.text_selection is None


def test_switching_to_hand_tool_clears_selection(window, sample_pdf):
    window.open_path(sample_pdf)
    v = window.view
    v.select_all_text()
    window.set_tool("hand")
    assert v.text_selection is None
