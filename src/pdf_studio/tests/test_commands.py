"""Tickets 11/19: command stack, dirty flag, UI wiring."""

from __future__ import annotations

import pytest

from pdf_studio.core.commands import Command, CommandStack, CompositeCommand
from pdf_studio.core.session import DocumentSession, EventKind


class Counter(Command):
    """Adds ``n`` to session.counter."""

    def __init__(self, n=1, label="Add", mergeable=False, fail=False, noop=False):
        self.n, self.label, self.mergeable, self.fail, self.noop = n, label, mergeable, fail, noop

    def execute(self, s):
        if self.fail:
            raise RuntimeError("boom")
        s.counter = getattr(s, "counter", 0) + (0 if self.noop else self.n)

    def undo(self, s):
        s.counter -= self.n

    def merge_with(self, other):
        if self.mergeable and isinstance(other, Counter) and other.mergeable:
            self.n += other.n
            return True
        return False

    @property
    def is_noop(self):
        return self.noop


@pytest.fixture
def session(fixture_paths):
    s = DocumentSession.open(fixture_paths["plain.pdf"])
    s.counter = 0
    yield s
    s.close()


def test_execute_undo_redo(session):
    st = session.stack
    assert st.push(Counter(2))
    assert st.push(Counter(3, label="Add three"))
    assert session.counter == 5 and st.undo_label == "Add three"
    st.undo()
    assert session.counter == 2 and st.can_redo and st.redo_label == "Add three"
    st.redo()
    assert session.counter == 5 and not st.can_redo
    st.undo(); st.undo()
    assert session.counter == 0 and not st.can_undo
    assert not st.undo()


def test_dirty_tracks_clean_index(session):
    st = session.stack
    events = []
    session.subscribe(lambda e: events.append(e.kind))
    assert not session.dirty
    st.push(Counter())
    assert session.dirty and EventKind.DIRTY in events
    st.undo()
    assert not session.dirty  # back to the saved state
    st.redo()
    st.set_clean()  # e.g. after Save
    assert not session.dirty
    st.undo()
    assert session.dirty
    st.redo()
    assert not session.dirty


def test_clean_state_unreachable_after_branching(session):
    st = session.stack
    st.push(Counter(1))
    st.set_clean()
    st.undo()
    st.push(Counter(5))  # discards the redo branch containing the saved state
    assert session.dirty
    st.undo()
    assert session.dirty  # never clean again until saved


def test_failed_command_changes_nothing(session):
    st = session.stack
    st.push(Counter(1))
    st.set_clean()
    with pytest.raises(RuntimeError):
        st.push(Counter(fail=True))
    assert len(st) == 1 and not session.dirty and session.counter == 1


def test_noop_command_is_not_recorded(session):
    st = session.stack
    assert not st.push(Counter(noop=True))
    assert len(st) == 0 and not session.dirty


def test_merge_consecutive_edits(session):
    st = session.stack
    st.push(Counter(1, mergeable=True))
    st.push(Counter(2, mergeable=True))
    assert len(st) == 1 and session.counter == 3
    st.undo()
    assert session.counter == 0


def test_no_merge_across_save_point(session):
    st = session.stack
    st.push(Counter(1, mergeable=True))
    st.set_clean()
    st.push(Counter(2, mergeable=True))
    assert len(st) == 2
    st.undo()
    assert not session.dirty


def test_composite_rolls_back_on_failure(session):
    comp = CompositeCommand("Batch", [Counter(1), Counter(2), Counter(fail=True)])
    with pytest.raises(RuntimeError):
        session.stack.push(comp)
    assert session.counter == 0 and len(session.stack) == 0


def test_history_limit_keeps_clean_index_consistent(session):
    st = CommandStack(session, limit=3)
    session.stack = st
    st.push(Counter())
    st.set_clean()
    for _ in range(5):
        st.push(Counter())
    assert len(st) == 3
    for _ in range(3):
        st.undo()
    assert session.dirty  # the saved state fell off the history


# ------------------------------------------------------------------ UI wiring
def test_undo_redo_actions_and_dirty_title(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    v.session.counter = 0
    assert not window.actions["undo"].isEnabled()
    assert v.execute(Counter(1, label="Add note"))
    assert window.actions["undo"].isEnabled()
    assert "Add note" in window.actions["undo"].text()
    assert window.tabs.tabText(window.tabs.currentIndex()).endswith(" *")
    assert window.windowTitle().startswith("plain.pdf *")
    window.actions["undo"].trigger()
    assert v.session.counter == 0
    assert not window.tabs.tabText(window.tabs.currentIndex()).endswith("*")
    assert window.actions["redo"].isEnabled()
    window.actions["redo"].trigger()
    assert v.session.counter == 1


def test_each_tab_has_its_own_history(window, plain_pdf, sample_pdf):
    window.open_path(plain_pdf)
    window.open_path(sample_pdf)
    a, b = window.views()
    a.session.counter = 0
    b.session.counter = 0
    window.activate_view(a)
    a.execute(Counter())
    window.activate_view(b)
    assert not window.actions["undo"].isEnabled()
    assert not b.session.dirty and a.session.dirty


def test_failed_command_reports_error(window, plain_pdf):
    window.open_path(plain_pdf)
    v = window.view
    v.session.counter = 0
    assert not v.execute(Counter(fail=True))
    assert window.errors
    assert not v.session.dirty
