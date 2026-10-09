"""Additional MCP servers (e.g. Roxy Substance 3D Painter MCP) handed to the AIs.

Each entry in settings["extra_mcp"]["servers"] is either a stdio server (a checkout with
a venv, or an explicit command) or an HTTP server (url). The app only passes it to the AI,
probes the app it drives (TCP port) for the status pills and applies the approval rules;
the server itself does all the work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

PROBE_INTERVAL = 5.0

# Known servers: detected next to this app (..\MCP\<folder>) and added once.
PRESETS: dict[str, dict] = {
    "substance-painter": {
        "name": "substance-painter",
        "label": "Substance 3D Painter",
        "enabled": True,
        "folder": "Roxy-Substance-3D-Painter-MCP",
        "script": "roxy-painter-mcp",
        "command": "",
        "args": [],
        "url": "",
        "probe_host": "127.0.0.1",
        "probe_port": 9877,
        "read_only_prefixes": ["get_", "list_", "search_", "check_", "wait_until_idle", "screenshot",
                               "capture_multi_view"],
        "auto_approve_readonly": True,
        "auto_approve_all": False,
        "prompt": "Adobe Substance 3D Painter のテクスチャリング（プロジェクト作成、ベイク、レイヤー、マスク、"
                  "スマートマテリアル、テクスチャ書き出し）は、MCP サーバー「substance-painter」のツールで行ってください。"
                  "Blender で作ったモデルは Blender MCP 側で書き出してから Painter に読み込んでください。",
        "offline_hint": "現在 Substance 3D Painter に接続されていません。Painter の操作が必要な場合は、"
                        "Painter の起動（ブリッジプラグイン導入済み）をユーザーに依頼してください。",
    },
}


def _is_checkout(path: Path, script: str) -> bool:
    try:
        return script in (path / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False


def detect_presets(settings: dict) -> list[dict]:
    """Presets whose checkout sits next to this app and that were never offered before."""
    conf = settings.get("extra_mcp") or {}
    seen = set(conf.get("auto_detected") or [])
    names = {s.get("name") for s in conf.get("servers") or []}
    here = Path(__file__).resolve()
    found = []
    for pid, preset in PRESETS.items():
        if pid in seen or preset["name"] in names:
            continue
        for parent in list(here.parents)[:6]:
            candidate = parent.parent / "MCP" / preset["folder"]
            if _is_checkout(candidate, preset["script"]):
                entry = {k: v for k, v in preset.items() if k != "folder"}
                entry["server_dir"] = str(candidate)
                found.append(entry)
                break
    return found


def enabled_servers(settings: dict) -> list[dict]:
    return [s for s in (settings.get("extra_mcp") or {}).get("servers") or []
            if s.get("enabled", True) and s.get("name")]


def server_spec(entry: dict) -> dict | None:
    """MCP client config for one entry: {"url": ...} or {"command", "args", "env"}."""
    if entry.get("url"):
        return {"url": entry["url"]}
    env = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
    if entry.get("command"):
        return {"command": entry["command"], "args": list(entry.get("args") or []), "env": env}
    server_dir, script = entry.get("server_dir") or "", entry.get("script") or ""
    if not (server_dir and script):
        return None
    # The venv executable directly: `uv run` would fight other clients over the lock file.
    exe = Path(server_dir) / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        script + (".exe" if os.name == "nt" else ""))
    if exe.is_file():
        return {"command": str(exe), "args": [], "env": env}
    uv = shutil.which("uv") or "uv"
    return {"command": uv, "args": ["run", "--no-sync", "--directory", server_dir, script], "env": env}


def is_read_only(entry: dict, tool: str) -> bool:
    return any(tool.startswith(p) for p in entry.get("read_only_prefixes") or [])


def find(settings: dict, server: str) -> dict | None:
    return next((s for s in enabled_servers(settings) if s["name"] == server), None)


class ExtraMcpMonitor:
    """Probes the apps behind the extra servers (TCP connect) for the status pills."""

    def __init__(self, settings, publish: Callable[[dict], Awaitable[None]]):
        self._settings = settings
        self._publish = publish
        self._task: asyncio.Task | None = None
        self.state: dict[str, str] = {}  # server name -> connected | offline

    def public_state(self) -> dict:
        return dict(self.state)

    def status(self, name: str) -> str | None:
        return self.state.get(name)

    async def start(self) -> None:
        await self.stop()
        self.state = {}
        if any(s.get("probe_port") for s in enabled_servers(self._settings.get())):
            self._task = asyncio.create_task(self._loop())
        await self._publish({"type": "extra_mcp.state", "state": self.public_state()})

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _probe(self, host: str, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
            writer.close()
            return True
        except Exception:
            return False

    async def _loop(self) -> None:
        while True:
            state = {}
            for s in enabled_servers(self._settings.get()):
                if s.get("probe_port"):
                    ok = await self._probe(s.get("probe_host") or "127.0.0.1", int(s["probe_port"]))
                    state[s["name"]] = "connected" if ok else "offline"
            if state != self.state:
                self.state = state
                await self._publish({"type": "extra_mcp.state", "state": self.public_state()})
            await asyncio.sleep(PROBE_INTERVAL)
