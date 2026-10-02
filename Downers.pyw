#! C:\Coding\.venv\Scripts\pythonw.exe
"""Double-click to open Downers without a console window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downers.app import main

main()
