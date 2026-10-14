"""Undo/redo command infrastructure (Qt-free).

A :class:`Command` mutates a :class:`DocumentSession` in :meth:`execute` and
reverses it in :meth:`undo`. :class:`CommandStack` keeps one linear history
per session plus a *clean index* (the position matching the file on disk),
from which the dirty flag is derived.

Commands run under the engine lock so background renders never observe a
half-applied change. If ``execute`` raises, the command is not pushed and
neither the history nor the dirty flag changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ..pdf import ENGINE_LOCK

if TYPE_CHECKING:  # pragma: no cover
    from .session import DocumentSession


class Command:
    """Base class. Subclasses implement :meth:`execute` and :meth:`undo`."""

    label: str = "Edit"

    def execute(self, session: "DocumentSession") -> None:
        raise NotImplementedError

    def undo(self, session: "DocumentSession") -> None:
        raise NotImplementedError

    def redo(self, session: "DocumentSession") -> None:
        self.execute(session)

    def merge_with(self, other: "Command") -> bool:
        """Absorb ``other`` (executed right after self). Return True if merged."""
        return False

    @property
    def is_noop(self) -> bool:
        """True when executing changed nothing (not pushed)."""
        return False


class CompositeCommand(Command):
    """Runs several commands as one undo step."""

    def __init__(self, label: str, commands: List[Command]):
        self.label = label
        self.commands = list(commands)
        self._done: List[Command] = []

    def execute(self, session):
        self._done = []
        try:
            for cmd in self.commands:
                cmd.execute(session)
                self._done.append(cmd)
        except Exception:
            for cmd in reversed(self._done):
                cmd.undo(session)
            self._done = []
            raise

    def undo(self, session):
        for cmd in reversed(self.commands):
            cmd.undo(session)

    def redo(self, session):
        for cmd in self.commands:
            cmd.redo(session)

    @property
    def is_noop(self) -> bool:
        return all(c.is_noop for c in self.commands)


class CommandStack:
    def __init__(self, session: "DocumentSession", limit: int = 500):
        self._session = session
        self._undo: List[Command] = []
        self._redo: List[Command] = []
        self._limit = limit
        # Number of undo entries that matches the saved file; None = unreachable.
        self._clean: Optional[int] = 0

    # ------------------------------------------------------------- queries
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    @property
    def index(self) -> int:
        return len(self._undo)

    @property
    def is_clean(self) -> bool:
        return self._clean == len(self._undo)

    def __len__(self) -> int:
        return len(self._undo)

    # ------------------------------------------------------------ mutation
    def push(self, cmd: Command, merge: bool = True) -> bool:
        """Execute ``cmd`` and record it. Returns False for failure/no-op."""
        with ENGINE_LOCK:
            cmd.execute(self._session)  # may raise: nothing recorded
        if cmd.is_noop:
            return False
        if self._redo:
            # The redo branch is discarded; if the saved state lived there it
            # can no longer be reached by undo/redo.
            if self._clean is not None and self._clean > len(self._undo):
                self._clean = None
            self._redo.clear()
        merged = (merge and bool(self._undo) and self._clean != len(self._undo)
                  and self._undo[-1].merge_with(cmd))
        if not merged:
            self._undo.append(cmd)
            if len(self._undo) > self._limit:
                self._undo.pop(0)
                if self._clean is not None:
                    self._clean = self._clean - 1 if self._clean > 0 else None
        self._session.history_changed()
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        cmd = self._undo[-1]
        with ENGINE_LOCK:
            cmd.undo(self._session)
        self._undo.pop()
        self._redo.append(cmd)
        self._session.history_changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        cmd = self._redo[-1]
        with ENGINE_LOCK:
            cmd.redo(self._session)
        self._redo.pop()
        self._undo.append(cmd)
        self._session.history_changed()
        return True

    def mark_unsaved(self) -> None:
        """For new documents: dirty until saved, even with an empty history."""
        self._clean = None
        self._session.history_changed()

    def set_clean(self) -> None:
        self._clean = len(self._undo)
        self._session.history_changed()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._clean = 0
        self._session.history_changed()
