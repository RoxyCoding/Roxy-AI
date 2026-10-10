#!/usr/bin/env bash
# Build the macOS app (.app + .dmg in app/src-tauri/target/release/bundle):
#   1. freeze the backend with PyInstaller into a Tauri sidecar
#   2. build the Tauri app (ad-hoc signed; see README for Gatekeeper)
#
# Options
#   --distribution  Build for other people: Claude defaults to API-key login, as Anthropic's
#                   terms do not allow offering claude.ai (subscription) login in third-party apps.
#   ROXY_SKIP_TAURI=1  Build the backend sidecar only.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
distribution=False
[[ "${1:-}" == "--distribution" ]] && distribution=True
py="$root/backend/.venv/bin/python"
bin="$root/app/src-tauri/binaries"
flags="$root/backend/roxy_ai/_build_flags.py"
triple="$(rustc -vV | sed -n 's/^host: //p')"
mkdir -p "$bin"

# Build-time flags baked into the frozen backend (removed again afterwards).
echo "DISTRIBUTION = $distribution" > "$flags"
trap 'rm -f "$flags"' EXIT
(
    cd "$root/backend"
    # The Claude Agent SDK's bundled CLI (~250MB) is excluded: the app uses the
    # Claude Code CLI installed on the user's PATH (or the path set in Settings).
    "$py" -m PyInstaller --noconfirm --clean --onefile \
        --name roxy-ai-backend \
        --distpath "$root/backend/build/dist" --workpath "$root/backend/build/work" --specpath "$root/backend/build" \
        --collect-submodules uvicorn --collect-submodules keyring \
        --hidden-import roxy_ai._build_flags \
        --exclude-module claude_agent_sdk._bundled \
        run_backend.py
)
cp "$root/backend/build/dist/roxy-ai-backend" "$bin/roxy-ai-backend-$triple"

[[ -n "${ROXY_SKIP_TAURI:-}" ]] && exit 0  # sidecar only
cd "$root/app"
npm run tauri:build
if [[ "$distribution" == True ]]; then
    echo "Distribution build: Claude defaults to API-key login (Anthropic does not permit offering claude.ai login in third-party apps)."
fi
echo "App: $root/app/src-tauri/target/release/bundle/macos  DMG: $root/app/src-tauri/target/release/bundle/dmg"
