#!/usr/bin/env bash
# Run the desktop app in development mode (Tauri starts Vite and the backend).
set -euo pipefail
cd "$(dirname "$0")/../app"
npm run tauri dev
