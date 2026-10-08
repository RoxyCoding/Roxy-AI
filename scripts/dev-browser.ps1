# Debug without Tauri: backend with a fixed token + Vite, open the printed URL in a browser.
param([int]$Port = 47821, [string]$Token = "dev-$(Get-Random)")
$root = Split-Path -Parent $PSScriptRoot
$backend = Start-Process -PassThru -NoNewWindow "$root\backend\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "roxy_ai", "--port", $Port, "--token", $Token, "--debug" -WorkingDirectory "$root\backend"
Write-Host "Open: http://127.0.0.1:1420/?port=$Port&token=$Token"
try {
    Push-Location "$root\app"
    npm run dev
} finally {
    Pop-Location
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
}
