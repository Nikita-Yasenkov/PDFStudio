"""Build a Windows one-folder bundle of PDF Studio with PyInstaller.

Usage (repo root; ``scripts/build_pdf_studio.ps1`` wraps this in a clean uv env)::

    python -m pdf_studio.packaging.build_windows            # build
    python -m pdf_studio.packaging.build_windows --smoke    # build + self-test

Output: ``dist/PDFStudio/PDFStudio.exe``. Only the Qt modules the app uses are
bundled; unrelated heavy packages present in the environment are excluded,
and no ``.env``/settings/secrets are collected.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.abspath(os.path.join(SRC, ".."))
NAME = "PDFStudio"

EXCLUDES = [
    # heavy packages that may be present in a shared development environment
    "torch", "torchvision", "cv2", "numpy", "PIL", "openai", "matplotlib", "pandas",
    "scipy", "tkinter", "_tkinter", "dotenv", "pynput", "mss", "simple_lama_inpainting",
    # Qt modules the app never imports
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
    "PyQt6.QtMultimedia", "PyQt6.QtSql", "PyQt6.QtBluetooth",
    "PyQt6.QtPositioning", "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.Qt3DCore",
    # our own test suite
    "pdf_studio.tests", "pytest",
]


def make_version_file(path: str) -> str:
    """Windows version resource: Properties → Details shows name and version."""
    from pdf_studio import APP_NAME, __version__
    from pdf_studio.integration.windows import PUBLISHER

    nums = tuple((int(n) for n in re.findall(r"\d+", __version__)[:4]))
    nums += (0,) * (4 - len(nums))
    strings = {
        "CompanyName": PUBLISHER,
        "FileDescription": APP_NAME,
        "FileVersion": __version__,
        "InternalName": NAME,
        "LegalCopyright": "PDF Studio contributors. Licensed under the AGPL-3.0.",
        "OriginalFilename": f"{NAME}.exe",
        "ProductName": APP_NAME,
        "ProductVersion": __version__,
    }
    table = ",\n".join(f"        StringStruct({k!r}, {v!r})" for k, v in strings.items())
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
{table}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ]
)
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def make_icon(path: str) -> str:
    """Render the app's SVG mark into a multi-size .ico."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["icon"])
    from PyQt6.QtCore import QByteArray, QRectF, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    from pdf_studio.ui.icons import APP_ICON_SVG

    img = QImage(256, 256, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(APP_ICON_SVG.encode())).render(p, QRectF(0, 0, 256, 256))
    p.end()
    if not img.save(path, "ICO"):
        img.save(path.replace(".ico", ".png"), "PNG")
        return path.replace(".ico", ".png")
    del app
    return path


def pyinstaller_args(name: str, dist: str, work: str, clean: bool = True) -> List[str]:
    """PyInstaller arguments shared by the Windows and macOS builds."""
    os.makedirs(work, exist_ok=True)
    entry = os.path.join(work, "pdf_studio_entry.py")
    with open(entry, "w", encoding="utf-8") as f:
        f.write("import sys\nfrom pdf_studio.ui.app import main\nsys.exit(main())\n")
    args = [
        entry,
        "--name", name,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--noupx",  # packed executables are a classic antivirus false-positive trigger
        "--paths", SRC,
        "--distpath", dist,
        "--workpath", os.path.join(work, "pyi"),
        "--specpath", work,
        "--hidden-import", "PyQt6.QtSvg",
        "--hidden-import", "PyQt6.QtNetwork",
        "--collect-submodules", "pdf_studio",
        "--collect-binaries", "pymupdf",
        "--collect-data", "pymupdf",
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    if clean:
        args.append("--clean")
    return args


def build(dist: str, work: str, clean: bool = True) -> str:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        sys.exit("PyInstaller is not installed: pip install pyinstaller")
    args = pyinstaller_args(NAME, dist, work, clean)
    args += ["--icon", make_icon(os.path.join(work, "pdf_studio.ico")),
             "--version-file", make_version_file(os.path.join(work, "version_info.txt"))]
    pyi.run(args)
    exe = os.path.join(dist, NAME, f"{NAME}.exe")
    if not os.path.exists(exe):
        sys.exit(f"Build finished but {exe} is missing")
    remove_conflicting_dlls(os.path.join(dist, NAME, "_internal"))
    return exe


def remove_conflicting_dlls(internal: str) -> list:
    """Drop DLLs that shadow Windows' own copies, and files the app never uses.

    Qt 6 on Windows uses the system ICU (``C:/Windows/System32/icuuc.dll``).
    Conda environments put an older ICU with the *same file name* on PATH and
    PyInstaller bundles it; QtCore then fails with "procedure could not be
    found". Removing the bundled copies lets Windows supply the right one.
    """
    removed = []
    if not os.path.isdir(internal):
        return removed
    for name in os.listdir(internal):
        low = name.lower()
        if low.startswith(("icuuc", "icudt", "icuin")) and low.endswith(".dll"):
            os.remove(os.path.join(internal, name))
            removed.append(name)
    import shutil
    # files the app never uses (it paints with QPainter, renders PDFs with MuPDF)
    unused = [
        "_tcl_data", "_tk_data",
        os.path.join("PyQt6", "Qt6", "bin", "opengl32sw.dll"),   # software OpenGL
        os.path.join("PyQt6", "Qt6", "bin", "Qt6Pdf.dll"),       # Qt's own PDF engine
        os.path.join("PyQt6", "Qt6", "plugins", "imageformats", "qpdf.dll"),
        os.path.join("PyQt6", "Qt6", "translations"),
        os.path.join("pymupdf", "mupdf-devel"),                  # headers / .lib files
    ]
    for rel in unused:
        path = os.path.join(internal, rel)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(rel)
        elif os.path.isfile(path):
            os.remove(path)
            removed.append(rel)
    if removed:
        print("Removed from bundle:", ", ".join(removed))
    return removed


def smoke(exe: str, pdf: str) -> int:
    log = os.path.join(tempfile.mkdtemp(), "smoke.txt")
    try:
        proc = subprocess.run([exe, "--smoke", "--smoke-log", log, pdf], timeout=180)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        # a frozen app that fails at start-up shows a blocking error box
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", os.path.basename(exe)], capture_output=True)
        code = 124
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            print(f.read())
    else:
        print("(no smoke log: the app did not start — run the exe by hand to see the error)")
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--work", default=os.path.join(ROOT, "build", "pdf-studio"))
    ap.add_argument("--smoke", action="store_true", help="run the bundled exe's self-test")
    ap.add_argument("--no-clean", action="store_true")
    args = ap.parse_args()
    exe = build(args.dist, args.work, clean=not args.no_clean)
    print(f"Built {exe}")
    if args.smoke:
        from pdf_studio.tests.fixtures.make_fixtures import ensure_fixtures
        sample = ensure_fixtures()["sample.pdf"]
        code = smoke(exe, sample)
        print("Smoke test", "passed" if code == 0 else f"FAILED ({code})")
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
