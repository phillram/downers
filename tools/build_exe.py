"""Build Downers.exe.

    python tools/build_exe.py

Produces dist/Downers/, a folder that runs on any Windows PC without Python:

    Downers.exe    the app (yt-dlp is inside it)
    ffmpeg.exe     merging, converting, embedding thumbnails
    deno.exe       solves YouTube's JavaScript challenges

The two helpers stay beside the exe rather than inside it: packed in, all
~185 MB would be unpacked to a temp folder on every launch. Run this again
after changing any of the code.
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # tools/ -> project root
OUT_DIR = ROOT / "dist" / "Downers"
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

    # PyInstaller quietly leaves out a module that doesn't compile
    import compileall
    if not compileall.compile_dir(ROOT / "downers", quiet=1):
        print("The code has a syntax error (above); fix it first.")
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
    if subprocess.run(command, cwd=ROOT).returncode != 0:
        print("\nBuild failed: see the PyInstaller output above.")
        return 1

    for name, source in helpers.items():
        target = OUT_DIR / name
        if not (target.exists() and filecmp.cmp(source, target, shallow=False)):
            print(f"Copying {name}")
            shutil.copy2(source, target)

    size = sum(f.stat().st_size for f in OUT_DIR.iterdir()) / 1e6
    print(f"\nBuilt {OUT_DIR} ({size:.0f} MB, exe {EXE_PATH.stat().st_size / 1e6:.0f} MB) "
          f"in {time.time() - started:.0f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
