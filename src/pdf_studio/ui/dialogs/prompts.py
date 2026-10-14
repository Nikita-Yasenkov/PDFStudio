"""Modal prompts used by the shell.

Every blocking dialog the main window shows goes through a function in this
module, so tests can monkeypatch them (no modal hangs in CI).
"""

from __future__ import annotations

import enum
from typing import List, Optional

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

PDF_FILTER = "PDF documents (*.pdf);;All files (*)"
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff);;All files (*)"


class CloseChoice(enum.Enum):
    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


def ask_open_pdfs(parent: QWidget, directory: str = "") -> List[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, "Open PDF", directory, PDF_FILTER)
    return list(paths)


def ask_open_pdf(parent: QWidget, directory: str = "", title: str = "Choose a PDF") -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(parent, title, directory, PDF_FILTER)
    return path or None


def ask_open_image(parent: QWidget, directory: str = "", title: str = "Choose an image") -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(parent, title, directory, IMAGE_FILTER)
    return path or None


def ask_save_pdf(parent: QWidget, suggested: str, title: str = "Save As") -> Optional[str]:
    path, _ = QFileDialog.getSaveFileName(parent, title, suggested, PDF_FILTER)
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    return path


def ask_directory(parent: QWidget, directory: str = "", title: str = "Choose a folder") -> Optional[str]:
    path = QFileDialog.getExistingDirectory(parent, title, directory)
    return path or None


def ask_close_choice(parent: QWidget, name: str) -> CloseChoice:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Unsaved changes")
    box.setText(f"Save changes to “{name}” before closing?")
    box.setInformativeText("Your changes will be lost if you don’t save them.")
    save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Don’t Save", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return CloseChoice.SAVE
    if clicked is discard:
        return CloseChoice.DISCARD
    return CloseChoice.CANCEL


def ask_password(parent: QWidget, name: str, wrong: bool = False) -> Optional[str]:
    """Password for an encrypted PDF; None when cancelled."""
    from .password import PasswordDialog
    dlg = PasswordDialog(parent, name, wrong)
    return dlg.password() if dlg.exec() == PasswordDialog.DialogCode.Accepted else None


def ask_insert_pages(parent: QWidget, source, source_name: str, target_count: int,
                     current_page: int, default_position: int = 1):
    """Choose pages + position; returns (pages, insert index) or None."""
    from .insert_pages import InsertPagesDialog
    dlg = InsertPagesDialog(parent, source, source_name, target_count, current_page,
                            default_position)
    if dlg.exec() == InsertPagesDialog.DialogCode.Accepted:
        return dlg.result_values()
    return None


def ask_extract(parent: QWidget, doc, name: str, initial):
    """Returns (pages, open_after, delete_after) or None."""
    from .page_tools import ExtractDialog
    dlg = ExtractDialog(parent, doc, name, initial)
    return dlg.values() if dlg.exec() == ExtractDialog.DialogCode.Accepted else None


def ask_split(parent: QWidget, page_count: int, name: str, out_dir: str, bookmark_groups):
    """Returns (groups, folder) or None."""
    from .page_tools import SplitDialog
    dlg = SplitDialog(parent, page_count, name, out_dir, bookmark_groups)
    return dlg.values() if dlg.exec() == SplitDialog.DialogCode.Accepted else None


def ask_combine(parent: QWidget, initial, current_name, start_dir: str, page_counter=None):
    """Returns (paths, mode) or None; mode 0 = new document, 1 = append."""
    from .page_tools import CombineDialog
    dlg = CombineDialog(parent, initial, current_name, start_dir, page_counter)
    return dlg.values() if dlg.exec() == CombineDialog.DialogCode.Accepted else None


def ask_replace_text(parent: QWidget, original: str, style):
    """Returns (text, TextStyle, align) or None."""
    from .replace_text import ReplaceTextDialog
    dlg = ReplaceTextDialog(parent, original, style)
    return dlg.values() if dlg.exec() == ReplaceTextDialog.DialogCode.Accepted else None


def ask_signature(parent: QWidget, store, start_tab=None):
    """Pick/draw/import a signature or a stamp; returns SignatureChoice or None."""
    from .signature import SignatureDialog
    dlg = SignatureDialog(parent, store, start_tab)
    return dlg.choice if dlg.exec() == SignatureDialog.DialogCode.Accepted else None


def show_about(parent: QWidget) -> None:
    from .about import AboutDialog
    AboutDialog(parent).exec()


def show_shortcuts(parent: QWidget, rows) -> None:
    from .about import ShortcutsDialog
    ShortcutsDialog(parent, rows).exec()


def show_integration(parent: QWidget) -> None:
    from .integration import IntegrationDialog
    IntegrationDialog(parent).exec()


def show_error(parent: QWidget, title: str, text: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    box.exec()


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "OK",
            destructive: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok
