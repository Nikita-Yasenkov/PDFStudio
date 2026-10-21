"""Help → Check for Updates: release parsing, the network side against a
local HTTP server (never GitHub), and the dialog."""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import io
import json
import os
import threading
import time
import zipfile

import pytest

from pdf_studio import __version__
from pdf_studio.core import updates
from pdf_studio.packaging.build_windows import ROOT



def _release_zip() -> bytes:
    """A small release zip, laid out like PDFStudio-win64.zip (~300 KB, so it
    arrives in several chunks)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("PDFStudio/PDFStudio.exe", b"MZ" + bytes(range(256)) * 600)
        z.writestr("PDFStudio/_internal/lib.dll", bytes(range(256)) * 600)
    return buf.getvalue()


ZIP = _release_zip()
ZIP_SHA = hashlib.sha256(ZIP).hexdigest()


def release_json(server: "Server", version: str = "0.1.3", sha: str = ZIP_SHA) -> dict:
    """Shaped like GitHub's ``releases/latest`` answer (trimmed)."""
    return {
        "tag_name": f"v{version}",
        "name": f"PDF Studio {version}",
        "html_url": f"https://github.com/{updates.REPOSITORY}/releases/tag/v{version}",
        "body": "- **Faster:** thumbnails appear sooner.\n\n### Get started\n\n"
                "**Windows**: download…\n\n**SHA-256**\n\n- `PDFStudio-win64.zip`: `…`\n",
        "assets": [
            {"name": "PDFStudio-win64.zip", "size": len(ZIP), "digest": f"sha256:{sha}",
             "browser_download_url": server.url("/download/PDFStudio-win64.zip")},
            {"name": "PDFStudio-macos-arm64.dmg", "size": 10, "digest": None,
             "browser_download_url": server.url("/download/PDFStudio-macos-arm64.dmg")},
        ],
    }


class Server:
    """A local HTTP server; ``routes`` maps a path to (status, body, headers)."""

    def __init__(self) -> None:
        self.routes = {}
        self.headers = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                server.headers.append(dict(self.headers))
                if self.path == "/hang-up":  # close the connection without an answer
                    self.close_connection = True
                    return
                status, body, headers = server.routes.get(self.path, (404, b"", {}))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}{path}"

    def serve_release(self, body: bytes = ZIP, **kw) -> str:
        self.routes["/latest"] = (200, json.dumps(release_json(self, **kw)).encode(),
                                  {"Content-Type": "application/json"})
        self.routes["/download/PDFStudio-win64.zip"] = (
            302, b"", {"Location": self.url("/files/PDFStudio-win64.zip")})
        self.routes["/files/PDFStudio-win64.zip"] = (200, body, {})
        return self.url("/latest")


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def wait_until(condition, ms: int = 5000) -> None:
    from PyQt6.QtTest import QTest
    deadline = time.monotonic() + ms / 1000
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        QTest.qWait(20)


# ------------------------------------------------------------------ core
def test_versions_compare_numerically():
    assert updates.parse_version("v0.1.10") == (0, 1, 10)
    assert updates.parse_version("0.2") == (0, 2)
    assert updates.parse_version("nightly") is None
    assert updates.is_newer("v0.1.10", "0.1.9")
    assert updates.is_newer("0.2", "0.1.9")
    assert not updates.is_newer("v0.1.2", "0.1.2")
    assert not updates.is_newer("0.1.2.0", "0.1.2")
    assert not updates.is_newer("0.1.1", "0.1.2")
    assert not updates.is_newer("nightly", "0.1.2")


def test_release_from_github_json():
    class Fake:
        def url(self, path):
            return "https://example.invalid" + path

    r = updates.parse_release(release_json(Fake()))
    assert r.version == "0.1.3"
    assert r.notes == "- **Faster:** thumbnails appear sooner."
    assert r.page_url.endswith("/releases/tag/v0.1.3")
    win = r.asset("PDFStudio-win64.zip")
    assert win.size == len(ZIP) and win.sha256 == ZIP_SHA
    assert win.url == "https://example.invalid/download/PDFStudio-win64.zip"
    assert r.asset("PDFStudio-macos-arm64.dmg").sha256 == ""
    assert r.asset(None) is None and r.asset("PDFStudio-linux.tar") is None


def test_release_needs_a_version_tag():
    with pytest.raises(ValueError):
        updates.parse_release({"tag_name": "nightly"})
    with pytest.raises(ValueError):
        updates.parse_release(["not", "a", "release"])


def test_whats_new_is_the_changelog_section_of_published_notes(tmp_path):
    """The dialog shows exactly the CHANGELOG section that release_notes.py
    put in front of the install steps."""
    spec = importlib.util.spec_from_file_location(
        "release_notes", os.path.join(ROOT, "scripts", "release_notes.py"))
    release_notes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_notes)
    asset = tmp_path / "PDFStudio-win64.zip"
    asset.write_bytes(b"zip")
    for notarized in (False, True):
        body = release_notes.notes(__version__, [str(asset)], notarized)
        assert updates.whats_new(body) == release_notes.changelog_section(__version__)


def test_asset_names_match_the_release_workflow():
    assert updates.asset_name("win32") == "PDFStudio-win64.zip"
    assert updates.asset_name("darwin", "arm64") == "PDFStudio-macos-arm64.dmg"
    assert updates.asset_name("darwin", "x86_64") == "PDFStudio-macos-x86_64.dmg"
    assert updates.asset_name("linux") is None
    with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
        workflow = f.read()
    for uploaded in ("dist/PDFStudio-win64.zip", "dist/PDFStudio-macos-${{ matrix.arch }}.dmg",
                     "arch: arm64", "arch: x86_64"):
        assert uploaded in workflow


def test_download_names(tmp_path):
    assert updates.download_name("PDFStudio-win64.zip", "0.1.3") == "PDFStudio-0.1.3-win64.zip"
    assert updates.download_name("PDFStudio-macos-arm64.dmg", "0.2.0") == \
        "PDFStudio-0.2.0-macos-arm64.dmg"
    first = updates.unique_path(str(tmp_path), "PDFStudio-0.1.3-win64.zip")
    assert os.path.basename(first) == "PDFStudio-0.1.3-win64.zip"
    open(first, "wb").close()
    second = updates.unique_path(str(tmp_path), "PDFStudio-0.1.3-win64.zip")
    assert os.path.basename(second) == "PDFStudio-0.1.3-win64 (2).zip"


# --------------------------------------------------------------- network
def test_https_is_available(qapp):
    from pdf_studio.ui.updates import https_status
    ok, detail = https_status()
    assert ok, detail


def _check(qapp, url):
    from pdf_studio.ui.updates import UpdateChecker, network_manager
    from PyQt6.QtCore import QObject
    owner = QObject()
    checker = UpdateChecker(network_manager(owner), url, owner)
    results = []
    checker.found.connect(results.append)
    checker.failed.connect(results.append)
    checker.start()
    wait_until(lambda: results)
    assert not checker.running
    return results[0]


def test_checker_finds_the_latest_release(qapp, server):
    release = _check(qapp, server.serve_release())
    assert isinstance(release, updates.Release) and release.version == "0.1.3"
    assert server.headers[-1]["User-Agent"] == f"PDFStudio/{__version__}"
    assert server.headers[-1]["Accept"] == "application/vnd.github+json"


@pytest.mark.parametrize("status, body, message", [
    (403, b"{}", "limiting requests"),
    (404, b"{}", "no release"),
    (500, b"", "HTTP 500"),
    (200, b"<html>", "couldn't be read"),
    (200, b'{"tag_name": "nightly"}', "couldn't be read"),
])
def test_checker_explains_failures(qapp, server, status, body, message):
    server.routes["/latest"] = (status, body, {})
    assert message in _check(qapp, server.url("/latest"))


def test_checker_when_the_connection_fails(qapp, server):
    assert "Check your internet connection" in _check(qapp, server.url("/hang-up"))


def _download(qapp, tmp_path, asset):
    from pdf_studio.ui.updates import Download, network_manager
    from PyQt6.QtCore import QObject
    owner = QObject()
    path = str(tmp_path / "PDFStudio-0.1.3-win64.zip")
    job = Download(network_manager(owner), asset, path, owner)
    results, progress = [], []
    job.finished.connect(results.append)
    job.failed.connect(results.append)
    job.progress.connect(lambda got, total: progress.append((got, total)))
    job.start()
    return owner, job, path, results, progress  # keep owner: it owns the job


def test_download_follows_redirects_and_checks_the_sha256(qapp, server, tmp_path):
    server.serve_release()
    asset = updates.Asset("PDFStudio-win64.zip", server.url("/download/PDFStudio-win64.zip"),
                          len(ZIP), ZIP_SHA)
    _owner, job, path, results, progress = _download(qapp, tmp_path, asset)
    wait_until(lambda: results)
    assert results == [path]
    assert open(path, "rb").read() == ZIP
    assert not os.path.exists(path + ".part")
    assert progress[-1] == (len(ZIP), len(ZIP))


def test_damaged_download_is_discarded(qapp, server, tmp_path):
    server.serve_release()
    asset = updates.Asset("PDFStudio-win64.zip", server.url("/download/PDFStudio-win64.zip"),
                          len(ZIP), "0" * 64)
    _owner, job, path, results, _ = _download(qapp, tmp_path, asset)
    wait_until(lambda: results)
    assert "damaged" in results[0]
    assert os.listdir(tmp_path) == []


def test_download_without_digest_checks_the_size(qapp, server, tmp_path):
    server.serve_release()
    asset = updates.Asset("PDFStudio-win64.zip", server.url("/download/PDFStudio-win64.zip"),
                          len(ZIP) + 1)
    _owner, job, path, results, _ = _download(qapp, tmp_path, asset)
    wait_until(lambda: results)
    assert "damaged" in results[0]
    assert os.listdir(tmp_path) == []


def test_cancelled_download_leaves_nothing(qapp, server, tmp_path):
    server.serve_release()
    asset = updates.Asset("PDFStudio-win64.zip", server.url("/download/PDFStudio-win64.zip"),
                          len(ZIP), ZIP_SHA)
    _owner, job, path, results, _ = _download(qapp, tmp_path, asset)
    job.abort()
    from PyQt6.QtTest import QTest
    QTest.qWait(200)
    assert results == [] and not job.running
    assert os.listdir(tmp_path) == []


# ---------------------------------------------------------------- dialog
@pytest.fixture
def dialog_for(qapp, tmp_path):
    from PyQt6.QtWidgets import QWidget
    from pdf_studio.ui.dialogs.updates import UpdateDialog
    host = QWidget()
    made = []

    def make(url, current="0.1.2", asset_name="PDFStudio-win64.zip", **kw):
        dlg = UpdateDialog(host, url=url, folder=str(tmp_path / "Downloads"), current=current,
                           asset_name=asset_name, **kw)
        made.append(dlg)
        return dlg

    yield make
    for dlg in made:
        if dlg.download is not None:
            dlg.download.abort()
    host.deleteLater()


def test_dialog_offers_the_new_version_and_downloads_it(dialog_for, server, tmp_path):
    dlg = dialog_for(server.serve_release())
    dlg.show()
    dlg.check()
    assert dlg.state == "checking" and dlg.close_btn.text() == "Cancel"
    wait_until(lambda: dlg.state != "checking")
    assert dlg.state == "available"
    assert dlg.heading.text() == "PDF Studio 0.1.3 is available"
    assert "You have version 0.1.2" in dlg.detail.text()
    assert "thumbnails appear sooner" in dlg.notes.toPlainText()
    assert "Get started" not in dlg.notes.toPlainText()
    assert dlg.primary.text() == "Download (0.3 MB)" and dlg.page_btn.isVisibleTo(dlg)
    dlg.primary.click()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "downloaded", dlg.detail.text()
    saved = tmp_path / "Downloads" / "PDFStudio-0.1.3-win64.zip"
    assert dlg.saved == str(saved) and saved.read_bytes() == ZIP
    assert "PDFStudio-0.1.3-win64.zip" in dlg.detail.text()
    assert dlg.primary.isVisibleTo(dlg) and not dlg.page_btn.isVisibleTo(dlg)


def test_dialog_when_up_to_date(dialog_for, server):
    url = server.serve_release()
    dlg = dialog_for(url, current="0.1.3")
    dlg.check()
    wait_until(lambda: dlg.state != "checking")
    assert dlg.state == "up_to_date"
    assert dlg.detail.text() == "PDF Studio 0.1.3 is the latest release."
    assert not dlg.primary.isVisibleTo(dlg)
    newer = dialog_for(url, current="0.2.0")
    newer.check()
    wait_until(lambda: newer.state != "checking")
    assert newer.state == "up_to_date"
    assert "the latest release is 0.1.3" in newer.detail.text()


def test_dialog_without_a_download_for_this_computer(dialog_for, server):
    dlg = dialog_for(server.serve_release(), asset_name="")
    dlg.check()
    wait_until(lambda: dlg.state != "checking")
    assert dlg.state == "available"
    assert dlg.primary.text() == "Open Release Page"
    assert not dlg.page_btn.isVisibleTo(dlg)


def test_dialog_retries_a_failed_check(dialog_for, server):
    server.routes["/latest"] = (403, b"{}", {})
    dlg = dialog_for(server.url("/latest"))
    dlg.check()
    wait_until(lambda: dlg.state != "checking")
    assert dlg.state == "check_failed" and "limiting" in dlg.detail.text()
    assert dlg.primary.text() == "Try Again"
    server.serve_release()
    dlg.primary.click()
    wait_until(lambda: dlg.state != "checking")
    assert dlg.state == "available"


def test_damaged_download_offers_retry(dialog_for, server, tmp_path):
    dlg = dialog_for(server.serve_release(sha="0" * 64))
    dlg.check()
    wait_until(lambda: dlg.state == "available")
    dlg.start_download()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "download_failed" and "damaged" in dlg.detail.text()
    assert dlg.primary.text() == "Try Again"
    assert os.listdir(tmp_path / "Downloads") == []


def test_closing_cancels_a_check_but_not_a_download(dialog_for, server, tmp_path):
    dlg = dialog_for(server.serve_release())
    dlg.show()
    dlg.check()
    dlg.close_btn.click()
    assert not dlg.isVisible() and not dlg.busy
    dlg.check()
    wait_until(lambda: dlg.state == "available")
    dlg.show()
    dlg.start_download()
    dlg.close_btn.click()  # "Hide"
    assert not dlg.isVisible()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "downloaded" and dlg.isVisible()  # back when done


def test_cancel_download_returns_to_the_offer(dialog_for, server, tmp_path):
    dlg = dialog_for(server.serve_release())
    dlg.check()
    wait_until(lambda: dlg.state == "available")
    dlg.start_download()
    assert dlg.primary.text() == "Cancel Download"
    dlg.primary.click()
    assert dlg.state == "available" and not dlg.busy
    assert os.listdir(tmp_path / "Downloads") == []


def test_help_menu_check_for_updates(window, monkeypatch):
    from pdf_studio.ui.dialogs.updates import UpdateDialog
    checks = []
    monkeypatch.setattr(UpdateDialog, "check", lambda self: checks.append(self))
    help_actions = window.help_menu.actions()
    assert window.actions["check_updates"] in help_actions
    assert help_actions.index(window.actions["check_updates"]) == \
        help_actions.index(window.actions["about"]) - 1
    window.actions["check_updates"].trigger()
    window.actions["check_updates"].trigger()
    assert len(checks) == 2 and checks[0] is checks[1]  # one dialog, reused
    assert checks[0].isVisible()
    checks[0].close()


# ------------------------------------------------- installing in place
@pytest.fixture
def in_place(dialog_for, server, tmp_path, monkeypatch):
    """A dialog for a copy that can replace itself (packaged, on Windows)."""
    from pdf_studio.integration import windows as win
    signers = {}
    monkeypatch.setattr(win, "signer", lambda path: signers.get(os.path.basename(
        os.path.dirname(path))))

    def make(**kw):
        dlg = dialog_for(server.serve_release(**kw), app_dir=str(tmp_path / "app"),
                         staging=str(tmp_path / "staging"))
        dlg.signers = signers  # folder name → signer subject
        dlg.check()
        wait_until(lambda: dlg.state == "available")
        return dlg

    return make


def test_update_is_unpacked_and_ready_to_install(in_place, tmp_path):
    dlg = in_place()
    assert dlg.primary.text() == "Update (0.3 MB)"
    dlg.primary.click()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "ready", dlg.detail.text()
    assert dlg.heading.text() == "PDF Studio 0.1.3 is ready to install"
    assert dlg.primary.text() == "Restart Now" and dlg.close_btn.text() == "Later"
    assert dlg.new_exe == str(tmp_path / "staging" / "0.1.3" / "app" / "PDFStudio" /
                              "PDFStudio.exe")
    assert os.path.isfile(dlg.new_exe)
    assert not os.path.exists(tmp_path / "Downloads")
    requested = []
    dlg.restartRequested.connect(lambda exe, app_dir: requested.append((exe, app_dir)))
    dlg.primary.click()
    assert requested == [(dlg.new_exe, str(tmp_path / "app"))]


def test_update_from_another_publisher_is_refused(in_place):
    dlg = in_place()
    dlg.signers.update({"app": "CN=PDF Studio", "PDFStudio": "CN=Someone Else"})
    dlg.start_download()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "download_failed"
    assert dlg.heading.text() == "Couldn't install the update"
    assert "isn't signed by the same publisher" in dlg.detail.text()
    assert dlg.new_exe is None


def test_update_that_is_not_a_zip_is_refused(in_place):
    body = b"not a zip at all"
    dlg = in_place(body=body, sha=hashlib.sha256(body).hexdigest())
    dlg.start_download()
    wait_until(lambda: dlg.state != "downloading")
    assert dlg.state == "download_failed" and "couldn't be unpacked" in dlg.detail.text()


def test_restart_to_update_closes_documents_and_hands_over(window, sample_pdf, monkeypatch):
    from pdf_studio.integration import updater
    calls = []
    monkeypatch.setattr(updater, "launch_update", lambda *a: calls.append(a))
    window.open_path(sample_pdf)
    window.restart_to_update("new.exe", "app-folder")
    (exe, app_dir, pid, files), = calls
    assert (exe, app_dir, pid) == ("new.exe", "app-folder", os.getpid())
    assert [os.path.normcase(f) for f in files] == [os.path.normcase(sample_pdf)]
    assert window.views() == [] and not window.isVisible()


def test_restart_to_update_waits_for_unsaved_changes(window, sample_pdf, monkeypatch):
    from pdf_studio.integration import updater
    from pdf_studio.ui.dialogs import prompts
    calls = []
    monkeypatch.setattr(updater, "launch_update", lambda *a: calls.append(a))
    monkeypatch.setattr(prompts, "ask_close_choice", lambda *a: prompts.CloseChoice.CANCEL)
    window.open_path(sample_pdf)
    window.rotate_pages(90)
    assert window.view.session.dirty
    window.restart_to_update("new.exe", "app-folder")
    assert calls == [] and len(window.views()) == 1 and window.isVisible()
