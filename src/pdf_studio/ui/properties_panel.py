"""Floating properties inspector.

Shows the selected annotation's properties (colour, fill, stroke width,
opacity, font size, comment) and edits them through undoable commands. When
no annotation is selected but an annotation tool is active, it edits that
tool's defaults instead. It floats over the top-right corner of the document
area, so showing it never reflows the pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDoubleSpinBox, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QSlider, QToolButton, QVBoxLayout, QWidget)

from ..core.annotation_commands import UpdateAnnotationCommand
from ..pdf.models import AnnotationKind as K
from ..pdf.models import AnnotationModel, Rect
from .icons import icons
from .qtutil import color_from_q, label, qcolor
from .tool_defaults import to_hex
from .widgets import ColorRow

if TYPE_CHECKING:  # pragma: no cover
    from .document_view import DocumentView

TOOL_KINDS = {
    "highlight": K.HIGHLIGHT, "underline": K.UNDERLINE, "strikeout": K.STRIKEOUT,
    "note": K.NOTE, "free_text": K.FREE_TEXT, "ink": K.INK, "rectangle": K.RECTANGLE,
    "ellipse": K.ELLIPSE, "line": K.LINE, "arrow": K.ARROW,
}
KIND_ICONS = {
    K.HIGHLIGHT: "highlight", K.UNDERLINE: "underline", K.STRIKEOUT: "strikeout",
    K.SQUIGGLY: "underline", K.NOTE: "note", K.FREE_TEXT: "free-text", K.INK: "ink",
    K.RECTANGLE: "rectangle", K.ELLIPSE: "ellipse", K.LINE: "line", K.ARROW: "arrow",
    K.STAMP: "stamp", K.OTHER: "info",
}


class _Section(QWidget):
    def __init__(self, title: str, body: QWidget, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(6)
        self.title = label(title.upper(), "section")
        lay.addWidget(self.title)
        lay.addWidget(body)


class _Slider(QWidget):
    """Slider + value label."""

    def __init__(self, lo: int, hi: int, fmt: Callable[[int], str], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(lo, hi)
        self.value_label = QLabel(self)
        self.value_label.setProperty("role", "muted")
        self.value_label.setMinimumWidth(42)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fmt = fmt
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(fmt(v)))
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)

    def set_value(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.value_label.setText(self._fmt(v))


class PropertiesPanel(QFrame):
    WIDTH = 264

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.setGraphicsEffect(shadow)
        self.view: Optional["DocumentView"] = None
        self.tool_name = ""
        self._mode = ""             # "annotation" | "tool" | ""
        self._page = -1
        self._annot_id = ""
        self._kind: Optional[K] = None
        self._dismissed = False
        self.defaults = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 14)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.kind_icon = QLabel(self)
        head.addWidget(self.kind_icon)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        self.title = label("", "heading")
        self.subtitle = label("", "muted")
        tcol.addWidget(self.title)
        tcol.addWidget(self.subtitle)
        head.addLayout(tcol, 1)
        self.close_btn = QToolButton(self)
        self.close_btn.setIcon(icons.icon("close"))
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setAutoRaise(True)
        self.close_btn.setToolTip("Hide properties (shown again on the next selection)")
        self.close_btn.clicked.connect(self.dismiss)
        head.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addSpacing(4)

        self.color_row = ColorRow(parent=self)
        self.color_row.colorChosen.connect(lambda c: self._apply("color", color_from_q(c)))
        self.color_sec = _Section("Color", self.color_row, self)
        lay.addWidget(self.color_sec)

        self.fill_row = ColorRow(parent=self, allow_none=True)
        self.fill_row.colorChosen.connect(
            lambda c: self._apply("fill", color_from_q(c) if c.isValid() else None))
        self.fill_sec = _Section("Fill", self.fill_row, self)
        lay.addWidget(self.fill_sec)

        self.text_color_row = ColorRow(parent=self)
        self.text_color_row.colorChosen.connect(lambda c: self._apply("text_color", color_from_q(c)))
        self.text_color_sec = _Section("Text color", self.text_color_row, self)
        lay.addWidget(self.text_color_sec)

        self.font_size = QDoubleSpinBox(self)
        self.font_size.setRange(4, 96)
        self.font_size.setDecimals(0)
        self.font_size.setSuffix(" pt")
        self.font_size.valueChanged.connect(lambda v: self._apply("font_size", float(v), "font_size"))
        self.font_sec = _Section("Font size", self.font_size, self)
        lay.addWidget(self.font_sec)

        self.width_slider = _Slider(1, 24, lambda v: f"{v / 2:g} pt", self)
        self.width_slider.slider.valueChanged.connect(
            lambda v: self._apply("width", v / 2.0, "width"))
        self.width_sec = _Section("Stroke width", self.width_slider, self)
        lay.addWidget(self.width_sec)

        self.opacity_slider = _Slider(10, 100, lambda v: f"{v}%", self)
        self.opacity_slider.slider.valueChanged.connect(
            lambda v: self._apply("opacity", v / 100.0, "opacity"))
        self.opacity_sec = _Section("Opacity", self.opacity_slider, self)
        lay.addWidget(self.opacity_sec)

        self.contents = QPlainTextEdit(self)
        self.contents.setPlaceholderText("Add a comment…")
        self.contents.setFixedHeight(84)
        self.contents.textChanged.connect(self._on_contents_edited)
        self.contents_sec = _Section("Comment", self.contents, self)
        lay.addWidget(self.contents_sec)
        self._contents_timer = QTimer(self)
        self._contents_timer.setSingleShot(True)
        self._contents_timer.setInterval(450)
        self._contents_timer.timeout.connect(self._commit_contents)

        self.delete_btn = QPushButton("Delete annotation", self)
        self.delete_btn.setProperty("variant", "danger")
        self.delete_btn.setIcon(icons.icon("delete"))
        self.delete_btn.clicked.connect(self._delete)
        lay.addSpacing(6)
        lay.addWidget(self.delete_btn)
        self.hint = label("", "faint", wrap=True)
        lay.addWidget(self.hint)
        self._syncing = False
        self.hide()

    # ---------------------------------------------------------------- binding
    def set_defaults(self, defaults) -> None:
        self.defaults = defaults

    def set_view(self, view: Optional["DocumentView"]) -> None:
        if self.view is view:
            return
        if self.view is not None:
            for sig in (self.view.annotationSelectionChanged,):
                try:
                    sig.disconnect(self.refresh)
                except TypeError:
                    pass
            try:
                self.view.tools.toolChanged.disconnect(self._on_tool_changed)
                self.view.propertiesRequested.disconnect(self.reveal)
            except TypeError:
                pass
        self.view = view
        if view is not None:
            view.annotationSelectionChanged.connect(self.refresh)
            view.tools.toolChanged.connect(self._on_tool_changed)
            view.propertiesRequested.connect(self.reveal)
            self.tool_name = view.tools.active_name
        self.refresh()

    def _on_tool_changed(self, name: str) -> None:
        if name != self.tool_name:
            self._dismissed = False
        self.tool_name = name
        self.refresh()

    def dismiss(self) -> None:
        self._commit_contents()
        self._dismissed = True
        self.hide()

    def reveal(self) -> None:
        self._dismissed = False
        self.refresh()

    # --------------------------------------------------------------- content
    def refresh(self) -> None:
        if self._contents_timer.isActive():
            self._commit_contents()
        v = self.view
        sel = v.selected_annotation() if v is not None else None
        prev_key = (self._mode, self._page, self._annot_id)
        if sel is not None and not sel[1].read_only:
            page, m = sel
            if (page, m.id) != (self._page, self._annot_id):
                self._dismissed = False
            self._mode, self._page, self._annot_id, self._kind = "annotation", page, m.id, m.kind
            self._show_model(m)
        elif v is not None and self.tool_name in TOOL_KINDS and self.defaults is not None:
            self._mode, self._page, self._annot_id = "tool", -1, ""
            self._kind = TOOL_KINDS[self.tool_name]
            self._show_tool(self.tool_name)
        else:
            self._mode, self._page, self._annot_id, self._kind = "", -1, "", None
        visible = bool(self._mode) and not self._dismissed
        if visible != self.isVisible():
            self.setVisible(visible)
        if visible:
            self.adjustSize()
            self.raise_()
            self.reposition()

    def _show_sections(self, kind: K, model: Optional[AnnotationModel]) -> None:
        probe = model or AnnotationModel(kind, rect=Rect(0, 0, 1, 1))
        self.color_sec.setVisible(probe.has_color and kind != K.FREE_TEXT)
        self.fill_sec.setVisible(probe.has_fill or kind == K.FREE_TEXT)
        self.fill_sec.title.setText("BACKGROUND" if kind == K.FREE_TEXT else "FILL")
        self.text_color_sec.setVisible(kind == K.FREE_TEXT)
        self.font_sec.setVisible(kind == K.FREE_TEXT)
        self.width_sec.setVisible(probe.has_width)
        self.opacity_sec.setVisible(True)
        self.contents_sec.setVisible(model is not None and kind != K.FREE_TEXT)
        self.contents_sec.title.setText("COMMENT" if kind != K.NOTE else "NOTE")
        self.delete_btn.setVisible(model is not None)
        self.kind_icon.setPixmap(icons.pixmap(KIND_ICONS.get(kind, "info"), 20))

    def _show_model(self, m: AnnotationModel) -> None:
        self._syncing = True
        try:
            self._show_sections(m.kind, m)
            if m.kind == K.STAMP:
                from ..pdf.stamps import STAMP_LABELS
                self.title.setText(f"Stamp · {STAMP_LABELS.get(m.stamp_name, m.stamp_name)}"
                                   if m.stamp_name else "Signature")
            else:
                self.title.setText(m.kind.label)
            who = f" · {m.author}" if m.author else ""
            self.subtitle.setText(f"Page {self._page + 1}{who}")
            self.color_row.set_color(qcolor(m.color) if m.color else None)
            self.fill_row.set_color(qcolor(m.fill) if m.fill else None)
            self.text_color_row.set_color(qcolor(m.text_color))
            self.font_size.setValue(m.font_size)
            self.width_slider.set_value(max(1, int(round(m.width * 2))))
            self.opacity_slider.set_value(int(round(m.opacity * 100)))
            if not self.contents.hasFocus():
                self.contents.setPlainText(m.contents)
            self.hint.setText("Drag to move · Delete to remove · Double-click to edit"
                              if m.can_move else "Delete to remove · Double-click to comment")
        finally:
            self._syncing = False

    def _show_tool(self, tool: str) -> None:
        self._syncing = True
        try:
            kind = TOOL_KINDS[tool]
            self._show_sections(kind, None)
            self.title.setText(self.view.tools.tools[tool].label)
            self.subtitle.setText("Default style for new annotations")
            d = self.defaults
            self.color_row.set_color(QColor(d.get(tool, "color")) if d.get(tool, "color") else None)
            self.fill_row.set_color(QColor(d.get(tool, "fill")) if d.get(tool, "fill") else None)
            self.text_color_row.set_color(QColor(d.get(tool, "text_color") or "#000000"))
            self.font_size.setValue(float(d.get(tool, "font_size", 12.0)))
            self.width_slider.set_value(max(1, int(round(float(d.get(tool, "width", 2.0)) * 2))))
            self.opacity_slider.set_value(int(round(float(d.get(tool, "opacity", 1.0)) * 100)))
            self.hint.setText("")
        finally:
            self._syncing = False

    # ----------------------------------------------------------------- edits
    def _current(self) -> Optional[AnnotationModel]:
        if self.view is None or self._mode != "annotation":
            return None
        sel = self.view.selected_annotation()
        if sel is None or sel[1].id != self._annot_id:
            return None
        return sel[1]

    def _apply(self, key: str, value, merge_key: Optional[str] = None) -> None:
        if self._syncing or self.view is None:
            return
        if self._mode == "tool":
            self.defaults.set(self.tool_name, key, value if key not in ("color", "fill", "text_color")
                              else to_hex(value) if value is not None else "")
            return
        m = self._current()
        if m is None:
            return
        new = m.with_changes(**{key: value})
        label_text = {"color": "Change color", "fill": "Change fill", "text_color": "Change text color",
                      "font_size": "Change font size", "width": "Change stroke width",
                      "opacity": "Change opacity", "contents": "Edit comment"}.get(key, "Edit annotation")
        self.view.execute(UpdateAnnotationCommand(self.view.session.page_id(self._page), m.id, new,
                                                  label_text, merge_key=merge_key and f"{m.id}:{merge_key}"))

    def _on_contents_edited(self) -> None:
        if not self._syncing:
            self._contents_timer.start()

    def _commit_contents(self) -> None:
        self._contents_timer.stop()
        m = self._current()
        if m is None:
            return
        text = self.contents.toPlainText()
        if text != m.contents:
            self._apply("contents", text, "contents")

    def _delete(self) -> None:
        if self.view is not None:
            self.view.delete_selected_annotation()

    # --------------------------------------------------------------- layout
    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        margin = 14
        x = p.width() - self.width() - margin - 12  # leave room for the scrollbar
        self.move(max(margin, x), margin)

    def refresh_icons(self) -> None:
        self.close_btn.setIcon(icons.icon("close"))
        self.delete_btn.setIcon(icons.icon("delete"))
        if self._kind is not None:
            self.kind_icon.setPixmap(icons.pixmap(KIND_ICONS.get(self._kind, "info"), 20))
