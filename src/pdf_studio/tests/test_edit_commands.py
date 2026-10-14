"""Ticket 28: undoable redact / replace / image commands (Qt-free)."""

from __future__ import annotations

import pytest

from pdf_studio.core.edit_commands import (PlaceImageCommand, RedactCommand,
                                           ReplaceTextCommand)
from pdf_studio.core.session import DocumentSession, EventKind
from pdf_studio.pdf.models import Rect, TextStyle
from pdf_studio.tests.test_editing_engine import _png


@pytest.fixture
def s(sample_pdf):
    session = DocumentSession.open(sample_pdf)
    yield session
    session.close()


def _rect(s, page, first, last=None):
    words = s.words(page)
    a = next(w for w in words if w.text == first)
    b = next(w for w in words if w.text == (last or first))
    return a.rect.union(b.rect)


def test_replace_command_execute_undo_redo(s):
    pid = s.page_id(2)
    rect = _rect(s, 2, "important", "sentence")
    style = s.probe_text_style(2, rect)
    events = []
    s.subscribe(lambda e: events.append(e.kind))
    rev0 = s.revision(pid)
    cmd = ReplaceTextCommand(pid, rect, "vital claim", style)
    assert s.stack.push(cmd)
    assert "vital claim" in s.doc.page_text(2) and s.dirty
    assert s.revision(pid) > rev0 and EventKind.PAGES in events
    assert not cmd.shrunk
    s.stack.undo()
    assert "important sentence" in s.doc.page_text(2) and "vital" not in s.doc.page_text(2)
    assert not s.dirty
    s.stack.redo()
    assert "vital claim" in s.doc.page_text(2) and s.dirty


def test_redact_multiple_rects_one_step(s):
    pid = s.page_id(1)
    rects = [_rect(s, 1, "Chapter"), _rect(s, 1, "PAGE-MARKER-2")]
    s.stack.push(RedactCommand(pid, rects))
    text = s.doc.page_text(1)
    assert "PAGE-MARKER-2" not in text and "Chapter" not in text
    assert len(s.stack) == 1
    s.stack.undo()
    assert "PAGE-MARKER-2" in s.doc.page_text(1)


def test_image_command(s):
    pid = s.page_id(0)
    before = len(s.image_rects(0))
    s.stack.push(PlaceImageCommand(pid, Rect(60, 600, 200, 680), _png()))
    assert len(s.image_rects(0)) == before + 1
    s.stack.undo()
    assert len(s.image_rects(0)) == before


def test_failed_edit_leaves_page_and_stack_untouched(s):
    pid = s.page_id(0)
    text = s.doc.page_text(0)
    with pytest.raises(Exception):
        s.stack.push(PlaceImageCommand(pid, Rect(60, 600, 200, 680), b"junk"))
    assert s.doc.page_text(0) == text and len(s.stack) == 0 and not s.dirty


def test_edits_survive_page_moves(s):
    """Commands track pages by id: undo works after the page moved."""
    from pdf_studio.core.page_commands import MovePagesCommand
    pid = s.page_id(2)
    rect = _rect(s, 2, "important", "sentence")
    s.stack.push(ReplaceTextCommand(pid, rect, "moved edit", TextStyle(font_size=12)))
    s.stack.push(MovePagesCommand([pid], 12))
    s.stack.undo()  # move
    s.stack.undo()  # edit
    assert "important sentence" in s.doc.page_text(2)


def test_commands_do_not_import_qt():
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "core" / "edit_commands.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m and m.startswith("PyQt") for m in mods)
