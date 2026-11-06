"""Filesystem locations used by the application."""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "BIN_ROOT",
    "DEFAULT_DB_PATH",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "STATE_ROOT",
    "WEB_ROOT",
]

PACKAGE_ROOT = Path(__file__).resolve().parent

# ``src/chess_master`` → ``src`` → repository root in a source checkout.  When
# the package is installed into site-packages, fall back to the current working
# directory so a portable ``bin/`` next to the launcher is still discovered.
_SOURCE_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = _SOURCE_ROOT.parent
if not (PROJECT_ROOT / "pyproject.toml").exists():
    PROJECT_ROOT = Path.cwd()

BIN_ROOT = PROJECT_ROOT / "bin"
WEB_ROOT = PACKAGE_ROOT / "web"

STATE_ROOT = PROJECT_ROOT / "state"
DEFAULT_DB_PATH = STATE_ROOT / "chess_master.db"
STATE_ROOT.mkdir(parents=True, exist_ok=True)
