"""Per-user Windows installation and PDF file association (stdlib only).

* ``install(source_dir)`` copies the built app folder to
  ``%LOCALAPPDATA%\\Programs\\PDF Studio``, creates Desktop and Start-menu
  shortcuts, registers the app as a PDF handler and adds an entry to
  *Settings → Apps* for uninstalling. No administrator rights needed:
  everything goes under ``HKEY_CURRENT_USER``.
* Windows 10/11 don't let applications make themselves the default handler
  (the choice is protected); :func:`open_default_apps_settings` opens the
  page where the user picks PDF Studio for ``.pdf`` once.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from .. import APP_NAME, __version__

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:  # pragma: no branch
    import winreg

EXE_NAME = "PDFStudio.exe"
PROG_ID = "PDFStudio.PDF"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFStudio"
CAPABILITIES_KEY = r"Software\PDF Studio\Capabilities"
PUBLISHER = "PDF Studio"


def default_install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "Programs", APP_NAME)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Optional[str]:
    """The packaged executable we're running from, if any."""
    return os.path.abspath(sys.executable) if is_frozen() else None


@dataclass
class Registry:
    """Where keys are written. Tests point ``prefix`` at a scratch key."""
    prefix: str = ""   # e.g. r"Software\PDFStudioTests\\" to sandbox everything

    def path(self, key: str) -> str:
        return self.prefix + key

    def classes(self, key: str) -> str:
        return self.path(r"Software\Classes" + "\\" + key)

    def set(self, key: str, name: Optional[str], value, kind: Optional[int] = None) -> None:
        kind = winreg.REG_SZ if kind is None else kind
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, kind, value)

    def get(self, key: str, name: Optional[str] = None) -> Optional[str]:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                value = winreg.QueryValueEx(k, name)[0]
                return "" if value is None else value  # empty REG_NONE reads as None
        except OSError:
            return None

    def delete_tree(self, key: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    self.delete_tree(key + "\\" + sub)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass

    def delete_value(self, key: str, name: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


@dataclass
class InstallResult:
    exe: str
    install_dir: str
    shortcuts: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- registry
def register(exe: str, reg: Optional[Registry] = None, install_dir: Optional[str] = None) -> None:
    """Register ``exe`` as a PDF handler for the current user."""
    reg = reg or Registry()
    exe = os.path.abspath(exe)
    command = f'"{exe}" "%1"'
    icon = f'"{exe}",0'
    # ProgID describing PDF files opened by PDF Studio
    reg.set(reg.classes(PROG_ID), None, "PDF Document")
    reg.set(reg.classes(PROG_ID), "FriendlyTypeName", "PDF Document")
    reg.set(reg.classes(PROG_ID + r"\DefaultIcon"), None, icon)
    reg.set(reg.classes(PROG_ID + r"\shell\open\command"), None, command)
    # appear in "Open with" for .pdf without taking over the current default
    reg.set(reg.classes(r".pdf\OpenWithProgids"), PROG_ID, b"", winreg.REG_NONE)
    app_key = reg.classes(r"Applications" + "\\" + EXE_NAME)
    reg.set(app_key, "FriendlyAppName", APP_NAME)
    reg.set(app_key + r"\SupportedTypes", ".pdf", "")
    reg.set(app_key + r"\DefaultIcon", None, icon)
    reg.set(app_key + r"\shell\open\command", None, command)
    # Default Apps (Settings) capabilities
    caps = reg.path(CAPABILITIES_KEY)
    reg.set(caps, "ApplicationName", APP_NAME)
    reg.set(caps, "ApplicationDescription", "View, annotate, organize, edit and sign PDF documents.")
    reg.set(caps, "ApplicationIcon", icon)
    reg.set(caps + r"\FileAssociations", ".pdf", PROG_ID)
    reg.set(reg.path(r"Software\RegisteredApplications"), APP_NAME, CAPABILITIES_KEY)
    # Settings → Apps entry (uninstall)
    if install_dir:
        un = reg.path(UNINSTALL_KEY)
        reg.set(un, "DisplayName", APP_NAME)
        reg.set(un, "DisplayVersion", __version__)
        reg.set(un, "Publisher", PUBLISHER)
        reg.set(un, "DisplayIcon", icon)
        reg.set(un, "InstallLocation", install_dir)
        reg.set(un, "UninstallString", f'"{exe}" --uninstall')
        reg.set(un, "NoModify", 1, winreg.REG_DWORD)
        reg.set(un, "NoRepair", 1, winreg.REG_DWORD)
        reg.set(un, "EstimatedSize", _folder_kb(install_dir), winreg.REG_DWORD)
    notify_association_change()


def unregister(reg: Optional[Registry] = None) -> None:
    reg = reg or Registry()
    reg.delete_tree(reg.classes(PROG_ID))
    reg.delete_value(reg.classes(r".pdf\OpenWithProgids"), PROG_ID)
    reg.delete_tree(reg.classes(r"Applications" + "\\" + EXE_NAME))
    reg.delete_tree(reg.path(r"Software\PDF Studio"))
    reg.delete_value(reg.path(r"Software\RegisteredApplications"), APP_NAME)
    reg.delete_tree(reg.path(UNINSTALL_KEY))
    notify_association_change()


def is_registered(reg: Optional[Registry] = None) -> bool:
    reg = reg or Registry()
    return reg.get(reg.classes(PROG_ID + r"\shell\open\command")) is not None


def registered_exe(reg: Optional[Registry] = None) -> Optional[str]:
    reg = reg or Registry()
    cmd = reg.get(reg.classes(PROG_ID + r"\shell\open\command"))
    if not cmd:
        return None
    return cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]


def current_default_progid() -> Optional[str]:
    """ProgID the user chose for .pdf (Explorer's UserChoice), if any."""
    return Registry().get(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
                          r"\.pdf\UserChoice", "ProgId")


def is_default_pdf_app() -> bool:
    return current_default_progid() in (PROG_ID, f"Applications\\{EXE_NAME}")


def notify_association_change() -> None:
    if not IS_WINDOWS:
        return
    try:
        SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:  # noqa: BLE001
        pass


def open_default_apps_settings() -> None:
    """Open Settings at PDF Studio's default-apps page (Windows 11), or the
    general Default apps page."""
    from urllib.parse import quote
    uri = f"ms-settings:defaultapps?registeredAppUser={quote(APP_NAME)}"
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
    except OSError:
        os.startfile("ms-settings:defaultapps")  # type: ignore[attr-defined]


# --------------------------------------------------------------- shortcuts
def _powershell(script: str) -> str:
    flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
    # windowed (no-console) builds have no valid std handles: redirect all three
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
                         creationflags=flags)
    if out.returncode != 0:
        raise OSError(out.stderr.strip() or "PowerShell failed")
    return out.stdout.strip()


def special_folder(name: str) -> str:
    """'Desktop' or 'Programs' (Start menu) for the current user."""
    return _powershell(f"[Environment]::GetFolderPath('{name}')")


def create_shortcut(link: str, target: str, description: str = APP_NAME) -> str:
    def q(s: str) -> str:
        return s.replace("'", "''")
    _powershell(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + q(link) + "');"
        "$s.TargetPath='" + q(target) + "';"
        "$s.WorkingDirectory='" + q(os.path.dirname(target)) + "';"
        "$s.IconLocation='" + q(target) + ",0';"
        "$s.Description='" + q(description) + "';$s.Save()")
    return link


def shortcut_paths() -> List[str]:
    return [os.path.join(special_folder("Desktop"), f"{APP_NAME}.lnk"),
            os.path.join(special_folder("Programs"), f"{APP_NAME}.lnk")]


# ----------------------------------------------------------- install/remove
def install(source_dir: str, target_dir: Optional[str] = None, shortcuts: bool = True,
            reg: Optional[Registry] = None) -> InstallResult:
    """Copy the app folder, create shortcuts, register the PDF handler."""
    target_dir = os.path.abspath(target_dir or default_install_dir())
    source_dir = os.path.abspath(source_dir)
    if not os.path.isfile(os.path.join(source_dir, EXE_NAME)):
        raise FileNotFoundError(f"{EXE_NAME} not found in {source_dir}")
    if os.path.normcase(source_dir) != os.path.normcase(target_dir):
        if os.path.isdir(target_dir):
            try:
                shutil.rmtree(target_dir)
            except OSError as exc:
                raise OSError(f"Close {APP_NAME} before reinstalling ({exc.strerror or exc})") from exc
        shutil.copytree(source_dir, target_dir)
    exe = os.path.join(target_dir, EXE_NAME)
    result = InstallResult(exe=exe, install_dir=target_dir)
    if shortcuts:
        for link in shortcut_paths():
            os.makedirs(os.path.dirname(link), exist_ok=True)
            result.shortcuts.append(create_shortcut(link, exe))
    register(exe, reg, install_dir=target_dir)
    return result


def uninstall(reg: Optional[Registry] = None, remove_files: bool = True,
              shortcuts: bool = True) -> List[str]:
    """Remove shortcuts, registry entries and (after exit) the program folder."""
    removed = []
    for link in shortcut_paths() if shortcuts else []:
        try:
            os.remove(link)
            removed.append(link)
        except OSError:
            pass
    reg = reg or Registry()
    install_dir = reg.get(reg.path(UNINSTALL_KEY), "InstallLocation") or default_install_dir()
    unregister(reg)
    if remove_files and os.path.isdir(install_dir):
        # the running exe lives there: delete once this process has exited
        flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
        subprocess.Popen(["cmd", "/c", f'ping -n 3 127.0.0.1 >nul & rmdir /s /q "{install_dir}"'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        removed.append(install_dir)
    return removed


def is_installed() -> bool:
    exe = current_exe()
    return bool(exe) and os.path.normcase(os.path.dirname(exe)) == os.path.normcase(default_install_dir())


def _folder_kb(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1024
