# Create a "Chess Master" shortcut on the current user's Desktop.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "dist\ChessMaster\ChessMaster.exe"
if (-not (Test-Path $exe)) {
    throw "ChessMaster.exe was not found. Build it first: .\.venv\Scripts\python.exe scripts\build_exe.py"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "Chess Master.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path -Parent $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = "Chess Master - Precision Play"
$shortcut.Save()

Write-Host "Created shortcut: $link" -ForegroundColor Green
