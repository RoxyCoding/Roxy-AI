"""The GUI's own connection to the Unreal Editor's MCP server.

The AIs use the UE MCP (an HTTP MCP server running inside the editor) directly
for game development. This module only reads editor state for the GUI:
connection, available toolsets, selected actors, PIE state and a viewport
capture. It never edits the level. Everything goes through the MCP's own
tools (`list_toolsets`, `call_tool` with EditorToolset.EditorAppToolset).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import SettingsStore

logger = logging.getLogger(__name__)

APP_TOOLSET = "EditorToolset.EditorAppToolset"
PROBE_INTERVAL = 10.0
CALL_TIMEOUT = 30.0
NO_ANNOTATIONS = {"gridSpacing": 0, "gridExtent": 0, "gridHeight": 0, "maxLabelDistance": 0,
                  "classFilter": {"refPath": ""}, "maxLabels": 0}


def actor_name(ref_path: str) -> str:
    """'/Game/Map.Map:PersistentLevel.StaticMeshActor_1' -> 'StaticMeshActor_1'."""
    return ref_path.rsplit(".", 1)[-1] if ref_path else ""


def level_of(ref_path: str) -> str:
    """'/Game/Map.Map:PersistentLevel.X' -> '/Game/Map'."""
    return ref_path.split(":", 1)[0].rsplit(".", 1)[0] if ":" in ref_path else ""


# Inner tool names that only read editor state. Anything else (Set*, Create*, Spawn*,
# StartPIE, Delete*, Save*, Python, ...) still asks for approval.
_READ_ONLY_PREFIXES = ("Get", "Is", "Has", "Search", "Find", "List", "Describe", "Read", "Query", "Capture",
                       "WorldPosToScreenCoords", "ScreenCoordsToWorld", "get_", "is_", "has_", "list_",
                       "find_", "search_", "describe_", "read_", "inspect_", "query_", "capture_")


def unreal_call_is_read_only(tool: str, args) -> bool:
    """Whether a UE MCP call only reads (for the optional auto-approval)."""
    if tool in ("list_toolsets", "describe_toolset"):
        return True
    if tool != "call_tool" or not isinstance(args, dict):
        return False
    inner = str(args.get("tool_name") or "")
    if args.get("toolset_name") and "Programmatic" in str(args.get("toolset_name")):
        return False  # runs a script that can call anything
    return inner.startswith(_READ_ONLY_PREFIXES)


def parse_toolsets(text: str) -> list[str]:
    names = []
    for line in (text or "").splitlines():
        if line.startswith("- ") and ":" in line:
            names.append(line[2:].split(":", 1)[0].strip())
    return names


class UnrealMonitor:
    def __init__(self, settings: SettingsStore, publish: Callable[[dict], Awaitable[None]],
                 save_image: Callable[[bytes, str, str], Awaitable[dict]]):
        self._settings = settings
        self._publish = publish
        self._save_image = save_image
        self._task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._reconnect = asyncio.Event()
        self._lock = asyncio.Lock()
        self.state: dict[str, Any] = {
            "status": "disabled",  # disabled | waiting | connecting | connected | error
            "url": "",
            "error": "",
            "toolsets": [],
            "editor": None,  # {"level", "selected", "pie"}
            "preview": None,
            "updated_at": 0,
        }

    def _conf(self) -> dict:
        return self._settings.get().get("unreal", {})

    async def _set(self, **changes: Any) -> None:
        self.state.update(changes)
        self.state["updated_at"] = time.time()
        await self._publish({"type": "unreal.state", "state": dict(self.state)})

    def public_state(self) -> dict:
        return dict(self.state)

    # Lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        await self.stop()
        conf = self._conf()
        if not conf.get("enabled", True) or not conf.get("monitor_enabled", True):
            await self._set(status="disabled", url=conf.get("url", ""), error="", editor=None)
            return
        self._stop = asyncio.Event()
        await self._set(status="waiting", url=conf.get("url", ""), error="", editor=None)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._session = None
        self._ready.clear()

    async def _editor_up(self, url: str) -> bool:
        u = urlparse(url)
        host = "127.0.0.1" if u.hostname in (None, "localhost") else u.hostname
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, u.port or 80), 1.5)
            writer.close()
            return True
        except Exception:
            return False

    async def _run(self) -> None:
        """Wait for the editor's MCP server, keep one session open, reconnect when it goes away."""
        url = self._conf().get("url") or "http://127.0.0.1:8000/mcp"
        while not self._stop.is_set():
            if not await self._editor_up(url):
                if self.state["status"] != "waiting":
                    await self._set(status="waiting", editor=None,
                                    error="Unreal Editor の MCP サーバーに接続できません（エディタが起動していないか、MCP プラグインが無効）。")
                await asyncio.sleep(PROBE_INTERVAL)
                continue
            try:
                await self._set(status="connecting", error="")
                async with streamable_http_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), 30)
                        self._session = session
                        self._ready.set()
                        listed = await session.call_tool("list_toolsets", {})
                        await self._set(status="connected", toolsets=parse_toolsets(self._text(listed)))
                        asyncio.create_task(self.refresh(preview=False))
                        # Stay connected while the editor is up.
                        while not self._stop.is_set():
                            await asyncio.sleep(PROBE_INTERVAL)
                            if not await self._editor_up(url):
                                break
            except asyncio.CancelledError:
                raise
            except BaseException as e:  # anyio wraps transport errors in groups
                logger.info("UE MCP session ended: %s", e)
            finally:
                self._session = None
                self._ready.clear()
            if not self._stop.is_set():
                await self._set(status="waiting", editor=None)
                await asyncio.sleep(2)

    # Calls -------------------------------------------------------------------------------

    @staticmethod
    def _text(result: Any) -> str:
        return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")

    async def app_tool(self, tool: str, args: dict | None = None) -> Any:
        """Call EditorAppToolset.<tool>; returns its returnValue."""
        if not self._ready.is_set() or self._session is None:
            raise RuntimeError("Unreal Editor に接続されていません")
        async with self._lock:
            res = await asyncio.wait_for(
                self._session.call_tool("call_tool", {"toolset_name": APP_TOOLSET, "tool_name": tool, "arguments": args or {}}),
                CALL_TIMEOUT)
        text = self._text(res)
        if res.isError:
            raise RuntimeError(text or f"{tool} failed")
        try:
            return json.loads(text).get("returnValue") if text else None
        except json.JSONDecodeError:
            return text

    async def refresh(self, preview: bool = True) -> dict:
        errors: list[str] = []
        try:
            selected = await self.app_tool("GetSelectedActors") or []
            pie = bool(await self.app_tool("IsPIERunning"))
            paths = [a.get("refPath", "") for a in selected if isinstance(a, dict)]
            level = next((level_of(p) for p in paths if level_of(p)), "")
            if not level:
                visible = await self.app_tool("GetVisibleActors") or []
                level = next((level_of(a.get("refPath", "")) for a in visible if isinstance(a, dict)), "")
            await self._set(editor={"level": level, "selected": [actor_name(p) for p in paths],
                                    "selected_paths": paths, "pie": pie, "at": time.time()})
        except Exception as e:
            errors.append(str(e))
        if preview and not errors:
            try:
                await self.capture_preview()
            except Exception as e:
                errors.append(str(e))
        return {"ok": not errors, "errors": errors, "state": self.public_state()}

    async def capture_preview(self) -> dict | None:
        camera = await self.app_tool("GetCameraTransform")
        result = await self.app_tool("CaptureViewport", {"captureTransform": camera, "annotations": NO_ANNOTATIONS,
                                                         "bShowUI": False})
        image = (result or {}).get("image") or {}
        if not image.get("data"):
            raise RuntimeError("ビューポートを取得できませんでした（PIE中は取得できない場合があります）")
        att = await self._save_image(base64.b64decode(image["data"]), image.get("mimeType") or "image/png", "preview")
        preview = {"attachment_id": att["id"], "at": time.time()}
        await self._set(preview=preview)
        return preview
