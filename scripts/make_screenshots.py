"""Generate the README screenshots from the real application.

    .venv\\Scripts\\python scripts\\make_screenshots.py

Writes PNGs to docs/screenshots/. A fictional demo agreement is generated,
annotated through the app's own commands, and captured in light and dark
themes. Dialogs are composited onto the window capture with a soft shadow.
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
# render off screen (nothing flashes on the desktop); that platform has no font
# database of its own, so it is pointed at the Windows fonts
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
OUT = os.path.join(ROOT, "docs", "screenshots")
SIZE = (1600, 1000)

import pymupdf as fitz  # noqa: E402  (demo document only)

INK = (0.12, 0.14, 0.18)
MUTED = (0.42, 0.45, 0.50)
ACCENT = (0.15, 0.39, 0.92)

CLAUSES = [
    ("1. Parties",
     "This Consulting Services Agreement is made between Harbor Lane Architects Ltd. "
     '(the "Client") and Bluefield Design Studio (the "Consultant"). '
     "Both parties agree to the terms below."),
    ("2. Scope of Work",
     "The Consultant will deliver a brand identity, a responsive marketing website and "
     "a library of document templates, as described in Schedule A. Changes to the scope "
     "require written approval from both parties."),
    ("3. Fees and Payment",
     "The total fee is EUR 48,000, invoiced per milestone. Invoices are payable within "
     "30 days of receipt. Late payments accrue interest at 1% per month."),
    ("4. Timeline",
     "Work begins on 1 November 2026. The milestones and their dates are listed in the "
     "table below; dates may move by agreement in writing."),
]
MILESTONES = [("Milestone", "Due date", "Amount"),
              ("1. Discovery and brand strategy", "20 Nov 2026", "EUR 12,000"),
              ("2. Visual identity and templates", "8 Jan 2027", "EUR 16,000"),
              ("3. Website design and build", "26 Feb 2027", "EUR 20,000")]
PAGE2 = [
    ("5. Confidentiality",
     "Each party keeps the other party's confidential information secret and uses it "
     "only to perform this Agreement. This obligation survives termination for 3 years."),
    ("6. Intellectual Property",
     "On full payment, all rights in the final deliverables pass to the Client. The "
     "Consultant may show the work in its portfolio after public launch."),
    ("7. Termination",
     "Either party may end this Agreement with 30 days' written notice. The Client pays "
     "for work completed up to the termination date."),
    ("8. Governing Law",
     "This Agreement is governed by the laws of the Netherlands. Disputes go to the "
     "competent court in Amsterdam."),
]


def make_demo(path: str) -> None:
    doc = fitz.open()
    w, h = fitz.paper_rect("a4").br

    def header(page, n):
        page.draw_rect(fitz.Rect(0, 0, w, 64), color=None, fill=(0.95, 0.96, 0.98))
        page.draw_rect(fitz.Rect(56, 22, 76, 42), color=None, fill=ACCENT)
        page.insert_text((84, 37), "Bluefield Design Studio", fontsize=11, fontname="hebo", color=INK)
        page.insert_text((w - 200, 37), f"Agreement 2026-114  ·  Page {n} of 3",
                         fontsize=9, fontname="helv", color=MUTED)

    def clauses(page, y, items):
        for title, body in items:
            page.insert_text((56, y), title, fontsize=12.5, fontname="hebo", color=INK)
            y += 10
            page.insert_textbox(fitz.Rect(56, y, w - 56, y + 70), body, fontsize=10.5,
                                fontname="helv", color=INK, lineheight=1.5)
            y += 72
        return y

    p = doc.new_page(width=w, height=h)
    header(p, 1)
    p.insert_text((56, 118), "Consulting Services Agreement", fontsize=24, fontname="hebo", color=INK)
    p.insert_text((56, 142), "Effective 1 November 2026  ·  Harbor Lane Architects × Bluefield",
                  fontsize=11, fontname="helv", color=MUTED)
    p.draw_line((56, 158), (w - 56, 158), color=(0.85, 0.86, 0.88), width=0.8)
    y = clauses(p, 190, CLAUSES)
    # milestone table
    col = [56, 300, 420]
    p.draw_rect(fitz.Rect(56, y - 4, w - 56, y + 18), color=None, fill=(0.95, 0.96, 0.98))
    for i, row in enumerate(MILESTONES):
        yy = y + 10 + i * 24
        for c, text in enumerate(row):
            p.insert_text((col[c] + 8, yy), text, fontsize=10, fontname="hebo" if i == 0 else "helv",
                          color=INK)
        p.draw_line((56, yy + 8), (w - 56, yy + 8), color=(0.88, 0.89, 0.91), width=0.6)

    p2 = doc.new_page(width=w, height=h)
    header(p2, 2)
    clauses(p2, 110, PAGE2)

    p3 = doc.new_page(width=w, height=h)
    header(p3, 3)
    p3.insert_text((56, 118), "Signatures", fontsize=20, fontname="hebo", color=INK)
    p3.insert_textbox(fitz.Rect(56, 136, w - 56, 190),
                      "Signed by the authorised representatives of both parties on the dates below.",
                      fontsize=10.5, fontname="helv", color=INK)
    for i, (who, name) in enumerate((("Client", "Dana Whitaker, Managing Partner"),
                                     ("Consultant", "Alex Morgan, Creative Director"))):
        x = 56 + i * 250
        p3.insert_text((x, 230), who.upper(), fontsize=9, fontname="hebo", color=MUTED)
        p3.draw_line((x, 300), (x + 210, 300), color=INK, width=0.8)
        p3.insert_text((x, 316), name, fontsize=10, fontname="helv", color=INK)
        p3.draw_line((x, 360), (x + 210, 360), color=(0.7, 0.72, 0.75), width=0.6)
        p3.insert_text((x, 374), "Date", fontsize=9, fontname="helv", color=MUTED)
    doc.set_metadata({"title": "Consulting Services Agreement"})
    doc.set_toc([[1, "Parties", 1], [1, "Scope of Work", 1], [1, "Fees and Payment", 1],
                 [1, "Timeline", 1], [1, "Confidentiality", 2], [1, "Intellectual Property", 2],
                 [1, "Termination", 2], [1, "Governing Law", 2], [1, "Signatures", 3]])
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    work = tempfile.mkdtemp()
    demo = os.path.join(work, "Consulting Agreement.pdf")
    make_demo(demo)

    from PyQt6.QtCore import QEventLoop, QPoint, QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap

    from pdf_studio.ui.app import create_application
    app = create_application(["shots"])
    from pdf_studio.core.annotation_commands import AddAnnotationCommand
    from pdf_studio.core.layout import ViewMode
    from pdf_studio.core.selection import select_between
    from pdf_studio.pdf.models import AnnotationKind as K
    from pdf_studio.pdf.models import AnnotationModel, Point, Rect
    from pdf_studio.pdf.stamps import signature_model, trim_transparent
    from pdf_studio.ui.icons import app_icon
    from pdf_studio.ui.main_window import MainWindow
    from pdf_studio.ui.settings import AppSettings
    from pdf_studio.ui.signatures import SignatureStore
    from pdf_studio.ui.theme import DARK, LIGHT, ThemeManager

    app.setWindowIcon(app_icon())

    def pump(ms=300):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def settle():
        for _ in range(8):
            pump(120)

    def save(img: QImage, name: str) -> None:
        img.save(os.path.join(OUT, name))
        print("wrote", name)

    def composite(base: QPixmap, dialog_pm: QPixmap, dy: int = 0) -> QImage:
        img = base.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(img.rect(), QColor(0, 0, 0, 70))  # modal dim
        x = (img.width() - dialog_pm.width()) // 2
        y = (img.height() - dialog_pm.height()) // 2 + dy
        for i in range(14, 0, -2):  # soft shadow
            path = QPainterPath()
            path.addRoundedRect(QRectF(x - i, y - i + 6, dialog_pm.width() + 2 * i,
                                       dialog_pm.height() + 2 * i), 14 + i, 14 + i)
            p.fillPath(path, QColor(0, 0, 0, 7))
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(x, y, dialog_pm.width(), dialog_pm.height()), 10, 10)
        p.setClipPath(clip)
        p.drawPixmap(x, y, dialog_pm)
        p.end()
        return img

    def signature_png() -> bytes:
        img = QImage(900, 260, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont("Segoe Script", 64)
        f.setItalic(True)
        p.setFont(f)
        p.setPen(QColor("#1F3B8C"))
        p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "Alex Morgan")
        p.end()
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        return trim_transparent(bytes(ba), pad=8)

    from pdf_studio.ui.icons import app_logo_pixmap
    save(app_logo_pixmap(96, dpr=2.0).toImage(), "logo.png")

    store = SignatureStore(os.path.join(work, "sigs"))
    sig = signature_png()
    store.add(sig)

    tm = ThemeManager(app, LIGHT)
    tm.apply()
    w = MainWindow(settings=AppSettings.from_file(os.path.join(work, "s.ini")), theme_manager=tm)
    w.tool_defaults.signature_store = store
    w.resize(*SIZE)
    w.show()
    w.open_path(demo)
    v = w.view
    s = v.session
    settle()

    # --- annotate page 1 through the app's own commands
    words = s.words(0)

    def span(first, last, start=0):
        a = next(i for i, x in enumerate(words) if i >= start and x.text.startswith(first))
        b = next(i for i, x in enumerate(words) if i >= a and x.text.startswith(last))
        return select_between(0, words, words[a].rect.center, words[b].rect.center)

    pid = s.page_id(0)
    hl = span("Invoices", "receipt.")
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.HIGHLIGHT, hl.bounds, quads=tuple(hl.line_rects), color=(1, 0.84, 0.04),
        contents="Ask for 14 days instead of 30.", author="Dana")))
    ul = span("brand", "templates")
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.UNDERLINE, ul.bounds, quads=tuple(ul.line_rects), color=(0, 0.48, 1))))
    st = span("Late", "month.")
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.STRIKEOUT, st.bounds, quads=tuple(st.line_rects), color=(1, 0.23, 0.19))))
    fee_line = hl.line_rects[0]
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.NOTE, Rect(fee_line.x1 + 14, fee_line.y0 - 4, fee_line.x1 + 32, fee_line.y0 + 14),
        contents="Payment terms: can we do 14 days?", color=(1, 0.84, 0.04), author="Dana")))
    # circle milestone 3's date and leave a margin note pointing at it
    jan = next(x for x in words if x.text == "Feb")
    date = Rect(jan.rect.x0 - 18, jan.rect.y0 - 4, jan.rect.x1 + 36, jan.rect.y1 + 4)
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.ELLIPSE, date, color=(0.85, 0.15, 0.12), width=1.5)))
    note_box = Rect(date.x0 - 30, date.y1 + 74, date.x0 + 160, date.y1 + 116)
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.FREE_TEXT, note_box, contents="Move milestone 3\nto 15 March",
        font_size=12, text_color=(0.85, 0.15, 0.12), fill=(1.0, 0.97, 0.9))))
    v.execute(AddAnnotationCommand(pid, AnnotationModel(
        K.ARROW, Rect(0, 0, 0, 0),
        strokes=((Point(note_box.x0 + 40, note_box.y0 - 2), Point(date.center.x - 6, date.y1 + 2)),),
        color=(0.85, 0.15, 0.12), width=2)))
    # signature on page 3
    v.execute(AddAnnotationCommand(s.page_id(2), signature_model(Rect(312, 250, 506, 298), sig,
                                                                 "Alex Morgan")))
    s.stack.set_clean()

    # 1) hero: annotations + inspector (light)
    v.set_zoom(0.9)
    v.go_to_page(0)
    v.canvas.verticalScrollBar().setValue(int(v.canvas.layout_.slot_for(0).y + 80))
    w.show_side_panel("pages")
    sel = next(a for a in s.annotations(0) if a.kind == K.HIGHLIGHT)
    v.select_annotation(0, sel.id)
    settle()
    save(w.grab().toImage(), "hero.png")

    # 2) signature: dialog over the signatures page
    v.clear_annotation_selection()
    v.go_to_page(2)
    v.canvas.verticalScrollBar().setValue(int(v.canvas.layout_.slot_for(2).y))
    settle()
    from pdf_studio.ui.dialogs.signature import SignatureDialog
    dlg = SignatureDialog(w, store)
    dlg.resize(620, 470)
    dlg.show()
    pump(200)
    save(composite(w.grab(), dlg.grab()), "sign.png")
    dlg.close()
    # ...and the placed signature, selected
    sig_annot = next(a for a in s.annotations(2) if a.kind == K.STAMP)
    v.select_annotation(2, sig_annot.id)
    settle()
    save(w.grab().toImage(), "signed.png")
    v.clear_annotation_selection()

    # 3) edit text: Replace Text dialog
    v.go_to_page(0)
    target = span("30", "days", start=next(i for i, x in enumerate(words) if x.text == "within"))
    v.set_text_selection(target)
    v.reveal(0, target.bounds)
    settle()
    from pdf_studio.ui.dialogs.replace_text import ReplaceTextDialog
    style = s.probe_text_style(0, target.bounds.inflated(0.5))
    rd = ReplaceTextDialog(w, target.text, style)
    rd.edit.setPlainText("14 days")
    rd.show()
    pump(200)
    save(composite(w.grab(), rd.grab(), dy=40), "edit-text.png")
    rd.close()
    v.clear_selection()

    # 4) dark: two-page spread + search
    tm.set_choice(DARK)
    w._on_theme_changed()
    w.set_view_mode(ViewMode.TWO_PAGE)
    v.fit_page()
    w.find()
    w.search_panel.field.setText("payment")
    w.search_panel._run_now(jump=True)
    settle()
    save(w.grab().toImage(), "dark-search.png")

    # 5) organize pages (dark): thumbnails multi-select + insert dialog
    w.set_view_mode(ViewMode.CONTINUOUS)
    v.fit_page()
    v.go_to_page(0)
    w.show_side_panel("pages")
    from PyQt6.QtCore import QItemSelectionModel
    sm = w.thumbnails.list.selectionModel()
    sm.select(w.thumbnails.model.index(1), QItemSelectionModel.SelectionFlag.ClearAndSelect)
    sm.select(w.thumbnails.model.index(2), QItemSelectionModel.SelectionFlag.Select)
    settle()
    from pdf_studio.pdf import PdfDocument
    from pdf_studio.ui.dialogs.insert_pages import InsertPagesDialog
    src = PdfDocument.open(demo)
    idlg = InsertPagesDialog(w, src, "Consulting Agreement.pdf", 3, 0)
    idlg.picker._on_range_edited("2-3")
    idlg.picker.range_edit.setText("2-3")
    idlg.show()
    for _ in range(10):
        pump(100)
    save(composite(w.grab(), idlg.grab()), "organize.png")
    idlg.close()
    src.close()

    # 6) welcome screen (light), with recent files in a demo folder: the shown
    # folder is truncated to its tail, so the temp location stays out of view
    tm.set_choice(LIGHT)
    w._on_theme_changed()
    w.close_all()
    import shutil
    folder = os.path.join(work, "Projects", "Clients", "Harbor Lane Architects", "Agreements")
    os.makedirs(folder)
    w.settings.clear_recent()
    for name in ("Site Plan - Rev C.pdf", "Invoice 2026-031.pdf", "Consulting Agreement.pdf"):
        dest = os.path.join(folder, name)
        shutil.copy(demo, dest)
        w.settings.add_recent(dest)
    w._refresh_recent()
    settle()
    save(w.grab().toImage(), "welcome.png")
    w.close()


if __name__ == "__main__":
    main()
