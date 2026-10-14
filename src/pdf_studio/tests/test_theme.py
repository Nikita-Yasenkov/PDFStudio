"""Tickets 37/42: light / dark theme and persistence."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from pdf_studio.tests.conftest import pump
from pdf_studio.ui import theme as th
from pdf_studio.ui.icons import icons
from pdf_studio.ui.settings import AppSettings


def test_theme_tokens_have_contrast():
    for t in (th.LIGHT_THEME, th.DARK_THEME):
        bg, fg = QColor(t.window), QColor(t.text)
        assert abs(bg.lightnessF() - fg.lightnessF()) > 0.6
        muted = QColor(t.text_muted)
        assert abs(bg.lightnessF() - muted.lightnessF()) > 0.3
    assert th.DARK_THEME.is_dark and not th.LIGHT_THEME.is_dark


def test_resolve_choices():
    assert th.resolve(th.LIGHT) is th.LIGHT_THEME
    assert th.resolve(th.DARK) is th.DARK_THEME
    assert th.resolve("bogus") is th.LIGHT_THEME
    assert th.resolve(th.SYSTEM) in (th.LIGHT_THEME, th.DARK_THEME)


def test_window_switches_and_persists_theme(window, sample_pdf):
    window.open_path(sample_pdf)
    window.set_theme(th.DARK)
    app = QApplication.instance()
    assert app.palette().color(QPalette.ColorRole.Window).name() == th.DARK_THEME.window.lower()
    assert th.current().is_dark
    assert window.actions["theme_dark"].isChecked()
    assert icons._normal.name() == th.DARK_THEME.icon.lower()  # icons recoloured
    assert window.settings.theme == "dark"
    window.actions["toggle_theme"].trigger()
    assert not th.current().is_dark and window.settings.theme == "light"
    window.set_theme(th.SYSTEM)
    assert window.actions["theme_system"].isChecked()
    window.set_theme(th.LIGHT)


def test_theme_round_trip_through_settings(qapp, tmp_path, monkeypatch):
    from pdf_studio.ui.dialogs import prompts
    from pdf_studio.ui.main_window import MainWindow
    monkeypatch.setattr(prompts, "show_error", lambda *a, **k: None)
    ini = str(tmp_path / "theme.ini")
    w = MainWindow(settings=AppSettings.from_file(ini))
    w.show()
    w.set_theme(th.DARK)
    w.close()
    w.deleteLater()
    pump()
    assert AppSettings.from_file(ini).theme == "dark"
    w2 = MainWindow(settings=AppSettings.from_file(ini))
    assert w2.theme_manager.choice == th.DARK
    assert w2.actions["theme_dark"].isChecked()
    w2.set_theme(th.LIGHT)
    w2.close()
    w2.deleteLater()
    pump()


def test_page_canvas_stays_white_in_dark_mode(window, plain_pdf):
    window.open_path(plain_pdf)
    window.set_theme(th.DARK)
    v = window.view
    pump(600)
    img = v.canvas.viewport().grab().toImage()
    slot = v.canvas.page_rect_widget(0)
    c = img.pixelColor(int(slot.left() + 8), int(slot.top() + 300))
    assert c.lightness() > 240  # paper stays white
    bg = img.pixelColor(2, 2)
    assert bg.lightness() < 60  # chrome is dark
    window.set_theme(th.LIGHT)
