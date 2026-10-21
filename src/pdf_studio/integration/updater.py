"""Install a downloaded update over the app's own folder (stdlib only).

1. The running app unpacks the new version's zip with :func:`unpack` and
   starts the new ``PDFStudio.exe`` from there with :func:`launch_update`,
   then quits.
2. The new exe (``--apply-update``, see ``ui/app.py``) calls
   :func:`apply_update`: it waits for the old app to exit, copies itself next
   to the app folder and swaps the two with renames, so a failure leaves the
   old version in place. Then it starts the updated app with the documents
   that were open.

Older versions start newer ones with :func:`command`'s arguments, so later
versions must keep accepting them.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import Callable, List, Optional, Sequence, Tuple

from .. import APP_NAME

EXE_NAME = "PDFStudio.exe"
ARG = "--apply-update"


def staging_root() -> str:
    """Where downloads are unpacked; the updated app removes it."""
    return os.path.join(tempfile.gettempdir(), f"{APP_NAME} update")


def unpack(zip_path: str, folder: str) -> str:
    """Extract the release zip into ``folder`` (emptied first) and return the
    app folder in it, the one holding PDFStudio.exe."""
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)  # zipfile never writes outside ``folder``
    for candidate in [folder] + [os.path.join(folder, d) for d in sorted(os.listdir(folder))]:
        if os.path.isfile(os.path.join(candidate, EXE_NAME)):
            return candidate
    raise FileNotFoundError(f"{EXE_NAME} isn't in the download")


def can_replace(app_dir: str) -> bool:
    """Whether this user may swap ``app_dir``: renaming it needs write access
    to the folder that holds it."""
    try:
        probe = tempfile.mkdtemp(prefix=".pdf-studio-", dir=os.path.dirname(app_dir))
    except OSError:
        return False
    os.rmdir(probe)
    return True


def same_publisher(current: Optional[str], new: Optional[str]) -> bool:
    """A signed app only installs updates signed by the same publisher
    (signer subjects; None = unsigned)."""
    return current is None or current == new


# ------------------------------------------------------------- hand-over
def command(new_exe: str, app_dir: str, pid: int, files: Sequence[str]) -> List[str]:
    return [new_exe, ARG, app_dir, "--wait", str(pid), *files]


def parse_command(argv: Sequence[str]) -> Tuple[str, int, List[str]]:
    """(app folder, process to wait for, files to reopen) from :func:`command`'s
    arguments; ValueError when they don't fit."""
    try:
        i = list(argv).index(ARG)
        app_dir, rest = argv[i + 1], list(argv[i + 2:])
        pid = 0
        if rest[:1] == ["--wait"]:
            pid, rest = int(rest[1]), rest[2:]
    except (IndexError, ValueError) as exc:
        raise ValueError(f"expected {ARG} FOLDER --wait PID [FILE…]") from exc
    return app_dir, pid, rest


def launch_update(new_exe: str, app_dir: str, pid: int, files: Sequence[str]) -> None:
    """Start the new version to replace ``app_dir`` once process ``pid`` exits.
    It runs from its own folder: a process working inside ``app_dir`` (the
    shortcuts start the app there) would stop the folder being renamed."""
    subprocess.Popen(command(new_exe, app_dir, pid, [os.path.abspath(f) for f in files]),
                     cwd=os.path.dirname(new_exe), close_fds=True)


def launch(exe: str, files: Sequence[str]) -> None:
    subprocess.Popen([exe, *files], cwd=os.path.dirname(exe), close_fds=True)


# ---------------------------------------------------------------- update
def apply_update(source: str, app_dir: str, pid: int, timeout: float = 60.0) -> str:
    """Wait for the old app (``pid``) to exit, then replace ``app_dir`` with
    ``source``; returns the updated exe."""
    if not os.path.isfile(os.path.join(app_dir, EXE_NAME)):
        raise FileNotFoundError(f"{app_dir} is not a {APP_NAME} folder")
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(app_dir)):
        raise ValueError("the update must run from its own folder")
    if not wait_for_exit(pid, timeout):
        raise OSError(f"{APP_NAME} didn't close")
    replace_folder(source, app_dir)
    return os.path.join(app_dir, EXE_NAME)


def replace_folder(source: str, target: str, attempts: int = 20) -> None:
    """Make ``target`` a copy of ``source``. The copy is made beside it and
    swapped in with two renames, so a failure (a file still in use, a full
    disk) leaves ``target`` as it was."""
    new, old = target + ".new", target + ".old"
    for leftover in (new, old):
        shutil.rmtree(leftover, ignore_errors=True)
    if os.path.exists(old):  # still locked from an earlier update
        old = f"{old}-{os.getpid()}"
    try:
        shutil.copytree(source, new)
    except OSError:
        shutil.rmtree(new, ignore_errors=True)
        raise
    if os.path.isdir(target):
        try:  # antivirus scans or a slow exit can hold files for a moment
            _retry(lambda: os.rename(target, old), attempts)
        except OSError as exc:
            shutil.rmtree(new, ignore_errors=True)
            raise OSError(f"Files in {target} are in use. Close {APP_NAME} and try again "
                          f"({exc.strerror or exc}).") from exc
    try:
        os.rename(new, target)
    except OSError:
        if os.path.isdir(old):
            os.rename(old, target)
        shutil.rmtree(new, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _retry(action: Callable[[], None], attempts: int, delay: float = 0.25) -> None:
    for i in range(attempts):
        try:
            action()
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def wait_for_exit(pid: int, timeout: float) -> bool:
    if pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        synchronize, wait_object_0 = 0x00100000, 0
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:  # already gone
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == wait_object_0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # exists, owned by someone else
            pass
        time.sleep(0.05)
    return False


def remove_leftovers(app_dir: str) -> None:
    """Delete what an update leaves behind: the unpacked download and the
    previous version's folder. Errors are ignored; the next start retries."""
    here = os.path.normcase(os.path.abspath(sys.executable))
    root = staging_root()
    if not here.startswith(os.path.normcase(root) + os.sep):
        shutil.rmtree(root, ignore_errors=True)
    for path in glob.glob(glob.escape(app_dir) + ".old*") + [app_dir + ".new"]:
        shutil.rmtree(path, ignore_errors=True)
