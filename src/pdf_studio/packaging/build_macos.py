"""Build the macOS app of PDF Studio with PyInstaller.

Usage (repo root, on a Mac)::

    python -m pdf_studio.packaging.build_macos            # build
    python -m pdf_studio.packaging.build_macos --smoke    # build + self-test

Output: ``dist/PDF Studio.app`` and the disk image
``dist/PDFStudio-macos-<arch>.dmg`` (arm64 on Apple silicon, x86_64 on Intel).
The app is ad-hoc signed; notarized releases need an Apple Developer ID.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

from .build_windows import ROOT, pyinstaller_args, smoke

BUNDLE_ID = "io.github.nikita-yasenkov.pdfstudio"
MIN_MACOS = "13.0"  # Qt 6.11's oldest supported macOS
COPYRIGHT = "PDF Studio contributors. Licensed under the AGPL-3.0."


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


def sign(app: str) -> None:
    """Ad-hoc sign again: editing Info.plist invalidated PyInstaller's signature."""
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app], check=True)


def build(dist: str, work: str, clean: bool = True) -> str:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        sys.exit("PyInstaller is not installed: pip install pyinstaller")
    args = pyinstaller_args(app_name(), dist, work, clean)
    args += ["--icon", make_icns(work), "--osx-bundle-identifier", BUNDLE_ID]
    pyi.run(args)
    app = os.path.join(dist, f"{app_name()}.app")
    if not os.path.isdir(app):
        sys.exit(f"Build finished but {app} is missing")
    update_info_plist(os.path.join(app, "Contents", "Info.plist"))
    sign(app)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "pdf-studio-build"))
    ap.add_argument("--smoke", action="store_true", help="run the bundled app's self-test")
    args = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("build_macos runs on macOS; use build_windows on Windows")
    app = build(args.dist, args.work)
    print(f"Built {app}")
    if args.smoke:
        from pdf_studio.tests.fixtures.make_fixtures import ensure_fixtures
        exe = os.path.join(app, "Contents", "MacOS", app_name())
        code = smoke(exe, ensure_fixtures()["sample.pdf"])
        if code != 0:
            print(f"Smoke test FAILED (exit {code})")
            return code
        print("Smoke test passed")
    print(f"Disk image: {make_dmg(app, args.dist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
