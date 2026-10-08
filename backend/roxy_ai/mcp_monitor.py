"""The GUI's own connection to Roxy-BlenderMCP-v2.

The AIs start their own copy of the MCP server and call it directly. This
module starts one more copy, as a plain MCP client, purely for what the GUI
shows: connection state, the tool list, scene facts and viewport previews. It
only calls tools the MCP itself provides (get_addon_status, get_scene_info,
viewport_capture) and never edits the scene.

The Blender addon accepts several clients at once and runs their commands one
by one on Blender's main thread, so this does not interfere with the AIs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import SettingsStore

logger = logging.getLogger(__name__)

PROBE_INTERVAL = 10.0
STATUS_TIMEOUT = 25.0
RECHECK_UNRESPONSIVE = 30.0
UNRESPONSIVE_MSG = (
    "Blender は接続を受け付けていますが、コマンドに応答しません。"
    "Blender でダイアログやモーダル操作が開いていないか、Roxy Blender MCP パネルの接続状態を確認してください。"
)
SCENE_FIELDS = ["location", "size", "details", "materials", "modifiers"]
HEADER_RE = re.compile(
    r"^Scene '(?P<scene>.*)' \| (?P<counts>.*?) \| active (?P<active>.*?) \| selected (?P<selected>.*?) \| mode (?P<mode>\S+)"
)


def mcp_server_command(settings: dict, host: str, port: int) -> dict:
    """How to start Roxy-BlenderMCP-v2 — shared by the monitor and the AIs."""
    mcp = settings["mcp"]
    env = {"BLENDER_HOST": host, "BLENDER_PORT": str(port)}
    if mcp.get("command"):
        return {"command": mcp["command"], "args": list(mcp.get("args") or []), "env": env}
    server_dir = mcp.get("server_dir") or ""
    # Prefer the checkout's installed entry point: `uv run` re-syncs the venv on
    # start and fails while another instance holds roxy-blender-mcp.exe open.
    bin_dir = Path(server_dir) / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    exe = bin_dir / ("roxy-blender-mcp.exe" if os.name == "nt" else "roxy-blender-mcp")
    if server_dir and exe.is_file():
        return {"command": str(exe), "args": [], "env": env}
    uv = shutil.which("uv") or "uv"
    return {"command": uv, "args": ["run", "--directory", server_dir, "roxy-blender-mcp"], "env": env}


def parse_scene_header(text: str) -> dict:
    lines = text.splitlines()
    out: dict[str, Any] = {}
    if lines:
        m = HEADER_RE.match(lines[0])
        if m:
            out = m.groupdict()
            out["selected"] = [] if out["selected"] == "none" else [s.strip() for s in out["selected"].split(",")]
            out["active"] = None if out["active"] == "none" else out["active"]
    if len(lines) > 1 and " | engine " in lines[1]:
        out["file_line"] = lines[1]
    # Per-object rows follow a "Showing N of M (...)" line: "name | type | ...".
    rows = []
    in_rows = False
    for line in lines:
        if line.startswith("Showing "):
            in_rows = True
            out["columns"] = line[line.find("(") + 1: line.rfind(")")].rstrip(":").split(" | ")
            continue
        if in_rows and line.strip() and not line.startswith("..."):
            rows.append(line)
    out["rows"] = rows
    return out


class BlenderMonitor:
    def __init__(self, settings: SettingsStore, log_dir: Path,
                 publish: Callable[[dict], Awaitable[None]],
                 save_image: Callable[[bytes, str, str], Awaitable[dict]]):
        self._settings = settings
        self._log_dir = log_dir
        self._publish = publish
        self._save_image = save_image
        self._task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._call_lock = asyncio.Lock()
        self._host = "localhost"
        self._port = 9876
        self.state: dict[str, Any] = {
            "mcp": "stopped",  # stopped | starting | running | error
            "blender": "unknown",  # unknown | connected | disconnected | unresponsive
            "endpoint": "",
            "error": "",
            "server": {},
            "addon": None,
            "tools": [],
            "scene": None,
            "preview": None,
            "updated_at": 0,
        }
        self._readonly_tools: set[str] = set()
        self._refreshing = False
        self._last_refresh = 0.0

    # Lifecycle ---------------------------------------------------------------

    async def start(self, host: str, port: int) -> None:
        await self.stop()
        if not self._settings.get()["mcp"].get("monitor_enabled", True):
            await self._set(mcp="stopped", error="モニター接続は設定で無効になっています")
            return
        self._host, self._port = host, int(port)
        self._stop = asyncio.Event()
        await self._set(endpoint=f"{host}:{port}", mcp="starting", blender="unknown", error="", scene=None)
        self._task = asyncio.create_task(self._run())
        self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        self._stop.set()
        for task in (self._task, self._probe_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = self._probe_task = None
        self._session = None
        self._ready.clear()
        if self.state["mcp"] != "stopped":
            await self._set(mcp="stopped")

    async def restart(self) -> None:
        await self.start(self._host, self._port)

    def is_readonly_tool(self, tool: str) -> bool:
        return tool in self._readonly_tools

    async def _set(self, **changes: Any) -> None:
        self.state.update(changes)
        self.state["updated_at"] = time.time()
        await self._publish({"type": "blender.state", "state": self.public_state()})

    def public_state(self) -> dict:
        return dict(self.state)

    async def _run(self) -> None:
        backoff = 2.0
        settings = self._settings.get()
        server_dir = settings["mcp"].get("server_dir") or ""
        cmd = mcp_server_command(settings, self._host, self._port)
        if not settings["mcp"].get("command") and not (server_dir and Path(server_dir, "pyproject.toml").is_file()):
            await self._set(mcp="error", error="Roxy-BlenderMCP-v2 のフォルダが見つかりません（設定 → Blender MCP）。")
            return
        while not self._stop.is_set():
            errlog = open(self._log_dir / "mcp_monitor.log", "a", encoding="utf-8")
            try:
                params = StdioServerParameters(
                    command=cmd["command"],
                    args=cmd["args"],
                    env={**os.environ, **cmd["env"], "BLENDER_MCP_APPS": "1"},
                    cwd=server_dir or None,
                )
                await self._set(mcp="starting", error="")
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        init = await asyncio.wait_for(session.initialize(), 120)
                        tools = await session.list_tools()
                        self._readonly_tools = {
                            t.name for t in tools.tools if t.annotations and t.annotations.readOnlyHint
                        }
                        self._session = session
                        self._ready.set()
                        backoff = 2.0
                        await self._set(
                            mcp="running",
                            server={"name": init.serverInfo.name, "version": init.serverInfo.version},
                            tools=[
                                {
                                    "name": t.name,
                                    "description": (t.description or "").strip().split("\n")[0][:200],
                                    "read_only": t.name in self._readonly_tools,
                                    "app_only": ((t.meta or {}).get("ui") or {}).get("visibility") == ["app"],
                                }
                                for t in tools.tools
                            ],
                        )
                        asyncio.create_task(self.refresh(preview=False))
                        await self._stop.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MCP monitor failed: %s", e)
                await self._set(mcp="error", error=f"MCPサーバーに接続できません: {e}")
            finally:
                self._session = None
                self._ready.clear()
                errlog.close()
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _probe_loop(self) -> None:
        """Cheap TCP check of the addon socket; MCP calls only when the state changes."""
        while not self._stop.is_set():
            ok = await self._tcp_alive()
            current = self.state["blender"]
            if not ok:
                if current != "disconnected":
                    await self._set(blender="disconnected")
            elif self._ready.is_set() and not self._refreshing and (
                current in ("disconnected", "unknown")
                or (current == "unresponsive" and time.time() - self._last_refresh > RECHECK_UNRESPONSIVE)
            ):
                # The socket is up; only a real command tells whether Blender answers.
                asyncio.create_task(self.refresh(preview=False))
            await asyncio.sleep(PROBE_INTERVAL)

    async def _tcp_alive(self) -> bool:
        # On Windows "localhost" tries ::1 first, which hangs rather than being
        # refused when Blender listens on IPv4 only; try 127.0.0.1 first.
        hosts = ["127.0.0.1", "::1"] if self._host == "localhost" else [self._host]
        for host in hosts:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, self._port), 1.5)
            except Exception:
                continue
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        return False

    # Calls -------------------------------------------------------------------

    async def call(self, tool: str, args: dict | None = None, timeout: float = 180) -> Any:
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), 30)
            except asyncio.TimeoutError:
                raise RuntimeError(self.state.get("error") or "MCPサーバーに接続されていません")
        assert self._session is not None
        async with self._call_lock:
            return await asyncio.wait_for(self._session.call_tool(tool, args or {}), timeout)

    @staticmethod
    def _text(result: Any) -> str:
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        # The MCP appends its session rules to the first reply; they're for the model, not the panel.
        cut = text.find("\n---\n# Roxy Blender MCP session rules")
        return (text[:cut] if cut >= 0 else text).strip()

    async def refresh(self, preview: bool = True) -> dict:
        """Addon status + scene facts (+ a viewport capture)."""
        errors = []
        self._refreshing = True
        self._last_refresh = time.time()
        try:
            res = await self.call("get_addon_status", {}, timeout=STATUS_TIMEOUT)
            text = self._text(res)
            if res.isError or "could not connect" in text.lower():
                await self._set(blender="disconnected", addon={"text": text})
            else:
                await self._set(blender="connected", addon={"text": text})
        except asyncio.TimeoutError:
            await self._set(blender="unresponsive", addon={"text": UNRESPONSIVE_MSG})
            errors.append(UNRESPONSIVE_MSG)
        except Exception as e:
            errors.append(str(e))
        finally:
            self._refreshing = False
        if self.state["blender"] == "connected":
            try:
                await self.scene_info()
            except Exception as e:
                errors.append(str(e))
            if preview:
                try:
                    await self.capture_preview()
                except Exception as e:
                    errors.append(str(e))
        return {"ok": not errors, "errors": errors, "state": self.public_state()}

    async def scene_info(self) -> dict:
        res = await self.call("get_scene_info", {"fields": SCENE_FIELDS + ["settings"], "limit": 40}, timeout=60)
        text = self._text(res)
        if res.isError or text.startswith("Error"):
            raise RuntimeError(text or "get_scene_info failed")
        scene = {"text": text, **parse_scene_header(text), "at": time.time()}
        await self._set(scene=scene)
        return scene

    async def capture_preview(self, auto: bool = False) -> dict | None:
        max_size = int(self._settings.get()["mcp"].get("preview_max_size") or 800)
        res = await self.call("viewport_capture", {"max_size": max_size, "auto": auto}, timeout=90)
        if res.isError:
            raise RuntimeError(self._text(res) or "viewport_capture failed")
        for c in res.content:
            if getattr(c, "type", "") == "image":
                att = await self._save_image(base64.b64decode(c.data), c.mimeType or "image/png", "preview")
                preview = {"attachment_id": att["id"], "at": time.time(), "info": res.structuredContent or {}}
                await self._set(preview=preview)
                return preview
        return None
