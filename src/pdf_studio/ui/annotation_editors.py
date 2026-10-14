"""Inline editors for annotation text: sticky-note popup and free-text box."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QKeyEvent
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from ..pdf.models import Color, Rect
from . import theme
from .qtutil import qcolor

if TYPE_CHECKING:  # pragma: no cover
    from .document_view import DocumentView

#: font used to preview free text (PDF base-14 Helvetica equivalent)
EDITOR_FONT_FAMILY = "Arial"
FREE_TEXT_PADDING_PT = 2.0


def free_text_size(text: str, font_size: float, min_width: float = 0.0,
                   max_width: Optional[float] = None) -> tuple:
    """Approximate (width, height) in points needed to show ``text``."""
    f = QFont(EDITOR_FONT_FAMILY)
    f.setPixelSize(max(1, int(round(font_size * 10))))  # measure at 10x for precision
    fm = QFontMetricsF(f)
    lines = text.split("\n") or [""]
    widest = max((fm.horizontalAdvance(line) for line in lines), default=0.0) / 10.0
    width = max(min_width, widest + 2 * FREE_TEXT_PADDING_PT + 4)
    if max_width is not None:
        width = min(width, max_width)
    # wrapped line count for the chosen width
    avail = max(10.0, width - 2 * FREE_TEXT_PADDING_PT - 2) * 10.0
    n = 0
    for line in lines:
        w = fm.horizontalAdvance(line)
        n += max(1, int(w // avail) + (1 if w % avail > 0 else 0)) if w > 0 else 1
    height = n * font_size * 1.22 + 2 * FREE_TEXT_PADDING_PT + 3
    return width, height


class FreeTextEditor(QPlainTextEdit):
    """Edits a free-text annotation in place, over the page."""

    def __init__(self, view: "DocumentView", page: int, rect: Rect, text: str,
                 font_size: float, color: Color, fill: Optional[Color],
                 on_commit: Callable[[str, Rect], None], on_cancel: Callable[[], None],
                 auto_width: bool = False):
        super().__init__(view.canvas.viewport())
        self.view = view
        self.page = page
        self.rect = rect
        self.font_size = font_size
        self.auto_width = auto_width
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._done = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setPlainText(text)
        bg = qcolor(fill) if fill else QColor(255, 255, 255, 235)
        fg = qcolor(color)
        acc = theme.current().accent
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {bg.name(QColor.NameFormat.HexArgb)}; color: {fg.name()};"
            f" border: 1.5px dashed {acc}; border-radius: 0; padding: 0; margin: 0;"
            f" selection-background-color: {acc}; }}")
        self.document().setDocumentMargin(1)
        self.textChanged.connect(self._fit)
        view.canvas.viewChanged.connect(self.reposition)
        view.canvas.layoutChanged.connect(self.reposition)
        self.reposition()
        self.show()
        self.setFocus()
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.setTextCursor(cur)

    # geometry follows page coordinates through zoom/scroll
    def reposition(self) -> None:
        if self._done:
            return
        scale = self.view.canvas.layout_.scale
        f = QFont(EDITOR_FONT_FAMILY)
        f.setPixelSize(max(4, int(round(self.font_size * scale))))
        self.setFont(f)
        self._fit(apply_only=True)

    def _fit(self, apply_only: bool = False) -> None:
        if self._done:
            return
        text = self.toPlainText()
        w, h = free_text_size(text, self.font_size,
                              min_width=60.0 if self.auto_width else self.rect.width,
                              max_width=None if self.auto_width else self.rect.width)
        if self.auto_width:
            page_w, _ = self.view.session.page_size(self.page)
            w = min(w + 6, max(60.0, page_w - self.rect.x0 - 4))
        self.rect = Rect(self.rect.x0, self.rect.y0, self.rect.x0 + max(w, 20.0),
                         self.rect.y0 + max(h, self.rect.height if not apply_only else h))
        wr = self.view.canvas.pdf_rect_to_widget(self.page, self.rect)
        if wr is not None:
            self.setGeometry(wr.toAlignedRect().adjusted(-2, -2, 2, 2))

    def final_rect(self) -> Rect:
        w, h = free_text_size(self.toPlainText(), self.font_size,
                              min_width=40.0 if self.auto_width else self.rect.width,
                              max_width=None if self.auto_width else self.rect.width)
        if self.auto_width:
            page_w, _ = self.view.session.page_size(self.page)
            w = min(w + 6, max(40.0, page_w - self.rect.x0 - 2))
        return Rect(self.rect.x0, self.rect.y0, self.rect.x0 + w, self.rect.y0 + h)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.commit() if self.toPlainText().strip() else self.cancel()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.commit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        if e.reason() != Qt.FocusReason.PopupFocusReason:
            QTimer.singleShot(0, self.commit)

    def commit(self) -> None:
        if self._done:
            return
        self._done = True
        text = self.toPlainText().rstrip()
        rect = self.final_rect()
        self._teardown()
        self._on_commit(text, rect)

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._teardown()
        self._on_cancel()

    def _teardown(self) -> None:
        try:
            self.view.canvas.viewChanged.disconnect(self.reposition)
            self.view.canvas.layoutChanged.disconnect(self.reposition)
        except TypeError:
            pass
        self.hide()
        self.deleteLater()
        self.view.canvas.setFocus()


class NotePopup(QFrame):
    """A sticky-note card anchored next to a note icon."""

    def __init__(self, view: "DocumentView", page: int, anchor: Rect, text: str,
                 author: str, on_commit: Callable[[str], None], on_cancel: Callable[[], None],
                 on_delete: Optional[Callable[[], None]] = None, title: str = "Note"):
        super().__init__(view)
        self.view = view
        self.page = page
        self.anchor = anchor
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._on_delete = on_delete
        self._done = False
        self.setObjectName("NotePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        t = theme.current()
        self.setStyleSheet(
            f"QFrame#NotePopup {{ background: {'#FFF8D6' if not t.is_dark else '#3A3626'};"
            f" border: 1px solid {'#E8D88A' if not t.is_dark else '#5A5338'}; border-radius: 10px; }}"
            f" QPlainTextEdit {{ background: transparent; border: none; padding: 0;"
            f" color: {'#2B2611' if not t.is_dark else '#F2ECD0'}; }}"
            f" QLabel {{ color: {'#6B5E21' if not t.is_dark else '#CFC59A'}; }}")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)
        self.setFixedWidth(260)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        who = QLabel(f"<b>{author or 'Note'}</b>", self)
        head.addWidget(who)
        head.addStretch(1)
        when = QLabel(datetime.now().strftime("%d %b %Y, %H:%M"), self)
        head.addWidget(when)
        lay.addLayout(head)
        self.edit = QPlainTextEdit(self)
        self.edit.setPlainText(text)
        self.edit.setPlaceholderText("Add a comment…")
        self.edit.setFixedHeight(110)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(6)
        if on_delete is not None:
            delete = QPushButton("Delete", self)
            delete.setProperty("variant", "danger")
            delete.clicked.connect(self.delete)
            row.addWidget(delete)
        row.addStretch(1)
        hint = QLabel("Ctrl+Enter", self)
        hint.setProperty("role", "faint")
        row.addWidget(hint)
        done = QPushButton("Done", self)
        done.setProperty("variant", "primary")
        done.clicked.connect(self.commit)
        row.addWidget(done)
        lay.addLayout(row)

        view.canvas.viewChanged.connect(self.reposition)
        view.canvas.layoutChanged.connect(self.reposition)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self.edit.setFocus()
        cur = self.edit.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.edit.setTextCursor(cur)

    def reposition(self) -> None:
        if self._done:
            return
        wr = self.view.canvas.pdf_rect_to_widget(self.page, self.anchor)
        if wr is None:
            return
        vp = self.view.canvas.viewport().geometry()
        x = wr.right() + 10
        if x + self.width() > vp.right() - 8:
            x = wr.left() - 10 - self.width()
        y = wr.top() - 6
        y = max(vp.top() + 8, min(y, vp.bottom() - self.height() - 8))
        x = max(vp.left() + 8, x)
        self.move(int(x), int(y))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.commit()
                return True
        return super().eventFilter(obj, event)

    def _on_focus_changed(self, _old, new) -> None:
        if self._done or new is None:
            return
        w = new
        while w is not None:
            if w is self:
                return
            w = w.parentWidget()
        # focus moved elsewhere in the app: keep what was typed
        self.commit()

    def text(self) -> str:
        return self.edit.toPlainText()

    def commit(self) -> None:
        if self._done:
            return
        self._done = True
        text = self.edit.toPlainText().rstrip()
        self._teardown()
        self._on_commit(text)

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._teardown()
        self._on_cancel()

    def delete(self) -> None:
        if self._done:
            return
        self._done = True
        self._teardown()
        if self._on_delete is not None:
            self._on_delete()

    def _teardown(self) -> None:
        try:
            QApplication.instance().focusChanged.disconnect(self._on_focus_changed)
        except TypeError:
            pass
        try:
            self.view.canvas.viewChanged.disconnect(self.reposition)
            self.view.canvas.layoutChanged.disconnect(self.reposition)
        except TypeError:
            pass
        self.hide()
        self.deleteLater()
        self.view.canvas.setFocus()
