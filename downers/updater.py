"""Keep yt-dlp current without pip, so Downers.exe can update itself too.

"Update yt-dlp" downloads the latest yt-dlp wheel from PyPI, plus the exact
yt-dlp-ejs it pins (the solver for YouTube's JavaScript challenges), and unpacks
both into paths.YTDLP_OVERRIDE. On the next start, activate() makes Python load
them from there instead of the bundled or pip-installed copy, but only while
they are newer. A later rebuild with a fresher yt-dlp wins automatically.

activate() must run before anything imports yt_dlp.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.metadata
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from downers.paths import YTDLP_OVERRIDE

PACKAGES = ("yt_dlp", "yt_dlp_ejs")
active: str | None = None   # version of the override in use, set by activate()
VERSION_FILE = YTDLP_OVERRIDE / "VERSION"


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def installed_version() -> str:
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def override_version() -> str | None:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None


class _OverrideFinder(importlib.abc.MetaPathFinder):
    """Serve yt_dlp and yt_dlp_ejs, and all their submodules, from the override
    folder. Sitting first on sys.meta_path, it beats PyInstaller's frozen
    importer, so the two versions can never get mixed."""

    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] not in PACKAGES:
            return None
        search = [str(YTDLP_OVERRIDE)] if "." not in name else path
        return importlib.machinery.PathFinder.find_spec(name, search)


def activate() -> str | None:
    """Use the downloaded yt-dlp if it's newer. Returns its version if so."""
    version = override_version()
    if not version or _key(version) <= _key(installed_version()):
        return None
    if not all((YTDLP_OVERRIDE / p / "__init__.py").exists() for p in PACKAGES):
        return None
    sys.meta_path.insert(0, _OverrideFinder())
    global active
    active = version
    return version


def _pypi(ydl, project: str, version: str | None = None) -> dict:
    url = f"https://pypi.org/pypi/{project}{'/' + version if version else ''}/json"
    return json.loads(ydl.urlopen(url).read())


def _wheel(ydl, release: dict) -> zipfile.ZipFile:
    url = next(u["url"] for u in release["urls"]
               if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("none-any.whl"))
    return zipfile.ZipFile(io.BytesIO(ydl.urlopen(url).read()))


def latest_version(proxy: str = "") -> str:
    """The newest yt-dlp on PyPI."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "proxy": proxy or None}) as ydl:
        return _pypi(ydl, "yt-dlp")["info"]["version"]


def is_newer(version: str, than: str) -> bool:
    return _key(version) > _key(than)


def update(current: str, proxy: str = "") -> tuple[bool, str]:
    """Fetch the latest yt-dlp if `current` is older.

    Returns (changed, message). Raises on network trouble.
    """
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "proxy": proxy or None}) as ydl:
        release = _pypi(ydl, "yt-dlp")
        latest = release["info"]["version"]
        if _key(latest) <= _key(current):
            return False, f"yt-dlp {current} is already the latest."

        pin = next((m.group(1) for r in release["info"].get("requires_dist") or []
                    if (m := re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)", r))), None)
        wheels = [_wheel(ydl, release), _wheel(ydl, _pypi(ydl, "yt-dlp-ejs", pin))]

    # Unpack beside the live folder, then swap, so a failure leaves the old one working
    staging = YTDLP_OVERRIDE.with_name(YTDLP_OVERRIDE.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for wheel in wheels:
        for member in wheel.namelist():
            if member.partition("/")[0] in PACKAGES:
                wheel.extract(member, staging)
    (staging / "VERSION").write_text(latest, encoding="utf-8")

    old = YTDLP_OVERRIDE.with_name(YTDLP_OVERRIDE.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if YTDLP_OVERRIDE.exists():
        YTDLP_OVERRIDE.rename(old)
    staging.rename(YTDLP_OVERRIDE)
    shutil.rmtree(old, ignore_errors=True)
    return True, f"Updated yt-dlp {current} → {latest}."
