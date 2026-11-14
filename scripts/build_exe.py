"""Build the standalone Windows desktop app.

Usage::

    .\\.venv\\Scripts\\python.exe scripts\\build_exe.py

Produces ``dist/ChessMaster/ChessMaster.exe`` (onedir, self-contained with
Stockfish and a writable ``state/``) plus a portable zip next to it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "ChessMaster"
VERSION = "1.0.0"
DIST_ROOT = ROOT / "dist"
APP_DIR = DIST_ROOT / APP_NAME
ICON_PATH = ROOT / "assets" / "icon.ico"
ENTRY_PATH = ROOT / "build" / "pyinstaller_entry.py"
ZIP_PATH = DIST_ROOT / f"{APP_NAME}-v{VERSION}-win64-portable.zip"

WEB_DIR = ROOT / "src" / "chess_master" / "web"
OPENINGS_TSV = ROOT / "src" / "chess_master" / "analysis" / "openings_data.tsv"
LOCAL_STOCKFISH = ROOT / "bin" / "stockfish"


def _run(command: list[str]) -> None:
    print(">", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def generate_icon(path: Path = ICON_PATH) -> Path:
    """Draw a dark/jade knight and save a multi-resolution Windows icon."""

    path.parent.mkdir(parents=True, exist_ok=True)
    scale = 4
    size = 256 * scale
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (6 * scale, 6 * scale, size - 6 * scale, size - 6 * scale),
        radius=52 * scale,
        fill="#0B0E12",
        outline="#1F6F5C",
        width=6 * scale,
    )
    knight = [
        (78, 196), (178, 196), (170, 158), (152, 128), (166, 104), (156, 82),
        (166, 62), (144, 48), (120, 52), (96, 72), (78, 98), (86, 120),
        (104, 124), (124, 108), (116, 132), (96, 150), (82, 172),
    ]
    draw.polygon([(x * scale, y * scale) for x, y in knight], fill="#3FE0B0")
    draw.ellipse((126 * scale, 70 * scale, 136 * scale, 80 * scale), fill="#0B0E12")
    draw.rounded_rectangle(
        (60 * scale, 198 * scale, 196 * scale, 216 * scale),
        radius=8 * scale,
        fill="#1F6F5C",
    )
    image = image.resize((256, 256), Image.LANCZOS)
    image.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


def _write_entry() -> Path:
    ENTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENTRY_PATH.write_text(
        "from chess_master.app import main\n\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    return ENTRY_PATH


def _copy_stockfish() -> Path:
    target = APP_DIR / "bin" / "stockfish"
    target.mkdir(parents=True, exist_ok=True)
    if not LOCAL_STOCKFISH.is_dir():
        raise SystemExit(
            "bin/stockfish/ is missing. Put a Stockfish binary there before building."
        )
    binaries = [
        path
        for path in LOCAL_STOCKFISH.iterdir()
        if path.is_file() and path.name.lower().startswith("stockfish")
    ]
    if not binaries:
        raise SystemExit("No stockfish* binary found in bin/stockfish/.")
    for path in binaries:
        shutil.copy2(path, target / path.name)
    copying = LOCAL_STOCKFISH / "Copying.txt"
    if copying.is_file():
        shutil.copy2(copying, target / copying.name)
    return target


def _zip_app() -> Path:
    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, Path(APP_NAME) / path.relative_to(APP_DIR))
    return ZIP_PATH


def build() -> Path:
    _run(["npm.cmd" if os.name == "nt" else "npm", "run", "build"])
    icon = generate_icon()
    entry = _write_entry()

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(icon),
            "--distpath",
            str(DIST_ROOT),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            "--specpath",
            str(ROOT / "build"),
            "--paths",
            str(ROOT / "src"),
            "--add-data",
            f"{WEB_DIR}{os.pathsep}chess_master/web",
            "--add-data",
            f"{OPENINGS_TSV}{os.pathsep}chess_master/analysis",
            "--collect-submodules",
            "webview",
            "--hidden-import",
            "webview.platforms.winforms",
            str(entry),
        ]
    )

    _copy_stockfish()
    (APP_DIR / "state").mkdir(parents=True, exist_ok=True)
    return APP_DIR


def main() -> int:
    app_dir = build()
    archive = _zip_app()
    exe = app_dir / f"{APP_NAME}.exe"
    print(f"\nDesktop app: {exe}")
    print(f"Portable zip: {archive}")
    print(f"Smoke test:   {exe} --smoke-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
