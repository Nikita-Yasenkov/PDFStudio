"""Tickets 21/26: undoable page commands (Qt-free)."""

from __future__ import annotations

import pytest

from pdf_studio.core.page_commands import (DeletePagesCommand, DuplicatePagesCommand,
                                           InsertBlankPageCommand, InsertPdfCommand,
                                           MovePagesCommand, RotatePagesCommand,
                                           SetPageOrderCommand)
from pdf_studio.core.session import DocumentSession
from pdf_studio.tests.test_page_ops_engine import markers


@pytest.fixture
def s(sample_pdf):
    session = DocumentSession.open(sample_pdf)
    yield session
    session.close()


def snapshot(s):
    return (markers(s.doc), [s.page_info(i).rotation for i in range(s.page_count)],
            list(s.page_ids))


def roundtrip(s, cmd):
    before = snapshot(s)
    assert s.stack.push(cmd)
    after = snapshot(s)
    assert after != before
    assert s.dirty
    s.stack.undo()
    assert snapshot(s) == before
    assert not s.dirty
    s.stack.redo()
    assert snapshot(s) == after
    return after


def test_rotate(s):
    after = roundtrip(s, RotatePagesCommand([s.page_ids[0], s.page_ids[2]], 90))
    assert after[1][0] == 90 and after[1][2] == 90


def test_delete(s):
    ids = s.page_ids
    after = roundtrip(s, DeletePagesCommand([ids[1], ids[4]]))
    assert after[0][:4] == [1, 3, 4, 6]
    # annotations on a deleted+restored page survive
    s.stack.undo()
    assert len(s.annotations(2)) == 1


def test_delete_last_remaining_page_fails_cleanly(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    try:
        before = snapshot(s)
        with pytest.raises(Exception):
            s.stack.push(DeletePagesCommand(list(s.page_ids)))
        assert snapshot(s) == before and len(s.stack) == 0 and not s.dirty
    finally:
        s.close()


def test_duplicate(s):
    cmd = DuplicatePagesCommand([s.page_ids[0], s.page_ids[3]])
    after = roundtrip(s, cmd)
    assert after[0][:6] == [1, 1, 2, 3, 4, 4]
    assert cmd.new_ids and all(pid in s.page_ids for pid in cmd.new_ids)


def test_move(s):
    after = roundtrip(s, MovePagesCommand([s.page_ids[0]], 3))
    assert after[0][:3] == [2, 3, 1]


def test_noop_move_is_not_recorded(s):
    assert not s.stack.push(MovePagesCommand([s.page_ids[2]], 2))
    assert len(s.stack) == 0 and not s.dirty


def test_explicit_order(s):
    order = list(reversed(s.page_ids))
    after = roundtrip(s, SetPageOrderCommand(order))
    assert after[0][0] == 12


def test_insert_blank(s):
    cmd = InsertBlankPageCommand(2, (595, 842))
    after = roundtrip(s, cmd)
    assert after[0][:4] == [1, 2, 0, 3]
    assert cmd.page_id == s.page_ids[2]  # redo keeps the same id


def test_insert_pdf(s, fixture_paths):
    with open(fixture_paths["plain.pdf"], "rb") as f:
        data = f.read()
    cmd = InsertPdfCommand(data, s.page_count, pages=[1, 0])
    roundtrip(s, cmd)
    assert s.page_count == 14
    assert "Plain page 2" in s.doc.page_text(12) and "Plain page 1" in s.doc.page_text(13)


def test_mixed_sequence_undoes_completely(s):
    start = snapshot(s)
    ids = list(s.page_ids)
    for cmd in (RotatePagesCommand([ids[0]], -90), MovePagesCommand([ids[5]], 0),
                DuplicatePagesCommand([ids[2]]), DeletePagesCommand([ids[7], ids[8]]),
                InsertBlankPageCommand(1, (300, 300))):
        assert s.stack.push(cmd)
    assert s.page_count == 12 + 1 - 2 + 1
    while s.stack.can_undo:
        s.stack.undo()
    assert snapshot(s) == start
    while s.stack.can_redo:
        s.stack.redo()
    assert s.page_count == 12
