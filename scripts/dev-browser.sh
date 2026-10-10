#!/usr/bin/env bash
# Debug without Tauri: backend with a fixed token + Vite, open the printed URL in a browser.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
port="${1:-47821}"
token="${2:-dev-$RANDOM}"
(cd "$root/backend" && exec .venv/bin/python -m roxy_ai --port "$port" --token "$token" --debug) &
backend=$!
trap 'kill $backend 2>/dev/null || true' EXIT
echo "Open: http://127.0.0.1:1420/?port=$port&token=$token"
cd "$root/app"
npm run dev
