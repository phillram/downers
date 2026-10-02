"""The downloaded-yt-dlp override. Runs Python in a subprocess with APPDATA
pointed at a temporary folder, so the real override (if any) is untouched."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
from downers import updater
print(updater.activate())
import yt_dlp.version
print(yt_dlp.version.__version__)
"""


def fake_override(appdata: Path, version: str) -> None:
    base = appdata / "Downers" / "yt-dlp"
    for pkg in ("yt_dlp", "yt_dlp_ejs"):
        (base / pkg).mkdir(parents=True)
        (base / pkg / "__init__.py").write_text("")
    (base / "yt_dlp" / "version.py").write_text(f"__version__ = 'fake {version}'\n")
    (base / "VERSION").write_text(version)


def probe(appdata: Path) -> list[str]:
    env = os.environ | {"APPDATA": str(appdata)}
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def test_newer_override_is_used_down_to_submodules(tmp_path):
    fake_override(tmp_path, "9999.1.1")
    assert probe(tmp_path) == ["9999.1.1", "fake", "9999.1.1"]


def test_older_override_is_ignored(tmp_path):
    fake_override(tmp_path, "2001.1.1")
    activated, version = probe(tmp_path)
    assert activated == "None"
    assert version != "fake"


def test_no_override(tmp_path):
    assert probe(tmp_path)[0] == "None"
