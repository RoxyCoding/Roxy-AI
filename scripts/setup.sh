#!/usr/bin/env bash
# One-time setup for development on macOS: backend venv + frontend packages.
# Usage:  ./scripts/setup.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/backend"
if command -v uv >/dev/null 2>&1; then
    uv venv .venv --python 3.10
    uv pip install --python .venv/bin/python -e ".[dev]"
else
    python3 -m venv .venv
    .venv/bin/python -m pip install -e ".[dev]"
fi
cd "$root/app"
npm install
echo "Setup complete. Start the app with: ./scripts/dev.sh"
