"""Replace-text dialog (redact & replace): text + style, prefilled from the
original."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QHBoxLayout, QLabel,
                             QPlainTextEdit, QToolButton, QVBoxLayout, QWidget)

from ...pdf.editing import FONT_CHOICES, base14_font
from ...pdf.models import TextStyle
from ..qtutil import color_from_q, label, qcolor
from ..widgets import ColorRow

_FAMILY_OF = {"helv": "sans", "hebo": "sans", "heit": "sans", "hebi": "sans",
              "tiro": "serif", "tibo": "serif", "tiit": "serif", "tibi": "serif",
              "cour": "mono", "cobo": "mono", "coit": "mono", "cobi": "mono"}
_PREVIEW_FAMILY = {"sans": "Arial", "serif": "Times New Roman", "mono": "Courier New"}


class ReplaceTextDialog(QDialog):
    def __init__(self, parent: QWidget, original: str, style: TextStyle):
        super().__init__(parent)
        self.setWindowTitle("Replace Text")
        self.setMinimumWidth(460)
        self.style_in = style
        font = style.font_name
        self._bold = font in ("hebo", "tibo", "cobo", "hebi", "tibi", "cobi")
        self._italic = font in ("heit", "tiit", "coit", "hebi", "tibi", "cobi")
        self._color = QColor(qcolor(style.color))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Replace text", "heading"))
        hint = ("The original text is removed and the new text is set in the same box. "
                "Long text shrinks to fit." if original else
                "Text set inside the selected area. Leave empty to erase the area.")
        lay.addWidget(label(hint, "muted", wrap=True))

        if original:
            orig = label(f"Original: “{original[:160]}{'…' if len(original) > 160 else ''}”",
                         "faint", wrap=True)
            lay.addWidget(orig)

        self.edit = QPlainTextEdit(self)
        self.edit.setPlainText(original)
        self.edit.setFixedHeight(96)
        self.edit.selectAll()
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.family = QComboBox(self)
        for text, key in FONT_CHOICES:
            self.family.addItem(text, key)
        self.family.setCurrentIndex(max(0, self.family.findData(_FAMILY_OF.get(font, "sans"))))
        row.addWidget(self.family, 1)
        self.size = QDoubleSpinBox(self)
        self.size.setRange(4, 144)
        self.size.setDecimals(1)
        self.size.setSuffix(" pt")
        self.size.setValue(style.font_size)
        row.addWidget(self.size)
        self.bold_btn = self._toggle("B", "Bold", self._bold, bold=True)
        self.italic_btn = self._toggle("I", "Italic", self._italic, italic=True)
        row.addWidget(self.bold_btn)
        row.addWidget(self.italic_btn)
        lay.addLayout(row)

        arow = QHBoxLayout()
        arow.addWidget(QLabel("Align:", self))
        self.align = QButtonGroup(self)
        for i, (text, tip) in enumerate((("Left", "Align left"), ("Center", "Center"),
                                         ("Right", "Align right"))):
            b = QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            self.align.addButton(b, i)
            arow.addWidget(b)
        self.align.button(0).setChecked(True)
        arow.addStretch(1)
        lay.addLayout(arow)

        lay.addWidget(label("TEXT COLOR", "section"))
        self.colors = ColorRow(("#1D1D1F", "#5F6672", "#FF3B30", "#007AFF", "#34C759"), self)
        self.colors.set_color(self._color)
        self.colors.colorChosen.connect(self._set_color)
        lay.addWidget(self.colors)

        lay.addWidget(label("PREVIEW ON THE PAGE BACKGROUND", "section"))
        self.preview = QLabel(self)
        self.preview.setMinimumHeight(48)
        self.preview.setStyleSheet(f"background: {qcolor(style.background).name()};"
                                   " border: 1px dashed rgba(0,0,0,70); border-radius: 6px;"
                                   " padding: 8px;")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel, self)
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Replace")
        ok.setProperty("variant", "primary")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for sig in (self.edit.textChanged, self.family.currentIndexChanged,
                    self.size.valueChanged, self.bold_btn.toggled, self.italic_btn.toggled,
                    self.align.idClicked):
            sig.connect(lambda *_: self._update_preview())
        self._update_preview()
        self.edit.setFocus()

    def _toggle(self, text: str, tip: str, checked: bool, bold=False, italic=False) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(True)
        b.setChecked(checked)
        f = b.font()
        f.setBold(bold)
        f.setItalic(italic)
        b.setFont(f)
        b.setFixedWidth(30)
        return b

    def _set_color(self, c: QColor) -> None:
        self._color = QColor(c)
        self._update_preview()

    def _update_preview(self) -> None:
        f = QFont(_PREVIEW_FAMILY[self.family.currentData()])
        f.setPointSizeF(max(6.0, min(28.0, self.size.value())))
        f.setBold(self.bold_btn.isChecked())
        f.setItalic(self.italic_btn.isChecked())
        self.preview.setFont(f)
        self.preview.setText(self.edit.toPlainText() or "(area will be erased)")
        self.preview.setAlignment([Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter,
                                   Qt.AlignmentFlag.AlignRight][max(0, self.align.checkedId())]
                                  | Qt.AlignmentFlag.AlignVCenter)
        self.preview.setStyleSheet(self.preview.styleSheet().split(" color:")[0] +
                                   f" color: {self._color.name()};")

    def values(self) -> Tuple[str, TextStyle, int]:
        style = replace(self.style_in,
                        font_size=float(self.size.value()),
                        color=color_from_q(self._color),
                        font_name=base14_font(self.family.currentData(),
                                              self.bold_btn.isChecked(),
                                              self.italic_btn.isChecked()))
        return self.edit.toPlainText(), style, max(0, self.align.checkedId())
