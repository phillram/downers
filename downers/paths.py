"""Where Downers keeps things, whether run from source or as Downers.exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# From source: the project root. Built: the folder holding Downers.exe, which is
# also where ffmpeg.exe and deno.exe sit.
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent

# Read-only files shipped with the app (the icon)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR / "assets"))
ICON = RESOURCE_DIR / "downers.ico"

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Downers"
SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "queue.json"
ARCHIVE_FILE = DATA_DIR / "archive.txt"
# A newer yt-dlp fetched by "Update yt-dlp", used in preference to the bundled one
YTDLP_OVERRIDE = DATA_DIR / "yt-dlp"
