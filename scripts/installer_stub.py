"""Chess Master v1.0.0 one-click Windows setup wizard.

Compiled by ``scripts/build_desktop.py`` with the application payload embedded
under ``payload/``.  It installs per-user (no UAC), writes Desktop and Start
Menu shortcuts, and registers an uninstaller in Windows "Installed Apps".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Chess Master"
APP_ID = "ChessMaster"
VERSION = "1.0.0"
EXE_NAME = "ChessMaster.exe"
UNINSTALL_NAME = "Uninstall-ChessMaster.bat"

BG = "#0b0e12"
PANEL = "#111721"
FG = "#e8edf3"
MUTED = "#8b97a8"
JADE = "#5fd7a4"
BORDER = "#263140"

NO_WINDOW = 0x08000000


def _payload_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload"


def _default_target() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "Programs" / APP_NAME


def _powershell(script: str) -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
        check=False,
    )
    return result.stdout.strip()


def _shell_folder(name: str) -> Path | None:
    try:
        value = _powershell(f"[Environment]::GetFolderPath('{name}')")
    except OSError:
        return None
    return Path(value) if value else None


def _shortcut_paths() -> tuple[Path, Path]:
    desktop = _shell_folder("Desktop")
    if desktop is None:
        desktop = Path.home() / "Desktop"
    programs = _shell_folder("Programs")
    if programs is None:
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return desktop / f"{APP_NAME}.lnk", programs / f"{APP_NAME}.lnk"


def _create_shortcut(lnk: Path, target: Path) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)

    def quote(value: object) -> str:
        return str(value).replace("'", "''")

    script = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{target}';$s.WorkingDirectory='{workdir}';"
        "$s.IconLocation='{target},0';$s.Save()"
    ).format(lnk=quote(lnk), target=quote(target), workdir=quote(target.parent))
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        creationflags=NO_WINDOW,
        check=True,
    )


def _write_uninstaller(target: Path, desktop_lnk: Path, start_lnk: Path) -> None:
    script = """@echo off
setlocal
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\\" set "APPDIR=%APPDIR:~0,-1%"
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ChessMaster" /f >nul 2>&1
del "{desktop}" >nul 2>&1
del "{start_menu}" >nul 2>&1
> "%TEMP%\\chess_master_uninstall.bat" echo @echo off
>> "%TEMP%\\chess_master_uninstall.bat" echo timeout /t 2 /nobreak ^>nul
>> "%TEMP%\\chess_master_uninstall.bat" echo rmdir /s /q "%APPDIR%"
>> "%TEMP%\\chess_master_uninstall.bat" echo del /q "%TEMP%\\chess_master_uninstall.bat"
start "" /min cmd /c "%TEMP%\\chess_master_uninstall.bat"
endlocal
exit /b 0
""".format(desktop=desktop_lnk, start_menu=start_lnk)
    (target / UNINSTALL_NAME).write_text(script, encoding="utf-8", newline="\r\n")


def _register_uninstall(target: Path) -> None:
    path = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        f"\\{APP_ID}"
    )
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": VERSION,
        "Publisher": APP_NAME,
        "InstallLocation": str(target),
        "DisplayIcon": str(target / EXE_NAME),
        "UninstallString": f'cmd.exe /c ""{target / UNINSTALL_NAME}""',
        "NoModify": 1,
        "NoRepair": 1,
    }
    try:
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)
    finally:
        winreg.CloseKey(key)


def install(
    target: Path,
    *,
    desktop_shortcut: bool,
    launch: bool,
    progress,
) -> Path:
    payload = _payload_dir()
    files = [item for item in payload.rglob("*") if item.is_file()]
    total = max(1, len(files))
    target.mkdir(parents=True, exist_ok=True)

    for index, source in enumerate(files, start=1):
        destination = target / source.relative_to(payload)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        progress(index, total)

    exe = target / EXE_NAME
    desktop_lnk, start_lnk = _shortcut_paths()
    _write_uninstaller(target, desktop_lnk, start_lnk)
    _register_uninstall(target)
    if desktop_shortcut:
        _create_shortcut(desktop_lnk, exe)
    _create_shortcut(start_lnk, exe)

    if launch:
        os.startfile(str(exe))
    return exe


class SetupWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.installing = False

        root.title(f"{APP_NAME} v{VERSION} Setup")
        root.geometry("640x440")
        root.resizable(False, False)
        root.configure(bg=BG)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=32, pady=(28, 6))
        tk.Label(
            header, text=APP_NAME, bg=BG, fg=FG, font=("Segoe UI", 24, "bold")
        ).pack(anchor="w")
        tk.Label(
            header,
            text=f"v{VERSION}  ·  Precision chess desktop studio",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=32, pady=18)

        tk.Label(
            body, text="Install location", bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(6, 18))
        self.path_var = tk.StringVar(value=str(_default_target()))
        entry = tk.Entry(
            row,
            textvariable=self.path_var,
            bg=PANEL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=JADE,
            font=("Consolas", 10),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        tk.Button(
            row,
            text="Browse…",
            command=self.browse,
            bg="#1b222c",
            fg=FG,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        ).pack(side="left")

        self.desktop_var = tk.BooleanVar(value=True)
        self.launch_var = tk.BooleanVar(value=True)
        for text, variable in (
            ("Create Desktop Shortcut", self.desktop_var),
            ("Launch Chess Master after installation", self.launch_var),
        ):
            tk.Checkbutton(
                body,
                text=text,
                variable=variable,
                bg=BG,
                fg=FG,
                activebackground=BG,
                activeforeground=FG,
                selectcolor="#1b222c",
                highlightthickness=0,
                bd=0,
                font=("Segoe UI", 10),
                anchor="w",
            ).pack(anchor="w", pady=2)

        self.status = tk.Label(
            body, text="Ready to install.", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w"
        )
        self.status.pack(fill="x", pady=(20, 6))

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(
            "Jade.Horizontal.TProgressbar",
            troughcolor=PANEL,
            background=JADE,
            bordercolor=PANEL,
            lightcolor=JADE,
            darkcolor=JADE,
            thickness=10,
        )
        self.progress = ttk.Progressbar(
            body, style="Jade.Horizontal.TProgressbar", maximum=100, value=0
        )
        self.progress.pack(fill="x")

        footer = tk.Frame(root, bg=BG)
        footer.pack(fill="x", padx=32, pady=(8, 26))
        self.install_button = tk.Button(
            footer,
            text="Install",
            command=self.start,
            bg=JADE,
            fg=BG,
            activebackground="#7fe8bb",
            activeforeground=BG,
            relief="flat",
            bd=0,
            padx=26,
            pady=10,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        self.install_button.pack(side="right")
        tk.Button(
            footer,
            text="Cancel",
            command=root.destroy,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=FG,
            relief="flat",
            bd=0,
            padx=16,
            pady=10,
            font=("Segoe UI", 10),
            cursor="hand2",
        ).pack(side="right", padx=8)

    def browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.path_var.get())
        if chosen:
            self.path_var.set(chosen)

    def start(self) -> None:
        if self.installing:
            return
        target = Path(self.path_var.get().strip())
        if not target.name:
            messagebox.showerror(APP_NAME, "Choose an install location.")
            return
        self.installing = True
        self.install_button.configure(state="disabled", text="Installing…")
        self.status.configure(text="Copying application files…")
        threading.Thread(
            target=self._worker,
            args=(target, self.desktop_var.get(), self.launch_var.get()),
            daemon=True,
        ).start()

    def _worker(self, target: Path, desktop: bool, launch: bool) -> None:
        try:
            exe = install(
                target,
                desktop_shortcut=desktop,
                launch=launch,
                progress=self._progress,
            )
        except Exception as exc:  # surfaced in the wizard, never silently lost
            self.root.after(0, self._failed, exc)
            return
        self.root.after(0, self._done, exe, launch)

    def _progress(self, done: int, total: int) -> None:
        percent = done * 100.0 / total
        self.root.after(0, self._set_progress, percent, done, total)

    def _set_progress(self, percent: float, done: int, total: int) -> None:
        self.progress["value"] = percent
        self.status.configure(text=f"Installing {done:,} of {total:,} files…")

    def _done(self, exe: Path, launched: bool) -> None:
        self.progress["value"] = 100
        self.status.configure(text="Installation complete.", fg=JADE)
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} v{VERSION} is installed.\n\n{exe}\n\n"
            + ("Launching now." if launched else "You can launch it from the Start Menu."),
        )
        self.root.destroy()

    def _failed(self, exc: Exception) -> None:
        self.installing = False
        self.install_button.configure(state="normal", text="Install")
        self.status.configure(text="Installation failed.", fg="#ff7b72")
        messagebox.showerror(APP_NAME, f"Installation failed:\n\n{exc}")


def main() -> int:
    root = tk.Tk()
    SetupWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
