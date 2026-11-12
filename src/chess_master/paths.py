"""Filesystem locations used by the application.

Source checkouts keep everything beside the repository.  A PyInstaller build
reads its read-only assets from the bundle (``sys._MEIPASS``) and writes state
to a per-user folder, unless a ``portable.flag`` sits next to ``ChessMaster.exe``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "APP_DIR",
    "BIN_ROOT",
    "BUNDLE_ROOT",
    "DEFAULT_DB_PATH",
    "OPENINGS_DATA_PATH",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "STATE_ROOT",
    "WEB_ROOT",
]

PACKAGE_ROOT = Path(__file__).resolve().parent


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if _frozen():
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    APP_DIR = Path(sys.executable).resolve().parent
    PROJECT_ROOT = APP_DIR
    WEB_ROOT = BUNDLE_ROOT / "chess_master" / "web"
    OPENINGS_DATA_PATH = BUNDLE_ROOT / "chess_master" / "analysis" / "openings_data.tsv"

    _app_bin = APP_DIR / "bin"
    _bundle_bin = BUNDLE_ROOT / "bin"
    BIN_ROOT = _app_bin if _app_bin.is_dir() else _bundle_bin

    # Portable builds opt in by dropping ``portable.flag`` next to the exe.
    if (APP_DIR / "portable.flag").exists():
        STATE_ROOT = APP_DIR / "state"
    else:
        STATE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChessMaster" / "state"
else:
    BUNDLE_ROOT = PACKAGE_ROOT
    # ``src/chess_master`` → ``src`` → repository root in a source checkout.  When
    # the package is installed into site-packages, fall back to the current working
    # directory so a portable ``bin/`` next to the launcher is still discovered.
    _SOURCE_ROOT = PACKAGE_ROOT.parent
    PROJECT_ROOT = _SOURCE_ROOT.parent
    if not (PROJECT_ROOT / "pyproject.toml").exists():
        PROJECT_ROOT = Path.cwd()
    APP_DIR = PROJECT_ROOT

    BIN_ROOT = PROJECT_ROOT / "bin"
    WEB_ROOT = PACKAGE_ROOT / "web"
    OPENINGS_DATA_PATH = PACKAGE_ROOT / "analysis" / "openings_data.tsv"

    STATE_ROOT = PROJECT_ROOT / "state"

DEFAULT_DB_PATH = STATE_ROOT / "chess_master.db"
STATE_ROOT.mkdir(parents=True, exist_ok=True)
