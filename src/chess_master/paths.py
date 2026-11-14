"""Filesystem locations used by the application.

The module serves two targets: a source checkout (or pip install) and a
PyInstaller ``--onedir`` build.  In a frozen build read-only assets live inside
``sys._MEIPASS``, while state and the external engine live next to the
executable so the extracted folder stays portable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "APP_DIR",
    "ASSET_ROOT",
    "BIN_ROOT",
    "DEFAULT_DB_PATH",
    "FROZEN",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "STATE_ROOT",
    "WEB_ROOT",
]

FROZEN = bool(getattr(sys, "frozen", False))

PACKAGE_ROOT = Path(__file__).resolve().parent

# ``src/chess_master`` → ``src`` → repository root in a source checkout.  When
# the package is installed into site-packages, fall back to the current working
# directory so a portable ``bin/`` next to the launcher is still discovered.
_SOURCE_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = _SOURCE_ROOT.parent
if not FROZEN and not (PROJECT_ROOT / "pyproject.toml").exists():
    PROJECT_ROOT = Path.cwd()

_MEIPASS = Path(getattr(sys, "_MEIPASS", PACKAGE_ROOT))

# Read-only bundled assets.  PyInstaller places ``--add-data`` payloads under
# ``_MEIPASS``; the bundle destination is ``chess_master/...``.
ASSET_ROOT = (_MEIPASS / "chess_master") if FROZEN else PACKAGE_ROOT

APP_DIR = Path(sys.executable).resolve().parent if FROZEN else PROJECT_ROOT

WEB_ROOT = ASSET_ROOT / "web"


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if FROZEN:
    # The shipped layout puts ``bin/stockfish`` next to the .exe; a one-file
    # build (or an unwritable folder) falls back to the bundled copy.
    BIN_ROOT = APP_DIR / "bin"
    if not BIN_ROOT.is_dir():
        BIN_ROOT = _MEIPASS / "bin"
    STATE_ROOT = APP_DIR / "state"
    if not _is_writable(STATE_ROOT):
        STATE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChessMaster" / "state"
        STATE_ROOT.mkdir(parents=True, exist_ok=True)
else:
    BIN_ROOT = PROJECT_ROOT / "bin"
    STATE_ROOT = PROJECT_ROOT / "state"
    STATE_ROOT.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = STATE_ROOT / "chess_master.db"
