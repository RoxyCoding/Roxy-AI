"""Paths, settings and secrets."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The data folder keeps its original name so existing chats, settings and downloaded
# models stay where they are after the app was renamed to "Roxy AI".
APP_NAME = "RoxyBlenderAI"
KEYRING_SERVICE = "RoxyBlenderAI"


def data_dir() -> Path:
    """Per-user data directory (override with ROXY_AI_DATA_DIR)."""
    override = os.environ.get("ROXY_AI_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


class Paths:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "roxy.db"
        self.settings = root / "settings.json"
        self.attachments = root / "attachments"
        self.logs = root / "logs"
        self.exports = root / "exports"
        self.workspaces = root / "workspaces"
        self.models = root / "models"  # .gguf files for the built-in local LLM runtime
        for d in (self.attachments, self.logs, self.exports, self.workspaces, self.models):
            d.mkdir(parents=True, exist_ok=True)


def _is_mcp_checkout(path: str | Path) -> bool:
    p = Path(path)
    try:
        return "roxy-blender-mcp" in (p / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False


def _dirs_from_client_configs() -> list[str]:
    """`--directory <checkout>` from the Codex / Claude Code configs that `setup` wrote."""
    import re

    found: list[str] = []
    codex = Path.home() / ".codex" / "config.toml"
    try:
        found += re.findall(r"""["']--directory["']\s*,\s*['"]([^'"]+)['"]""", codex.read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            args = node.get("args")
            if isinstance(args, list) and "--directory" in args:
                i = args.index("--directory")
                if i + 1 < len(args) and isinstance(args[i + 1], str):
                    found.append(args[i + 1])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _default_mcp_dir() -> str:
    """Best guess at the Roxy-BlenderMCP-v2 checkout: a sibling MCP folder of
    this app, else the checkout the user's Codex / Claude Code already use."""
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        candidate = parent.parent / "MCP" / "Roxy-BlenderMCP-v2"
        if _is_mcp_checkout(candidate):
            return str(candidate)
    for candidate in _dirs_from_client_configs():
        if _is_mcp_checkout(candidate):
            return str(Path(candidate))
    return ""


try:  # written by scripts/build.ps1 for frozen builds only
    from ._build_flags import DISTRIBUTION  # type: ignore[import-not-found]
except ImportError:
    DISTRIBUTION = False

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "system",  # system | light | dark
    "default_provider": "claude",
    "providers": {
        "claude": {
            "enabled": True,
            "cli_path": "",  # empty: `claude` on PATH, else the SDK's bundled CLI
            "default_model": "default",
            "default_effort": "",  # empty: the model's own default
            # cli: the CLI's own login (subscription or env) | api_key. Builds made for other people
            # default to api_key: Anthropic does not permit offering claude.ai login in third-party apps.
            "auth_mode": "api_key" if DISTRIBUTION else "cli",
            "permission_mode": "default",
        },
        "codex": {
            "enabled": True,
            "cli_path": "",  # empty: `codex` on PATH
            "default_model": "",
            "default_effort": "",
            "sandbox": "workspace-write",
            "approval_policy": "on-request",
        },
        # Local models run through Codex (built-in lmstudio / ollama providers);
        # CLI path, sandbox and approval policy come from the codex settings above.
        "local": {
            "enabled": True,
            "runtime": "builtin",  # builtin (app-managed llama.cpp) | lmstudio | ollama
            "base_url": "",  # lmstudio/ollama: empty = the runtime's default local address
            "default_model": "",  # empty: the first model found
            "llama_server_path": "",  # empty: llama-server on PATH or from winget
            "models_dir": "",  # where downloaded models are saved; empty = <data>/models
            "model_dirs": [],  # extra folders to search for .gguf files
            "ctx_size": 32768,
            "gpu_layers": 99,  # layers offloaded to the GPU (0 = CPU only)
            # CPU offload (all free numbers; 0 / False = off)
            "cpu_layers": 0,  # keep N layers on the CPU (overrides gpu_layers)
            "n_cpu_moe": 0,  # MoE expert weights of the first N layers on the CPU
            "threads": 0,  # CPU threads, 0 = auto
            "kv_on_cpu": False,  # KV cache in system RAM
            "idle_unload_minutes": 15,  # free VRAM after this long unused (0 = never)
            "allow_shell": False,  # small models misuse the shell instead of the Blender MCP
            "default_effort": "",
        },
    },
    "mcp": {
        "server_dir": "",  # Roxy-BlenderMCP-v2 checkout; empty = auto-detect
        "command": "",  # empty: uv run --directory <server_dir> roxy-blender-mcp
        "args": [],
        "server_name": "blender",
        "inject_into_ai": True,
        "auto_preview": True,
        "preview_max_size": 800,
        "monitor_enabled": True,
    },
    # Unreal Engine 5: the editor's MCP server (HTTP, runs inside Unreal Editor).
    "unreal": {
        "enabled": True,
        "url": "http://127.0.0.1:8000/mcp",
        # Same name as the user's own CLI configs use, so injecting does not duplicate it.
        "server_name": "unreal-mcp",
        "inject_into_ai": True,
        "monitor_enabled": True,
        "auto_preview": True,
    },
    "approval": {
        # Tools the MCP marks readOnlyHint never change the scene.
        "auto_approve_readonly_mcp": True,
        "auto_approve_blender_mcp": False,
        # UE5 MCP: read-only calls (Get*/Is*/Capture* ...) run without asking; full = everything.
        "auto_approve_unreal_readonly": True,
        "auto_approve_unreal_mcp": False,
        # Full access: the AIs run without approvals or sandbox (Claude bypassPermissions,
        # Codex approval "never" + danger-full-access). Off unless the user turns it on.
        "full_access": False,
    },
    "context": {"handoff_max_chars": 40000},
    "ui": {"right_panel_open": True, "sidebar_open": True},
    "last_project_id": None,
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class SettingsStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        data: dict = {}
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.error("settings.json unreadable, using defaults: %s", e)
        merged = _merge(DEFAULT_SETTINGS, data)
        if not merged["mcp"]["server_dir"]:
            merged["mcp"]["server_dir"] = _default_mcp_dir()
        return merged

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._data = _merge(self._data, patch)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return copy.deepcopy(self._data)


class Secrets:
    """API keys live in the OS credential store (Windows Credential Manager), never in files."""

    NAMES = ("anthropic",)

    def get(self, name: str) -> str | None:
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, name)
        except Exception as e:  # no backend available
            logger.warning("keyring unavailable: %s", e)
            return None

    def set(self, name: str, value: str | None) -> None:
        import keyring

        if value:
            keyring.set_password(KEYRING_SERVICE, name, value)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, name)
            except Exception:
                pass

    def status(self) -> dict[str, bool]:
        return {name: bool(self.get(name)) for name in self.NAMES}
