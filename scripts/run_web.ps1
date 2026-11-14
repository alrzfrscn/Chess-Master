# Launch the multi-user Chess Master web server on the LAN.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$env:PYTHONPATH = Join-Path $root "src"
$port = 8000
$lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -ne "127.0.0.1" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.AddressState -eq "Preferred"
    } |
    Select-Object -First 1 -ExpandProperty IPAddress

Write-Host "Chess Master web server" -ForegroundColor Green
Write-Host "  Local: http://127.0.0.1:$port"
if ($lanIp) {
    Write-Host "  LAN:   http://${lanIp}:$port"
} else {
    Write-Host "  LAN:   (no active network adapter found)"
}
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray

& $python -m chess_master --serve --host 0.0.0.0 --port $port
