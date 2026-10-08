# Run the desktop app in development mode (Tauri starts Vite and the backend).
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\app"
npm run tauri dev
Pop-Location
