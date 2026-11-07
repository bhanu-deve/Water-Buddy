"""Finding the project's files, both when run from source and when packaged.

When you run `python main.py`, files live next to main.py.
When the app is later packaged with PyInstaller (Phase 7) into one .exe, the data
files are unpacked into a temporary folder whose path is stored in `sys._MEIPASS`.
Every module asks *this* file where things are, so nothing else needs to care.
"""

from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    """The folder that contains the `assets` folder."""
    if getattr(sys, "frozen", False):  # running as a PyInstaller .exe
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent  # the folder holding main.py


def asset_path(*parts: str) -> Path:
    """Path inside the assets folder, e.g. asset_path("character", "water_reminder.gif")."""
    return project_root().joinpath("assets", *parts)
