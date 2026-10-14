"""Ticket 06: lazy page thumbnails."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from pdf_studio.core.render_cache import WHOLE_PAGE
from pdf_studio.tests.conftest import pump


def test_thumbnail_panel_lists_pages_and_navigates(window, sample_pdf):
    window.open_path(sample_pdf)
    pump(100)
    panel = window.thumbnails
    assert panel.model.rowCount() == 12
    idx = panel.model.index(6)
    panel.list.scrollTo(idx)
    pump(50)
    rect = panel.list.visualRect(idx)
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window.view.current_page == 6


def test_thumbnails_are_lazy_on_large_documents(window, long_pdf):
    t0 = time.perf_counter()
    window.open_path(long_pdf)
    opened = time.perf_counter() - t0
    assert opened < 3.0  # opening does not rasterise 400 thumbnails
    pump(800)
    panel = window.thumbnails
    uid = window.view.session.uid
    thumbs = {k.page_id for k in window.render_service.cache
              if k.doc == uid and k.tx == WHOLE_PAGE}
    assert 0 < len(thumbs) < 20
    assert panel.requests_submitted < 40


def test_thumbnail_tracks_current_page(window, long_pdf):
    window.open_path(long_pdf)
    pump(100)
    window.view.go_to_page(250)
    pump(100)
    panel = window.thumbnails
    rect = panel.list.visualRect(panel.model.index(250))
    assert panel.list.viewport().rect().intersects(rect)


def test_panel_rebinds_on_tab_switch(window, sample_pdf, plain_pdf):
    window.open_path(sample_pdf)
    window.open_path(plain_pdf)
    assert window.thumbnails.model.rowCount() == 2
    window.activate_view(window.views()[0])
    assert window.thumbnails.model.rowCount() == 12
    window.close_all()
    assert window.thumbnails.model.rowCount() == 0


def test_sidebar_toggle(window, sample_pdf):
    window.open_path(sample_pdf)
    assert window.side.isVisible()
    window.actions["toggle_sidebar"].trigger()
    assert not window.side.isVisible()
    window.actions["toggle_sidebar"].trigger()
    assert window.side.isVisible()


# ---------------------------------------------------------------- ticket 08
def test_outline_panel_shows_bookmarks_and_navigates(window, sample_pdf):
    window.open_path(sample_pdf)
    window.actions["show_outline"].trigger()
    panel = window.outline_panel
    assert window.side.currentWidget() is panel
    assert panel.entry_count == 12
    assert panel.tree.topLevelItemCount() == 4  # Cover + 3 parts
    part3 = panel.tree.topLevelItem(3)
    images = next(part3.child(i) for i in range(part3.childCount())
                  if part3.child(i).text(0) == "Images")
    panel._on_item_activated(images)
    v = window.view
    assert v.current_page == 9
    # the destination y (300pt) is near the top of the viewport
    wp = v.canvas.pdf_to_widget(9, __import__("pdf_studio.pdf", fromlist=["Point"]).Point(0, 300))
    assert 0 <= wp.y() <= 60


def test_outline_empty_state(window, plain_pdf):
    window.open_path(plain_pdf)
    window.actions["show_outline"].trigger()
    panel = window.outline_panel
    assert panel.entry_count == 0
    assert panel.stack.currentWidget() is panel.empty
    assert "no bookmarks" in panel.empty_text.text()


def test_outline_follows_current_page(window, sample_pdf):
    window.open_path(sample_pdf)
    window.show_side_panel("outline")
    window.view.go_to_page(5)
    pump(30)
    assert window.outline_panel.tree.currentItem().text(0) == "Rotation"


def test_selected_thumbnail_still_shows_the_page(window, plain_pdf):
    # regression: the outline of a selected, non-current thumbnail was drawn
    # with the card brush and painted over the page
    from PyQt6.QtGui import QColor, QImage, QPainter
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

    from pdf_studio.ui import theme
    from pdf_studio.ui.thumbnails import PAD_Y

    window.open_path(plain_pdf)
    pump(100)
    panel = window.thumbnails
    assert window.view.current_page == 0
    index = panel.model.index(1)
    rect = panel.list.visualRect(index).translated(0, -panel.list.visualRect(index).y())
    opt = QStyleOptionViewItem()
    opt.initFrom(panel.list)
    opt.rect = rect
    opt.state |= QStyle.StateFlag.State_Selected
    img = QImage(rect.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(theme.current().surface))
    p = QPainter(img)
    panel.list.itemDelegate().paint(p, opt, index)
    p.end()
    box = panel.thumb_box(1)
    x = (rect.width() - box.width()) // 2
    corner = img.pixelColor(x + 4, PAD_Y + 4)  # inside the page's top margin
    assert corner.lightness() >= 252, corner.name()  # white page, not the card tint
