"""Tickets 20/26: page operations — engine facade and session mirror."""

from __future__ import annotations

import re

import pytest

from pdf_studio.core.session import DocumentSession
from pdf_studio.pdf import PageOperationError, PdfDocument
from pdf_studio.pdf.page_operations import move_order

MARK = re.compile(r"PAGE-MARKER-(\d+)")


def markers(doc) -> list:
    """Distinctive per-page content, in page order (0 = blank page)."""
    out = []
    for i in range(doc.page_count):
        m = MARK.search(doc.page_text(i))
        out.append(int(m.group(1)) if m else 0)
    return out


@pytest.fixture
def doc(sample_pdf):
    d = PdfDocument.open(sample_pdf)
    yield d
    d.close()


def test_rotate(doc):
    prev = doc.rotate_pages([0, 5], 90)
    assert prev == {0: 0, 5: 90}
    assert doc.page_info(0).rotation == 90 and doc.page_info(5).rotation == 180
    assert (round(doc.page_info(0).width), round(doc.page_info(0).height)) == (842, 595)
    doc.rotate_pages([0], -90)
    assert doc.page_info(0).rotation == 0
    doc.set_rotations({5: 90})
    assert doc.page_info(5).rotation == 90
    with pytest.raises(PageOperationError):
        doc.rotate_pages([0], 45)


def test_delete_and_export(doc):
    data = doc.export_pages([1, 3])
    n = doc.delete_pages([1, 3])
    assert n == 10
    assert markers(doc)[:4] == [1, 3, 5, 6]
    exported = PdfDocument.from_bytes(data)
    assert markers(exported) == [2, 4]
    exported.close()
    with pytest.raises(PageOperationError):
        doc.delete_pages(list(range(doc.page_count)))  # can't delete every page
    with pytest.raises(PageOperationError):
        doc.delete_pages([99])


def test_duplicate_keeps_annotations(doc):
    new_index = doc.duplicate_page(2)
    assert new_index == 3
    assert markers(doc)[:5] == [1, 2, 3, 3, 4]
    assert len(doc.annotations(3)) == 1  # the highlight was copied too
    last = doc.duplicate_page(doc.page_count - 1)
    assert last == doc.page_count - 1 and markers(doc)[-1] == 12


def test_move_and_reorder(doc):
    order = doc.move_pages([0], 3)
    assert order[:4] == [1, 2, 0, 3]
    assert markers(doc)[:4] == [2, 3, 1, 4]
    doc.move_pages([11], 0)
    assert markers(doc)[0] == 12
    doc.reorder_pages(list(reversed(range(doc.page_count))))
    assert markers(doc)[-1] == 12
    with pytest.raises(PageOperationError):
        doc.reorder_pages([0, 0, 1])


def test_move_order_helper():
    assert move_order(5, [0], 3) == [1, 2, 0, 3, 4]
    assert move_order(5, [3], 0) == [3, 0, 1, 2, 4]
    assert move_order(5, [1, 3], 5) == [0, 2, 4, 1, 3]
    assert move_order(5, [4], 5) == [0, 1, 2, 3, 4]


def test_insert_blank_and_pdf(doc, fixture_paths):
    doc.insert_blank_page(1, 612, 792)
    assert markers(doc)[:3] == [1, 0, 2]
    assert (round(doc.page_info(1).width), round(doc.page_info(1).height)) == (612, 792)
    with open(fixture_paths["plain.pdf"], "rb") as f:
        data = f.read()
    n = doc.insert_pdf(data, doc.page_count)  # append
    assert n == 2 and doc.page_count == 15
    n = doc.insert_pdf(data, 0, pages=[1])
    assert n == 1 and "Plain page 2" in doc.page_text(0)
    with pytest.raises(PageOperationError):
        doc.insert_pdf(b"garbage", 0)
    with pytest.raises(PageOperationError):
        doc.insert_pdf(data, 0, pages=[7])


def test_insert_from_encrypted_requires_password(doc, fixture_paths):
    with open(fixture_paths["encrypted.pdf"], "rb") as f:
        data = f.read()
    with pytest.raises(PageOperationError):
        doc.insert_pdf(data, 0)
    assert doc.insert_pdf(data, 0, password="secret") == 3


# ------------------------------------------------------------ session mirror
@pytest.fixture
def session(sample_pdf):
    s = DocumentSession.open(sample_pdf)
    yield s
    s.close()


def test_session_ids_follow_pages(session):
    ids = list(session.page_ids)
    session.move_pages([ids[0]], 3)
    assert session.page_ids[:3] == [ids[1], ids[2], ids[0]]
    assert markers(session.doc)[:3] == [2, 3, 1]
    prev = session.rotate_pages([ids[0]], 90)
    assert prev == {ids[0]: 0}
    assert session.page_size(session.index_of(ids[0]))[0] == pytest.approx(842, abs=1)
    copies = session.duplicate_pages([ids[4]])
    assert session.index_of(copies[0]) == session.index_of(ids[4]) + 1
    deleted = session.delete_pages([ids[1], ids[6]])
    assert session.page_count == 11 and ids[1] not in session.page_ids
    session.restore_pages(deleted)
    assert session.page_count == 12 + 1 - 0  # the copy is still there
    assert session.index_of(ids[1]) is not None
    blank = session.insert_blank_page(0, (595, 842))
    assert session.page_ids[0] == blank
    assert len(session.page_sizes()) == session.page_count == session.doc.page_count


def test_session_reorder_does_not_bump_revisions(session):
    """Thumbnails/tiles are keyed by (page id, revision): a move must not
    invalidate them (ticket 23)."""
    revs = {pid: session.revision(pid) for pid in session.page_ids}
    session.move_pages([session.page_ids[0]], 5)
    assert {pid: session.revision(pid) for pid in session.page_ids} == revs


def test_session_restores_outline_after_delete_undo(session):
    toc_before = [(e.title, e.page_index) for e in session.outline()]
    deleted = session.delete_pages([session.page_ids[9]])  # "Images" bookmark target
    session.restore_pages(deleted)
    assert [(e.title, e.page_index) for e in session.outline()] == toc_before
