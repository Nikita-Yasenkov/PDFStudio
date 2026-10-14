"""Tickets 33/41: Sign tool, signature dialog, signature pad."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtTest import QTest

from pdf_studio.pdf import PdfDocument
from pdf_studio.pdf.models import AnnotationKind as K
from pdf_studio.pdf.models import Point
from pdf_studio.pdf.stamps import image_size
from pdf_studio.tests.conftest import pump
from pdf_studio.tests.test_stamps_engine import signature_png
from pdf_studio.ui.dialogs import prompts
from pdf_studio.ui.signatures import SignatureChoice, SignatureStore


@pytest.fixture
def view(window, plain_pdf):
    window.open_path(plain_pdf)
    pump(50)
    return window.view


def wp(view, x, y, page=0):
    return view.canvas.pdf_to_widget(page, Point(x, y)).toPoint()


def stamps(view, page=0):
    return [a for a in view.session.annotations(page) if a.kind == K.STAMP]


def test_click_places_signature_and_selects_it(window, view):
    window.arm_signature(SignatureChoice(image=signature_png(), label="Signature"))
    assert view.tools.active_name == "signature"
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 650))
    placed = stamps(view)
    assert len(placed) == 1
    assert placed[0].rect.center.x == pytest.approx(300, abs=1.5)
    assert placed[0].rect.width == pytest.approx(170, abs=1)  # default signature width
    assert view.tools.active_name == "select"
    assert view.selected_annotation()[1].id == placed[0].id
    assert view.session.dirty
    window.actions["undo"].trigger()
    assert stamps(view) == []


def test_drag_sets_size_keeping_aspect(window, view):
    data = signature_png(300, 100)  # 3:1
    window.arm_signature(SignatureChoice(image=data))
    vp = view.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=wp(view, 100, 600))
    QTest.mouseMove(vp, wp(view, 250, 650))
    QTest.mouseMove(vp, wp(view, 400, 700))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=wp(view, 400, 700))
    r = stamps(view)[0].rect
    assert r.width / r.height == pytest.approx(3.0, abs=0.1)
    assert r.width == pytest.approx(300, abs=2)


def test_named_stamp_from_menu(window, view):
    window.arm_signature(SignatureChoice(stamp_name="Draft", label="Draft"))
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 300))
    placed = stamps(view)
    assert len(placed) == 1 and placed[0].stamp_name == "Draft"


def test_tool_without_choice_asks_and_cancel_reverts(window, view, monkeypatch):
    asked = []
    monkeypatch.setattr(prompts, "ask_signature", lambda parent, store, start_tab=None: asked.append(1))
    window.set_tool("signature")
    assert asked == [1]
    assert view.tools.active_name == "select"
    assert window.actions["tool_select"].isChecked()


def test_tool_with_dialog_choice(window, view, monkeypatch):
    data = signature_png()
    monkeypatch.setattr(prompts, "ask_signature",
                        lambda parent, store, start_tab=None: SignatureChoice(image=data))
    window.set_tool("signature")
    assert view.tools.active_name == "signature"
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 400))
    assert len(stamps(view)) == 1


def test_signature_survives_save_and_reopen(window, view, tmp_path, monkeypatch):
    window.arm_signature(SignatureChoice(image=signature_png()))
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 650))
    out = str(tmp_path / "signed.pdf")
    monkeypatch.setattr(prompts, "ask_save_pdf", lambda *a, **k: out)
    assert window.save_as()
    doc = PdfDocument.open(out)
    try:
        got = [a for a in doc.annotations(0) if a.kind == K.STAMP]
        assert len(got) == 1
        # rendered: the signature's ink colour shows up inside its box
        r = got[0].rect
        img = doc.render(0, 1.0, clip=r)
        data = img.data
        dark = sum(1 for i in range(0, len(data), 3) if data[i] < 100 and data[i + 2] > 80)
        assert dark > 20
    finally:
        doc.close()


# ------------------------------------------------------------ dialog/store
def test_store_add_dedup_remove(tmp_path):
    store = SignatureStore(str(tmp_path / "sigs"))
    assert store.list() == []
    data = signature_png()
    p1 = store.add(data)
    p2 = store.add(data)
    assert p1 == p2 and len(store.list()) == 1
    store.add(signature_png(200, 80))
    assert len(store.list()) == 2
    store.remove(p1)
    assert len(store.list()) == 1


def test_dialog_import_and_save(window, tmp_path):
    from pdf_studio.ui.dialogs.signature import SignatureDialog
    store = SignatureStore(str(tmp_path / "sigs"))
    png_path = tmp_path / "scan.png"
    import pymupdf as fitz  # helper: white background "scan"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 90), False)
    pix.set_rect(pix.irect, (252, 252, 250))
    for x in range(30, 210):
        pix.set_pixel(x, 45, (15, 25, 90))
    png_path.write_bytes(pix.tobytes("png"))
    dlg = SignatureDialog(window, store, start_tab=SignatureDialog.TAB_IMPORT)
    assert not dlg.ok.isEnabled()
    assert dlg.load_image_file(str(png_path))
    choice = dlg.current_choice()
    w, h = image_size(choice.image)
    assert w < 240 and h < 30  # background removed + trimmed
    dlg._accept()
    assert dlg.choice is not None and len(store.list()) == 1
    # now it shows up in the Saved tab
    dlg2 = SignatureDialog(window, store)
    assert dlg2.tabs.currentIndex() == SignatureDialog.TAB_SAVED
    assert dlg2.saved.count() == 1 and dlg2.current_choice().image
    dlg2.tabs.setCurrentIndex(SignatureDialog.TAB_STAMPS)
    assert dlg2.current_choice().stamp_name == "Approved"
    for d in (dlg, dlg2):
        d.close()


def test_signature_pad_draw_to_png(window):
    from pdf_studio.ui.dialogs.signature_pad import SignaturePadPanel
    panel = SignaturePadPanel()
    panel.resize(480, 220)
    panel.show()
    pad = panel.pad
    assert pad.png() is None and not panel.clear_btn.isEnabled()
    QTest.mousePress(pad, Qt.MouseButton.LeftButton, pos=QPoint(40, 120))
    for x in range(40, 300, 8):
        QTest.mouseMove(pad, QPoint(x, 120 + (20 if (x // 16) % 2 else -10)))
    QTest.mouseRelease(pad, Qt.MouseButton.LeftButton, pos=QPoint(300, 110))
    assert panel.clear_btn.isEnabled()
    png = pad.png()
    w, h = image_size(png)
    assert 700 < w < 900 and h < 140  # 3x export, trimmed to the strokes
    panel.colors.button(1).click()
    assert pad.color.name() == "#1f3b8c"
    pad.undo_stroke()
    assert pad.is_empty
    panel.close()
    panel.deleteLater()


# ---------------------------------------------------------------- ticket 41
def _scribble(pad):
    QTest.mousePress(pad, Qt.MouseButton.LeftButton, pos=QPoint(50, 100))
    for i in range(1, 40):
        QTest.mouseMove(pad, QPoint(50 + i * 7, 100 + (12 if i % 3 else -14)))
    QTest.mouseRelease(pad, Qt.MouseButton.LeftButton, pos=QPoint(330, 96))


def test_drawn_signature_places_via_add_command(window, view, tmp_path):
    from pdf_studio.core.annotation_commands import AddAnnotationCommand
    from pdf_studio.pdf.models import Rect
    from pdf_studio.pdf.stamps import signature_model
    from pdf_studio.ui.dialogs.signature import SignatureDialog
    store = SignatureStore(str(tmp_path / "sigs"))
    dlg = SignatureDialog(window, store, start_tab=SignatureDialog.TAB_DRAW)
    dlg.resize(620, 470)
    dlg.show()
    pump(30)
    assert not dlg.ok.isEnabled()  # nothing drawn yet
    _scribble(dlg.pad_panel.pad)
    assert dlg.ok.isEnabled()
    dlg._accept()
    choice = dlg.choice
    assert choice is not None and choice.image[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(store.list()) == 1  # "Save for next time" is on by default
    # the PNG bytes work with the regular add-annotation command
    pid = view.session.page_id(0)
    cmd = AddAnnotationCommand(pid, signature_model(Rect(300, 600, 470, 660), choice.image))
    assert view.execute(cmd)
    assert stamps(view)[0].id == cmd.annot_id
    dlg.close()


def test_drawn_signature_not_saved_when_unchecked(window, tmp_path):
    from pdf_studio.ui.dialogs.signature import SignatureDialog
    store = SignatureStore(str(tmp_path / "sigs"))
    dlg = SignatureDialog(window, store, start_tab=SignatureDialog.TAB_DRAW)
    dlg.resize(620, 470)
    dlg.show()
    pump(30)
    _scribble(dlg.pad_panel.pad)
    dlg.pad_panel.save_box.setChecked(False)
    dlg._accept()
    assert dlg.choice is not None and store.list() == []
    dlg.close()


def test_sign_menu_offers_saved_signatures(window, view, tmp_path):
    store = window.tool_defaults.signature_store
    store.add(signature_png())
    from PyQt6.QtWidgets import QMenu
    menu = QMenu(window)
    window._fill_sign_menu(menu)
    texts = [a.text() for a in menu.actions()]
    assert "Saved signatures" in texts and "New Signature…" in texts
    pick = [a for a in menu.actions() if a.toolTip() == "Place this signature"]
    assert pick
    pick[0].trigger()
    assert view.tools.active_name == "signature"
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=wp(view, 300, 500))
    assert len(stamps(view)) == 1
