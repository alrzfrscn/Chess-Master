"""Filesystem locations used by the application."""

from __future__ import annotations

from pathlib import Path

__all__ = ["BIN_ROOT", "PACKAGE_ROOT", "PROJECT_ROOT", "WEB_ROOT"]

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
