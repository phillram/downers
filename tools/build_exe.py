"""Build Downers.exe.

    python tools/build_exe.py

Puts three files straight into the project folder. They run on any Windows PC
without Python, as long as they stay together:

    Downers.exe    the app (yt-dlp is inside it)
    ffmpeg.exe     merging, converting, embedding thumbnails
    deno.exe       solves YouTube's JavaScript challenges

The two helpers stay beside the exe rather than inside it: packed in, all
~185 MB would be unpacked to a temp folder on every launch. Run this again
after changing any of the code. All three are gitignored.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # tools/ -> project root
OUT_DIR = ROOT
EXE_PATH = OUT_DIR / "Downers.exe"
WORK_DIR = ROOT / "build"
ICON = ROOT / "assets" / "downers.ico"

# Never imported by Downers, but can get dragged in transitively from the venv
EXCLUDES = ("matplotlib", "scipy", "pandas", "numpy", "PIL", "PyQt5", "PyQt6",
            "PySide2", "PySide6", "pytest", "IPython", "notebook")


def _is_running() -> bool:
    """Windows locks a running exe, so the build couldn't replace it."""
    if not EXE_PATH.exists():
        return False
    try:
        with EXE_PATH.open("ab"):
            return False
    except OSError:
        return True


def _helpers() -> dict[str, Path]:
    import deno
    import imageio_ffmpeg
    return {"ffmpeg.exe": Path(imageio_ffmpeg.get_ffmpeg_exe()),
            "deno.exe": Path(deno.find_deno_bin())}


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
        helpers = _helpers()
    except ImportError as e:
        print(f"Missing {e.name}. Run:")
        print(f"    {sys.executable} -m pip install pyinstaller -r requirements.txt")
        return 1

    # PyInstaller quietly leaves out a module that doesn't compile.
    # compile() checks without writing __pycache__ folders.
    for source in (ROOT / "downers").glob("*.py"):
        try:
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
        except SyntaxError as e:
            print(f"Syntax error, fix it first: {e}")
            return 1

    if _is_running():
        print("Downers.exe is running, so it can't be replaced. Close it and run this again.")
        return 1

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed", "--name", "Downers",
        "--icon", str(ICON), "--add-data", f"{ICON};.",
        "--paths", str(ROOT),
        # The JavaScript the challenge solver runs, and the version numbers the
        # updater compares against
        "--collect-data", "yt_dlp_ejs",
        "--copy-metadata", "yt-dlp", "--copy-metadata", "yt-dlp-ejs",
        "--distpath", str(OUT_DIR), "--workpath", str(WORK_DIR), "--specpath", str(WORK_DIR),
    ]
    for module in EXCLUDES:
        command += ["--exclude-module", module]
    command.append(str(ROOT / "downers" / "__main__.py"))

    print("Building Downers.exe. This takes a minute.\n")
    started = time.time()
    # No __pycache__ folders left in the project by the analysis
    env = os.environ | {"PYTHONDONTWRITEBYTECODE": "1"}
    if subprocess.run(command, cwd=ROOT, env=env).returncode != 0:
        print("\nBuild failed: see the PyInstaller output above.")
        return 1

    shutil.rmtree(WORK_DIR, ignore_errors=True)  # PyInstaller's scratch; keep the folder flat

    for name, source in helpers.items():
        target = OUT_DIR / name
        if not (target.exists() and filecmp.cmp(source, target, shallow=False)):
            print(f"Copying {name}")
            shutil.copy2(source, target)

    print(f"\nBuilt {EXE_PATH} ({EXE_PATH.stat().st_size / 1e6:.0f} MB) "
          f"in {time.time() - started:.0f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
