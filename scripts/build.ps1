# Build the Windows installer:
#   1. freeze the backend with PyInstaller into a Tauri sidecar
#   2. build the Tauri app + NSIS installer (app\src-tauri\target\release\bundle\nsis)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\backend\.venv\Scripts\python.exe"
$bin = "$root\app\src-tauri\binaries"
New-Item -ItemType Directory -Force $bin | Out-Null

Push-Location "$root\backend"
# The Claude Agent SDK's bundled CLI (~250MB) is excluded: the app uses the
# Claude Code CLI installed on the user's PATH (or the path set in Settings).
# Console app on purpose: the desktop shell starts it with CREATE_NO_WINDOW, so it has a
# hidden console that every child (claude, codex, MCP servers, shells) inherits instead of
# each opening its own terminal window.
& $py -m PyInstaller --noconfirm --clean --onefile `
    --name roxy-ai-backend `
    --distpath "$root\backend\build\dist" --workpath "$root\backend\build\work" --specpath "$root\backend\build" `
    --collect-submodules uvicorn --collect-submodules keyring `
    --hidden-import win32ctypes.core `
    --exclude-module claude_agent_sdk._bundled `
    run_backend.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item "$root\backend\build\dist\roxy-ai-backend.exe" "$bin\roxy-ai-backend-x86_64-pc-windows-msvc.exe" -Force
Pop-Location

if ($env:ROXY_SKIP_TAURI) { exit 0 }  # sidecar only
Push-Location "$root\app"
npm run tauri:build
Pop-Location
Write-Host "Installer: $root\app\src-tauri\target\release\bundle\nsis"
