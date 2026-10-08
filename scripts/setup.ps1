# One-time setup for development: backend venv + frontend packages.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\backend"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv venv .venv --python 3.10
    uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
} else {
    py -3.10 -m venv .venv
    .venv\Scripts\python.exe -m pip install -e ".[dev]"
}
Pop-Location
Push-Location "$root\app"
npm install
Pop-Location
Write-Host "Setup complete. Start the app with: scripts\dev.ps1"
