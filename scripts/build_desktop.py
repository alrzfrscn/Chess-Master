"""Build the Windows desktop bundle, portable ZIP and one-click installer.

Usage (from the repository root, with the project virtualenv active)::

    .\\.venv\\Scripts\\python.exe scripts/build_desktop.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"
INSTALLER = ROOT / "installer"
ICON = ASSETS / "icon.ico"

APP_NAME = "ChessMaster"
VERSION = "1.0.0"
PORTABLE_ZIP = DIST / f"ChessMaster-v{VERSION}-win64-portable.zip"
SETUP_EXE = DIST / f"ChessMaster-Setup-v{VERSION}.exe"

WEB_ROOT = ROOT / "src" / "chess_master" / "web"
OPENINGS_DATA = ROOT / "src" / "chess_master" / "analysis" / "openings_data.tsv"
STOCKFISH_DIR = ROOT / "bin" / "stockfish"
ENTRY_POINT = ROOT / "src" / "chess_master" / "app.py"

# Normalized 0..100 knight silhouette (facing left) with a plinth.
_KNIGHT = [
    (18, 52), (26, 44), (34, 34), (44, 28),
    (50, 14), (55, 27), (63, 16), (66, 30),
    (73, 37), (66, 43), (75, 47), (68, 55),
    (76, 59), (85, 68), (88, 82), (88, 90),
    (40, 90), (37, 80), (32, 70), (26, 62),
    (23, 57),
]


def _run(command: list[str] | str, **kwargs: object) -> None:
    print("+", command if isinstance(command, str) else " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, **kwargs)


def generate_icon(path: Path = ICON) -> Path:
    """Render the dark studio badge with a jade knight at every ICO size."""

    from PIL import Image, ImageDraw

    supersample = 4
    canvas = 256 * supersample
    scale, offset_x, offset_y = 2.2, 11.0, 16.0

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 8 * supersample
    draw.rounded_rectangle(
        (margin, margin, canvas - margin, canvas - margin),
        radius=56 * supersample,
        fill="#0b0e12",
        outline="#5fd7a4",
        width=12 * supersample,
    )

    def point(x: float, y: float) -> tuple[float, float]:
        return ((offset_x + x * scale) * supersample, (offset_y + y * scale) * supersample)

    draw.polygon([point(x, y) for x, y in _KNIGHT], fill="#5fd7a4")
    draw.rounded_rectangle(
        (point(30, 88)[0], point(30, 88)[1], point(96, 98)[0], point(96, 98)[1]),
        radius=3 * supersample,
        fill="#5fd7a4",
    )
    eye = point(31, 44)
    draw.ellipse(
        (eye[0] - 5 * supersample, eye[1] - 5 * supersample, eye[0] + 5 * supersample, eye[1] + 5 * supersample),
        fill="#0b0e12",
    )

    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="ICO", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    print(f"icon: {path}", flush=True)
    return path


def build_app() -> Path:
    """Run the frontend build, then produce ``dist/ChessMaster/ChessMaster.exe``."""

    generate_icon()
    _run(["npm", "run", "build"], shell=True)

    app_dir = DIST / APP_NAME
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
            str(ICON),
            "--paths",
            str(ROOT / "src"),
            "--add-data",
            f"{WEB_ROOT}{os.pathsep}chess_master/web",
            "--add-data",
            f"{OPENINGS_DATA}{os.pathsep}chess_master/analysis",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / "pyinstaller"),
            "--specpath",
            str(BUILD),
            str(ENTRY_POINT),
        ]
    )

    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, app_dir / name)

    if not STOCKFISH_DIR.is_dir():
        raise FileNotFoundError(f"Local Stockfish build is missing: {STOCKFISH_DIR}")
    shutil.copytree(STOCKFISH_DIR, app_dir / "bin" / "stockfish", dirs_exist_ok=True)
    print(f"app: {app_dir}", flush=True)
    return app_dir


def build_portable(app_dir: Path) -> Path:
    """Zip a copy of the app that keeps its database beside the executable."""

    staging = BUILD / "portable"
    if staging.exists():
        shutil.rmtree(staging)
    target = staging / APP_NAME
    shutil.copytree(app_dir, target)
    (target / "portable.flag").write_text("", encoding="utf-8")

    PORTABLE_ZIP.unlink(missing_ok=True)
    archive = shutil.make_archive(
        str(PORTABLE_ZIP.with_suffix("")),
        "zip",
        root_dir=str(staging),
        base_dir=APP_NAME,
    )
    print(f"portable: {archive}", flush=True)
    return Path(archive)


def write_inno_script() -> Path:
    """Keep an Inno Setup equivalent next to the self-contained installer."""

    INSTALLER.mkdir(parents=True, exist_ok=True)
    path = INSTALLER / "chess_master.iss"
    path.write_text(
        f"""\
; Reference script for building the same installer with Inno Setup.
; Our shipped setup is scripts/installer_stub.py compiled by PyInstaller.
#define MyAppName "Chess Master"
#define MyAppVersion "{VERSION}"
#define MyAppExeName "{APP_NAME}.exe"

[Setup]
AppId={{{{8C2F4E9A-6B1D-4C7E-9A3F-5D2B8E1C0A47}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
DefaultDirName={{localappdata}}\\Programs\\Chess Master
DefaultGroupName={{#MyAppName}}
PrivilegesRequired=lowest
OutputDir=..\\dist
OutputBaseFilename=ChessMaster-Setup-v{{#MyAppVersion}}
SetupIconFile=..\\assets\\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\\dist\\ChessMaster\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{{autodesktop}}\\Chess Master"; Filename: "{{app}}\\{{#MyAppExeName}}"; IconFilename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{autoprograms}}\\Chess Master"; Filename: "{{app}}\\{{#MyAppExeName}}"; IconFilename: "{{app}}\\{{#MyAppExeName}}"

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Flags: nowait postinstall skipifsilent
""",
        encoding="utf-8",
    )
    return path


def build_installer(app_dir: Path) -> Path:
    """Compile the Tkinter setup wizard with the application payload embedded."""

    write_inno_script()
    SETUP_EXE.unlink(missing_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            f"ChessMaster-Setup-v{VERSION}",
            "--icon",
            str(ICON),
            "--add-data",
            f"{app_dir}{os.pathsep}payload",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / "installer"),
            "--specpath",
            str(BUILD),
            str(ROOT / "scripts" / "installer_stub.py"),
        ]
    )
    print(f"setup: {SETUP_EXE}", flush=True)
    return SETUP_EXE


def main() -> int:
    app_dir = build_app()
    build_portable(app_dir)
    build_installer(app_dir)
    print("\nArtifacts:", flush=True)
    for artifact in (app_dir / f"{APP_NAME}.exe", PORTABLE_ZIP, SETUP_EXE):
        print(f"  {artifact} ({artifact.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
