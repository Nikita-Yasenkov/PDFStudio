"""Build the macOS app of PDF Studio with PyInstaller.

Usage (repo root, on a Mac)::

    python -m pdf_studio.packaging.build_macos            # build
    python -m pdf_studio.packaging.build_macos --smoke    # build + self-test
    python -m pdf_studio.packaging.build_macos --smoke --notarize

Output: ``dist/PDF Studio.app`` and the disk image
``dist/PDFStudio-macos-<arch>.dmg`` (arm64 on Apple silicon, x86_64 on Intel).

Signing: with ``PDF_STUDIO_SIGN_IDENTITY`` set to a "Developer ID Application:
…" identity in the keychain, the app and the disk image are signed with it
(hardened runtime, secure timestamp); otherwise the app is ad-hoc signed.
``--notarize`` then has Apple notarize the disk image and staples the ticket,
using either ``PDF_STUDIO_NOTARY_PROFILE`` (a ``notarytool store-credentials``
profile) or an App Store Connect API key: ``PDF_STUDIO_NOTARY_KEY`` (path to
the .p8 file), ``PDF_STUDIO_NOTARY_KEY_ID`` and ``PDF_STUDIO_NOTARY_ISSUER``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

from .build_windows import ROOT, pyinstaller_args, smoke

BUNDLE_ID = "io.github.nikita-yasenkov.pdfstudio"
MIN_MACOS = "13.0"  # Qt 6.11's oldest supported macOS
COPYRIGHT = "PDF Studio contributors. Licensed under the AGPL-3.0."
# Hardened runtime exceptions (Developer ID builds): Python's ctypes/libffi may
# create small executable trampolines at run time.
ENTITLEMENTS = {"com.apple.security.cs.allow-unsigned-executable-memory": True}


def app_name() -> str:
    from pdf_studio import APP_NAME
    return APP_NAME


def make_icns(work: str) -> str:
    """Render the app mark into an .icns, inset like other macOS app icons."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QByteArray, QRectF, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import QApplication

    from pdf_studio.ui.icons import APP_ICON_SVG

    app = QApplication.instance() or QApplication(["icon"])  # noqa: F841 - painting needs one
    iconset = os.path.join(work, "PDFStudio.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            img = QImage(px, px, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            margin = px * 0.1  # Apple's icon grid keeps the tile off the edges
            renderer.render(p, QRectF(margin, margin, px - 2 * margin, px - 2 * margin))
            p.end()
            img.save(os.path.join(iconset, f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"))
    icns = os.path.join(work, "PDFStudio.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    return icns


def update_info_plist(path: str) -> dict:
    """Version, minimum macOS and the PDF document type (for Finder's Open With
    and choosing PDF Studio as the default PDF app)."""
    from pdf_studio import __version__
    with open(path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleName": app_name(),
        "CFBundleDisplayName": app_name(),
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": COPYRIGHT,
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "PDF Document",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
        }],
    })
    with open(path, "wb") as f:
        plistlib.dump(info, f)
    return info


def write_entitlements(work: str) -> str:
    path = os.path.join(work, "entitlements.plist")
    with open(path, "wb") as f:
        plistlib.dump(ENTITLEMENTS, f)
    return path


def sign_app(app: str, identity: Optional[str], entitlements: str) -> None:
    """Sign the bundle again, the way PyInstaller does: editing Info.plist
    invalidated its signature. PyInstaller already signed every binary inside."""
    if identity:
        cmd = ["codesign", "--force", "--deep", "--timestamp", "--options", "runtime",
               "--entitlements", entitlements, "--sign", identity, app]
    else:
        cmd = ["codesign", "--force", "--deep", "--sign", "-", app]
    subprocess.run(cmd, check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app], check=True)


def build(dist: str, work: str, identity: Optional[str] = None, clean: bool = True) -> str:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        sys.exit("PyInstaller is not installed: pip install pyinstaller")
    args = pyinstaller_args(app_name(), dist, work, clean)
    args += ["--icon", make_icns(work), "--osx-bundle-identifier", BUNDLE_ID]
    entitlements = write_entitlements(work)
    if identity:
        args += ["--codesign-identity", identity, "--osx-entitlements-file", entitlements]
    pyi.run(args)
    app = os.path.join(dist, f"{app_name()}.app")
    if not os.path.isdir(app):
        sys.exit(f"Build finished but {app} is missing")
    update_info_plist(os.path.join(app, "Contents", "Info.plist"))
    sign_app(app, identity, entitlements)
    return app


def make_dmg(app: str, dist: str) -> str:
    """Disk image with the app and an Applications shortcut to drag it onto."""
    dmg = os.path.join(dist, f"PDFStudio-macos-{platform.machine()}.dmg")
    stage = tempfile.mkdtemp()
    shutil.copytree(app, os.path.join(stage, os.path.basename(app)), symlinks=True)
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(stage, "LICENSE.txt"))
    if os.path.exists(dmg):
        os.remove(dmg)
    cmd = ["hdiutil", "create", "-volname", app_name(), "-srcfolder", stage,
           "-fs", "HFS+", "-format", "UDZO", "-ov", dmg]
    for attempt in range(4):  # hdiutil fails intermittently with "Resource busy"
        if subprocess.run(cmd).returncode == 0:
            return dmg
        time.sleep(5 * (attempt + 1))
    sys.exit("hdiutil could not create the disk image")


def notary_auth() -> List[str]:
    profile = os.environ.get("PDF_STUDIO_NOTARY_PROFILE")
    if profile:
        return ["--keychain-profile", profile]
    names = ("PDF_STUDIO_NOTARY_KEY", "PDF_STUDIO_NOTARY_KEY_ID", "PDF_STUDIO_NOTARY_ISSUER")
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"--notarize needs PDF_STUDIO_NOTARY_PROFILE, or {', '.join(missing)}")
    key, key_id, issuer = (os.environ[n] for n in names)
    return ["--key", key, "--key-id", key_id, "--issuer", issuer]


def notarize(path: str) -> None:
    """Have Apple notarize the disk image (and the app inside), then staple
    the ticket so Gatekeeper accepts it even offline."""
    auth = notary_auth()
    p = subprocess.run(["xcrun", "notarytool", "submit", path, *auth, "--wait",
                        "--output-format", "json"], capture_output=True, text=True)
    try:
        result = json.loads(p.stdout)
    except ValueError:
        result = {}
    print(f"Notarization: {result.get('status', 'no result')} (id {result.get('id', '-')})")
    if result.get("status") != "Accepted":
        if result.get("id"):  # Apple's list of what to fix
            subprocess.run(["xcrun", "notarytool", "log", result["id"], *auth])
        sys.exit(f"Notarization failed: {(p.stderr or p.stdout).strip()}")
    subprocess.run(["xcrun", "stapler", "staple", path], check=True)
    subprocess.run(["spctl", "--assess", "--type", "open", "--context",
                    "context:primary-signature", "--verbose=2", path], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "pdf-studio-build"))
    ap.add_argument("--smoke", action="store_true", help="run the bundled app's self-test")
    ap.add_argument("--notarize", action="store_true",
                    help="notarize and staple the disk image (needs a signing identity)")
    args = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("build_macos runs on macOS; use build_windows on Windows")
    identity = os.environ.get("PDF_STUDIO_SIGN_IDENTITY") or None
    if args.notarize and not identity:
        sys.exit("--notarize needs PDF_STUDIO_SIGN_IDENTITY (a Developer ID Application identity)")
    if args.notarize:
        notary_auth()  # fail before the long build when credentials are missing
    app = build(args.dist, args.work, identity)
    print(f"Built {app} ({'signed: ' + identity if identity else 'ad-hoc signed'})")
    if args.smoke:
        from pdf_studio.tests.fixtures.make_fixtures import ensure_fixtures
        exe = os.path.join(app, "Contents", "MacOS", app_name())
        code = smoke(exe, ensure_fixtures()["sample.pdf"])
        if code != 0:
            print(f"Smoke test FAILED (exit {code})")
            return code
        print("Smoke test passed")
    dmg = make_dmg(app, args.dist)
    if identity:
        subprocess.run(["codesign", "--force", "--timestamp", "--sign", identity, dmg], check=True)
    if args.notarize:
        notarize(dmg)
    print(f"Disk image: {dmg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
