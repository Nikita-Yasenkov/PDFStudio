"""Installation, PDF file association and single-instance hand-off.

Registry writes go to a sandbox key (HKCU\Software\PDFStudioTests) and
files to tmp_path, so the real system is never touched."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from pdf_studio.tests.conftest import pump

win = pytest.importorskip("pdf_studio.integration.windows")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

SANDBOX = "Software\PDFStudioTests\\"


@pytest.fixture
def reg():
    r = win.Registry(prefix=SANDBOX)
    yield r
    r.delete_tree(SANDBOX.rstrip("\\"))


def test_register_and_unregister(reg, tmp_path):
    exe = str(tmp_path / win.EXE_NAME)
    win.register(exe, reg, install_dir=str(tmp_path))
    assert win.is_registered(reg)
    assert win.registered_exe(reg) == exe
    cmd = reg.get(reg.classes(win.PROG_ID + r"\shell\open\command"))
    assert cmd == f'"{exe}" "%1"'
    assert reg.get(reg.classes(r".pdf\OpenWithProgids"), win.PROG_ID) is not None
    assert reg.get(reg.path(win.CAPABILITIES_KEY + r"\FileAssociations"), ".pdf") == win.PROG_ID
    assert reg.get(reg.path(win.UNINSTALL_KEY), "UninstallString") == f'"{exe}" --uninstall'
    win.unregister(reg)
    assert not win.is_registered(reg)
    assert reg.get(reg.path(win.UNINSTALL_KEY), "DisplayName") is None


def test_install_copies_app_and_registers(reg, tmp_path):
    src = tmp_path / "dist" / "PDFStudio"
    (src / "_internal").mkdir(parents=True)
    (src / win.EXE_NAME).write_bytes(b"MZ fake exe")
    (src / "_internal" / "lib.dll").write_bytes(b"x")
    target = tmp_path / "Programs" / "PDF Studio"
    result = win.install(str(src), str(target), shortcuts=False, reg=reg)
    assert result.exe == str(target / win.EXE_NAME)
    assert (target / "_internal" / "lib.dll").exists()
    assert win.registered_exe(reg) == result.exe
    assert reg.get(reg.path(win.UNINSTALL_KEY), "InstallLocation") == str(target)
    # reinstall over an existing copy works
    win.install(str(src), str(target), shortcuts=False, reg=reg)
    removed = win.uninstall(reg, remove_files=False, shortcuts=False)
    assert removed == [] and not win.is_registered(reg)


def test_install_requires_the_exe(tmp_path, reg):
    with pytest.raises(FileNotFoundError):
        win.install(str(tmp_path), str(tmp_path / "t"), shortcuts=False, reg=reg)


def test_refresh_registration_after_an_update(reg, tmp_path):
    from pdf_studio import __version__
    app = tmp_path / "PDF Studio"
    app.mkdir()
    (app / win.EXE_NAME).write_bytes(b"MZ")
    assert not win.refresh_registration(str(app), reg)  # nothing registered yet
    win.register(str(app / win.EXE_NAME), reg, install_dir=str(app))
    reg.set(reg.path(win.UNINSTALL_KEY), "DisplayVersion", "0.0.1")
    assert win.refresh_registration(str(app), reg)
    assert reg.get(reg.path(win.UNINSTALL_KEY), "DisplayVersion") == __version__
    assert not win.refresh_registration(str(tmp_path / "another copy"), reg)


def test_refresh_registration_of_a_portable_copy(reg, tmp_path):
    exe = tmp_path / win.EXE_NAME
    exe.write_bytes(b"MZ")
    win.register(str(exe), reg)  # opens PDFs, but was never installed
    assert win.refresh_registration(str(tmp_path), reg)
    assert reg.get(reg.path(win.UNINSTALL_KEY), "InstallLocation") is None


def test_signer_of_an_unsigned_file(tmp_path):
    exe = tmp_path / win.EXE_NAME
    exe.write_bytes(b"MZ not signed")
    assert win.signer(str(exe)) is None


def test_create_shortcut(tmp_path):
    exe = tmp_path / "PDFStudio.exe"
    exe.write_bytes(b"MZ")
    link = win.create_shortcut(str(tmp_path / "PDF Studio.lnk"), str(exe))
    assert os.path.exists(link) and os.path.getsize(link) > 100


def test_paths():
    assert win.default_install_dir().endswith(os.path.join("Programs", "PDF Studio"))
    assert not win.is_frozen() and win.current_exe() is None and not win.is_installed()


# ---------------------------------------------------------- single instance
def test_second_launch_hands_files_to_running_instance(qapp, tmp_path):
    from pdf_studio.ui.single_instance import InstanceServer
    name = f"PDFStudioTest-{os.getpid()}-{time.time_ns()}"
    server = InstanceServer(name)
    assert server.listening
    got = []
    server.filesReceived.connect(got.append)
    pdf = str(tmp_path / "a.pdf")
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    client = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from PyQt6.QtCore import QCoreApplication; app=QCoreApplication([]);"
         "from pdf_studio.ui.single_instance import send_to_running;"
         f"sys.exit(0 if send_to_running([{pdf!r}], {name!r}, 3000) else 1)"],
        cwd=src_dir)
    end = time.time() + 10
    while not got and time.time() < end:
        pump(20)
    assert client.wait(10) == 0
    assert got == [[os.path.abspath(pdf)]]
    server.close()


def test_no_running_instance(qapp):
    from pdf_studio.ui.single_instance import send_to_running
    assert not send_to_running(["x.pdf"], f"PDFStudioNobody-{time.time_ns()}", 200)


def test_window_receives_files(window, sample_pdf, plain_pdf):
    window.receive_files([sample_pdf, plain_pdf, "missing.pdf"])
    assert window.tabs.count() == 2


def test_integration_dialog_from_source(window):
    from pdf_studio.ui.dialogs.integration import IntegrationDialog
    dlg = IntegrationDialog(window)
    assert dlg.install_btn.isHidden()      # only the packaged app installs itself
    assert dlg.uninstall_btn.isHidden()
    dlg.close()


def test_install_command_flow(qapp, tmp_path, monkeypatch):
    """--install from a (simulated) packaged exe: no crash, no dialogs with --quiet."""
    from pdf_studio.ui import app as app_module
    messages = []
    monkeypatch.setattr(app_module, "_message", lambda *a, **k: messages.append(a))
    # running from source: explains how to install, exit code 2
    assert app_module.run_install_command(["x", "--install"]) == 2 and messages
    calls = []
    exe = tmp_path / "PDFStudio.exe"
    monkeypatch.setattr(win, "is_frozen", lambda: True)
    monkeypatch.setattr(win, "current_exe", lambda: str(exe))
    monkeypatch.setattr(win, "install", lambda src: calls.append(src) or
                        win.InstallResult(exe=str(exe), install_dir=str(tmp_path)))
    messages.clear()
    assert app_module.run_install_command(["x", "--install", "--quiet"]) == 0
    assert calls == [str(tmp_path)] and messages == []
    # failures are reported, not raised
    monkeypatch.setattr(win, "install", lambda src: (_ for _ in ()).throw(RuntimeError("boom")))
    assert app_module.run_install_command(["x", "--install", "--quiet"]) == 1
    assert "boom" in messages[-1][1]
