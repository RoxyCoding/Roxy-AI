# Build the Windows installer:
#   1. freeze the backend with PyInstaller into a Tauri sidecar
#   2. build the Tauri app + NSIS installer (app\src-tauri\target\release\bundle\nsis)
#
# Options
#   -Distribution   Build for other people: Claude defaults to API-key login, as Anthropic's
#                   terms do not allow offering claude.ai (subscription) login in third-party apps.
#   Code signing    Set ROXY_SIGN_THUMBPRINT to the SHA1 thumbprint of a code-signing certificate
#                   in the Windows certificate store (Current User or Local Machine). The backend,
#                   the app and the installer are then signed and timestamped. Optional:
#                   ROXY_SIGN_TIMESTAMP (default http://timestamp.digicert.com).
#   ROXY_SKIP_TAURI Build the backend sidecar only.
param([switch]$Distribution)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\backend\.venv\Scripts\python.exe"
$bin = "$root\app\src-tauri\binaries"
$flags = "$root\backend\roxy_ai\_build_flags.py"
$thumb = $env:ROXY_SIGN_THUMBPRINT
$timestamp = if ($env:ROXY_SIGN_TIMESTAMP) { $env:ROXY_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
New-Item -ItemType Directory -Force $bin | Out-Null

function Sign-File($path) {
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "signtool.exe not found (install the Windows SDK) - needed because ROXY_SIGN_THUMBPRINT is set" }
    & $signtool.FullName sign /sha1 $thumb /fd sha256 /tr $timestamp /td sha256 $path
    if ($LASTEXITCODE -ne 0) { throw "signing failed: $path" }
}

# Build-time flags baked into the frozen backend (removed again afterwards).
"DISTRIBUTION = $(if ($Distribution) { 'True' } else { 'False' })" | Set-Content -Encoding utf8 $flags
try {
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
        --hidden-import win32ctypes.core --hidden-import roxy_ai._build_flags `
        --exclude-module claude_agent_sdk._bundled `
        run_backend.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    Pop-Location
} finally {
    Remove-Item $flags -ErrorAction SilentlyContinue
}
$sidecar = "$bin\roxy-ai-backend-x86_64-pc-windows-msvc.exe"
Copy-Item "$root\backend\build\dist\roxy-ai-backend.exe" $sidecar -Force
if ($thumb) { Sign-File $sidecar }

if ($env:ROXY_SKIP_TAURI) { exit 0 }  # sidecar only
Push-Location "$root\app"
if ($thumb) {
    # Tauri signs the app executable and the installer with the same certificate.
    $override = @{ bundle = @{ windows = @{ certificateThumbprint = $thumb; digestAlgorithm = "sha256"; timestampUrl = $timestamp } } } |
        ConvertTo-Json -Depth 5 -Compress
    $overrideFile = "$root\app\src-tauri\tauri.signing.conf.json"
    $override | Set-Content -Encoding utf8 $overrideFile
    try {
        npx tauri build --config src-tauri/tauri.release.conf.json --config src-tauri/tauri.signing.conf.json
        if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
    } finally {
        Remove-Item $overrideFile -ErrorAction SilentlyContinue
    }
} else {
    npm run tauri:build
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
    Write-Host "Note: unsigned build. Windows SmartScreen will warn users; set ROXY_SIGN_THUMBPRINT to sign."
}
Pop-Location
if ($Distribution) {
    Write-Host "Distribution build: Claude defaults to API-key login (Anthropic does not permit offering claude.ai login in third-party apps)."
}
Write-Host "Installer: $root\app\src-tauri\target\release\bundle\nsis"
