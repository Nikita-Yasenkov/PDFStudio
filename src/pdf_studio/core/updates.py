"""Is there a newer release, and which file is for this computer? (Qt-free.)

Help → Check for Updates asks GitHub for the repository's latest release
(:data:`LATEST_RELEASE_URL`). The release workflow publishes it as tag
``v<version>`` with the downloads named in :func:`asset_name`; its notes are
the version's CHANGELOG.md section followed by install steps
(``scripts/release_notes.py``).
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

REPOSITORY = "Nikita-Yasenkov/PDFStudio"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases/latest"

#: where the release notes' install steps start, after the CHANGELOG section
_INSTALL_STEPS = re.compile(r"^### Get started\b", re.M)
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str = ""  # hex digest GitHub computed on upload; "" when not reported


@dataclass(frozen=True)
class Release:
    version: str  # "0.1.3", without the tag's "v"
    notes: str  # what's new, as Markdown
    page_url: str
    assets: Tuple[Asset, ...] = ()

    def asset(self, name: Optional[str]) -> Optional[Asset]:
        return next((a for a in self.assets if a.name == name), None)


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """``"v0.1.10"`` → ``(0, 1, 10)``; None for anything else."""
    m = _VERSION.fullmatch((text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def whats_new(body: str) -> str:
    """Release notes without the install steps and checksums that follow."""
    return _INSTALL_STEPS.split(body or "", maxsplit=1)[0].strip()


def parse_release(data: Any) -> Release:
    """A release from GitHub's ``releases/latest`` JSON; ValueError when it
    isn't one or its tag isn't a version."""
    if not isinstance(data, Mapping):
        raise ValueError("not a release")
    tag = str(data.get("tag_name") or "")
    m = _VERSION.fullmatch(tag.strip())
    if m is None:
        raise ValueError(f"release tag {tag!r} is not a version")
    assets = []
    for a in data.get("assets") or ():
        if not isinstance(a, Mapping):
            continue
        digest = str(a.get("digest") or "")
        assets.append(Asset(name=str(a.get("name") or ""),
                            url=str(a.get("browser_download_url") or ""),
                            size=int(a.get("size") or 0),
                            sha256=digest[7:].lower() if digest.startswith("sha256:") else ""))
    return Release(version=m.group(1), notes=whats_new(str(data.get("body") or "")),
                   page_url=str(data.get("html_url") or RELEASES_URL), assets=tuple(assets))


def asset_name(system: str = sys.platform, machine: Optional[str] = None) -> Optional[str]:
    """The release download for this computer; None where there is none."""
    if system == "win32":
        return "PDFStudio-win64.zip"
    if system == "darwin":
        return f"PDFStudio-macos-{machine or mac_architecture()}.dmg"
    return None


def mac_architecture() -> str:
    """``arm64`` or ``x86_64``: the Mac's own processor, so an Intel build
    running under Rosetta on Apple silicon is offered the arm64 download."""
    machine = platform.machine()
    if machine == "x86_64":
        try:
            out = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out.strip() == "1":
            return "arm64"
    return machine


def download_name(asset: str, version: str) -> str:
    """``PDFStudio-win64.zip`` → ``PDFStudio-0.1.3-win64.zip``: the saved
    file says which version it is."""
    stem, sep, rest = asset.partition("-")
    return f"{stem}-{version}-{rest}" if sep else f"{version}-{asset}"


def unique_path(directory: str, name: str) -> str:
    """``directory/name``, or ``name (2)``, ``name (3)``… when it's taken."""
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(directory, name), 1
    while os.path.exists(path) or os.path.exists(path + ".part"):
        n += 1
        path = os.path.join(directory, f"{stem} ({n}){ext}")
    return path
