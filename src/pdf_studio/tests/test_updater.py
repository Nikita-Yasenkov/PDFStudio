"""Installing a downloaded update over the app folder (integration/updater.py).
Everything happens in tmp_path; no real app is replaced."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import zipfile

import pytest

from pdf_studio.integration import updater


def make_app(folder, marker: str) -> str:
    os.makedirs(os.path.join(folder, "_internal"), exist_ok=True)
    for name in (updater.EXE_NAME, os.path.join("_internal", "lib.dll")):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(marker)
    return str(folder)


def version_in(folder) -> str:
    with open(os.path.join(folder, updater.EXE_NAME), encoding="utf-8") as f:
        return f.read()


def sleeper(seconds: float) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it on POSIX
    return proc


def test_unpack_finds_the_app_folder(tmp_path):
    for top in ("PDFStudio/", ""):
        archive = tmp_path / "update.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(top + updater.EXE_NAME, "new")
            z.writestr(top + "_internal/lib.dll", "new")
        app = updater.unpack(str(archive), str(tmp_path / "unpacked"))
        assert version_in(app) == "new"
        assert os.path.isfile(os.path.join(app, "_internal", "lib.dll"))


def test_unpack_needs_the_app(tmp_path):
    archive = tmp_path / "other.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", "hello")
    with pytest.raises(FileNotFoundError):
        updater.unpack(str(archive), str(tmp_path / "unpacked"))


def test_hand_over_arguments_round_trip():
    argv = updater.command(r"C:\Temp\PDFStudio.exe", r"C:\Programs\PDF Studio", 42,
                           [r"C:\My files\a.pdf", "b.pdf"])
    assert updater.parse_command(argv) == (r"C:\Programs\PDF Studio", 42,
                                           [r"C:\My files\a.pdf", "b.pdf"])
    assert updater.parse_command(["exe", updater.ARG, "folder"]) == ("folder", 0, [])
    for bad in (["exe", updater.ARG], ["exe", updater.ARG, "folder", "--wait", "soon"]):
        with pytest.raises(ValueError):
            updater.parse_command(bad)


def test_launch_update_runs_outside_the_app_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    new_exe = str(tmp_path / "staging" / updater.EXE_NAME)
    updater.launch_update(new_exe, str(tmp_path / "app"), 7, ["a.pdf"])
    args, kw = calls[0]
    assert args == [new_exe, updater.ARG, str(tmp_path / "app"), "--wait", "7",
                    os.path.abspath("a.pdf")]
    assert kw["cwd"] == os.path.dirname(new_exe)


def test_replace_folder_swaps_in_the_new_version(tmp_path):
    new = make_app(tmp_path / "new", "new")
    app = make_app(tmp_path / "app", "old")
    open(os.path.join(app, "only-in-old.txt"), "w").close()
    updater.replace_folder(new, app)
    assert version_in(app) == "new"
    assert not os.path.exists(os.path.join(app, "only-in-old.txt"))
    assert sorted(os.listdir(tmp_path)) == ["app", "new"]  # no .new or .old left


def test_replace_folder_creates_a_missing_folder(tmp_path):
    new = make_app(tmp_path / "new", "new")
    updater.replace_folder(new, str(tmp_path / "app"))
    assert version_in(tmp_path / "app") == "new"


def test_files_in_use_keep_the_old_version(tmp_path, monkeypatch):
    new = make_app(tmp_path / "new", "new")
    app = make_app(tmp_path / "app", "old")
    rename = os.rename

    def locked(src, dst):
        if src == app:
            raise PermissionError(32, "The process cannot access the file")
        rename(src, dst)

    monkeypatch.setattr(os, "rename", locked)
    with pytest.raises(OSError, match="in use"):
        updater.replace_folder(new, app, attempts=2)
    assert version_in(app) == "old"
    assert sorted(os.listdir(tmp_path)) == ["app", "new"]


def test_a_failed_swap_restores_the_old_version(tmp_path, monkeypatch):
    new = make_app(tmp_path / "new", "new")
    app = make_app(tmp_path / "app", "old")
    rename = os.rename

    def fail_second(src, dst):
        if src.endswith(".new"):
            raise OSError(5, "Access is denied")
        rename(src, dst)

    monkeypatch.setattr(os, "rename", fail_second)
    with pytest.raises(OSError):
        updater.replace_folder(new, app)
    assert version_in(app) == "old"
    assert sorted(os.listdir(tmp_path)) == ["app", "new"]


def test_wait_for_exit():
    assert updater.wait_for_exit(0, 1)
    proc = sleeper(0.5)
    assert not updater.wait_for_exit(proc.pid, 0.05)
    assert updater.wait_for_exit(proc.pid, 10)


def test_apply_update(tmp_path):
    new = make_app(tmp_path / "new", "new")
    app = make_app(tmp_path / "app", "old")
    proc = sleeper(0.3)  # the old app, about to exit
    exe = updater.apply_update(new, app, proc.pid)
    assert exe == os.path.join(app, updater.EXE_NAME) and version_in(app) == "new"


def test_apply_update_checks_its_folders(tmp_path):
    new = make_app(tmp_path / "new", "new")
    with pytest.raises(FileNotFoundError):
        updater.apply_update(new, str(tmp_path / "not-an-app"), 0)
    with pytest.raises(ValueError):
        updater.apply_update(new, new, 0)


def test_apply_update_gives_up_when_the_app_stays_open(tmp_path):
    new = make_app(tmp_path / "new", "new")
    app = make_app(tmp_path / "app", "old")
    proc = sleeper(30)
    try:
        with pytest.raises(OSError, match="didn't close"):
            updater.apply_update(new, app, proc.pid, timeout=0.1)
    finally:
        proc.kill()
    assert version_in(app) == "old"


def test_same_publisher():
    assert updater.same_publisher(None, None)
    assert updater.same_publisher(None, "CN=Someone")  # unsigned today: anything goes
    assert updater.same_publisher("CN=Me", "CN=Me")
    assert not updater.same_publisher("CN=Me", None)
    assert not updater.same_publisher("CN=Me", "CN=Someone Else")


def test_can_replace(tmp_path):
    assert updater.can_replace(str(tmp_path / "app"))
    assert os.listdir(tmp_path) == []  # the probe is gone


def test_remove_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "staging_root", lambda: str(tmp_path / "staging"))
    app = make_app(tmp_path / "app", "new")
    for leftover in ("app.old", "app.old-12", "app.new", os.path.join("staging", "0.1.3")):
        os.makedirs(tmp_path / leftover)
    updater.remove_leftovers(app)
    assert os.listdir(tmp_path) == ["app"]


def test_apply_update_command_rejects_bad_arguments(qapp, tmp_path, monkeypatch):
    import tempfile
    from pdf_studio.ui import app as app_module
    shown = []
    monkeypatch.setattr(app_module, "_message", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))  # the log
    assert app_module.run_apply_update(["exe", updater.ARG]) == 2
    # running from source: only the packaged app replaces itself
    assert app_module.run_apply_update(["exe", updater.ARG, str(tmp_path), "--wait", "0"]) == 2
    assert "packaged app" in shown[-1][1]
