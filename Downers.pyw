#! C:\Coding\.venv\Scripts\pythonw.exe
"""Double-click to open Downers without a console window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downers import updater

updater.activate()  # before anything imports yt_dlp

from downers.app import main  # noqa: E402

main()
