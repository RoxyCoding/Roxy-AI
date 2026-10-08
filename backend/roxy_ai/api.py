"""FastAPI app: REST for state changes, one WebSocket for live events.

Access control: the server binds to 127.0.0.1 only, every request needs the
per-launch random token (Authorization header, or ?t= for <img>/WebSocket),
and browser requests from origins other than the app's own are refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .adapters import build_adapters
from .adapters.base import AdapterUnavailable, ApprovalDecision
from .attachments import AttachmentStore
from .chat import ChatService, Conflict
from .config import Paths, Secrets, SettingsStore
from .db import Database
from .logs import op, tail
from .mcp_monitor import BlenderMonitor

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


class Hub:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue] = set()

    async def publish(self, event: dict) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("WebSocket client too slow; dropping event")


class State:
    """Everything the routes need, built once at startup."""

    def __init__(self, paths: Paths, token: str):
        self.paths = paths
        self.token = token
        self.hub = Hub()
        self.settings = SettingsStore(paths.settings)
        self.secrets = Secrets()
        self.db = Database(paths.db)
        self.attachments = AttachmentStore(self.db, paths.attachments)
        self.adapters = build_adapters(self.settings, self.secrets, paths.workspaces)

        async def save_image(data: bytes, mime: str, kind: str) -> dict:
            att = self.attachments.save_bytes(data, f"{kind}.{mime.split('/')[-1]}", mime, kind)
            self._prune_previews(att["id"])
            return att

        self.monitor = BlenderMonitor(self.settings, paths.logs, self.hub.publish, save_image)
        self.chat = ChatService(self.db, self.settings, paths, self.adapters, self.attachments, self.monitor,
                                self.hub.publish)

    def _prune_previews(self, keep_id: str, keep: int = 5) -> None:
        rows = [r for r in self.db._all("SELECT * FROM attachments WHERE kind='preview' ORDER BY created_at DESC")]
        for r in rows[keep:]:
            if r["id"] != keep_id:
                self.attachments.delete(r["id"])

    def active_project(self) -> dict:
        s = self.settings.get()
        project = self.db.get_project(s.get("last_project_id") or "") if s.get("last_project_id") else None
        if project is None:
            projects = self.db.list_projects()
            project = projects[0] if projects else self.db.create_project("デフォルトプロジェクト")
            self.settings.update({"last_project_id": project["id"]})
        return project


# Request bodies ------------------------------------------------------------------


class ProjectBody(BaseModel):
    name: str | None = None
    blend_path: str | None = None
    work_dir: str | None = None
    blender_host: str | None = None
    blender_port: int | None = None
    notes: str | None = None


class ConversationCreate(BaseModel):
    project_id: str
    provider: str
    model: str = ""


class ConversationPatch(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    project_id: str | None = None


class SendBody(BaseModel):
    text: str = ""
    attachment_ids: list[str] = []
    provider: str
    model: str = ""


class EditBody(SendBody):
    attachment_ids: list[str] | None = None  # None keeps the original attachments


class RetryBody(BaseModel):
    provider: str
    model: str = ""


class ApprovalBody(BaseModel):
    allow: bool
    remember: bool = False
    message: str = ""
    answers: dict[str, list[str]] = {}
    content: dict | None = None
    cancel_turn: bool = False


class LoginBody(BaseModel):
    method: str  # chatgpt | api_key
    api_key: str | None = None


class ExportBody(BaseModel):
    format: str = "md"


class RevealBody(BaseModel):
    path: str


class RefreshBody(BaseModel):
    preview: bool = True


def create_app(paths: Paths, token: str) -> FastAPI:
    state = State(paths, token)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        n = state.db.mark_interrupted_streams()
        if n:
            logger.info("Marked %d interrupted messages", n)
        for row in state.db.orphan_uploads(time.time() - 86400):
            state.attachments.delete(row["id"])
        project = state.active_project()
        await state.monitor.start(project["blender_host"], project["blender_port"])
        op("app.start", version=__version__)
        yield
        op("app.stop")
        await state.chat.shutdown()
        for a in state.adapters.values():
            try:
                await asyncio.wait_for(a.shutdown(), 10)
            except Exception as e:
                logger.warning("adapter shutdown: %s", e)
        await state.monitor.stop()
        state.db.close()

    app = FastAPI(title="Roxy Blender AI backend", version=__version__, lifespan=lifespan)
    app.state.roxy = state

    def authorized(request_token: str | None) -> bool:
        return bool(request_token) and hmac.compare_digest(request_token, token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        auth = request.headers.get("authorization", "")
        supplied = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("t")
        if not authorized(supplied):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    # Added after guard so it is the outermost layer: error responses keep CORS headers.
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(Conflict)
    async def conflict(_: Request, e: Conflict):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(AdapterUnavailable)
    async def unavailable(_: Request, e: AdapterUnavailable):
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(KeyError)
    async def not_found(_: Request, e: KeyError):
        return JSONResponse({"detail": f"見つかりません: {e}"}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # Bootstrap / settings --------------------------------------------------------------

    @app.get("/api/bootstrap")
    async def bootstrap():
        project = state.active_project()
        return {
            "version": __version__,
            "settings": state.settings.get(),
            "secrets": state.secrets.status(),
            "projects": state.db.list_projects(),
            "active_project_id": project["id"],
            "providers": [{"id": a.id, "name": a.name} for a in state.adapters.values()],
            "blender": state.monitor.public_state(),
            "runs": state.chat.active_runs(),
            "data_dir": str(paths.root),
        }

    @app.get("/api/settings")
    async def get_settings():
        return state.settings.get()

    @app.patch("/api/settings")
    async def patch_settings(patch: dict[str, Any]):
        patch.pop("last_project_id", None)
        before = state.settings.get()
        after = state.settings.update(patch)
        if before["mcp"] != after["mcp"]:
            project = state.active_project()
            await state.monitor.start(project["blender_host"], project["blender_port"])
        for pid, a in state.adapters.items():
            if before["providers"].get(pid) != after["providers"].get(pid):
                a._status = None  # type: ignore[attr-defined]  # re-probe on next status call
                # Other settings take effect with the next session; a new CLI needs a restart.
                cli_changed = before["providers"][pid].get("cli_path") != after["providers"][pid].get("cli_path")
                if cli_changed and not any(r.provider == pid for r in state.chat.runs.values()):
                    await a.shutdown()
        op("settings.update", keys=list(patch))
        return after

    # Providers -------------------------------------------------------------------------

    @app.get("/api/providers")
    async def providers(refresh: bool = False):
        enabled = state.settings.get()["providers"]

        async def one(a):
            if not enabled.get(a.id, {}).get("enabled", True):
                return {"id": a.id, "name": a.name, "available": False, "detail": "設定で無効", "models": []}
            st = await a.status(refresh=refresh)
            d = st.__dict__.copy()
            d["models"] = [m.__dict__ for m in st.models]
            return d

        return await asyncio.gather(*(one(a) for a in state.adapters.values()))

    @app.post("/api/providers/{pid}/login")
    async def login(pid: str, body: LoginBody):
        a = state.chat.adapter(pid)
        res = await a.login(body.method, body.api_key)
        op("provider.login", provider=pid, method=body.method, ok=res.get("ok"))
        return res

    @app.post("/api/providers/{pid}/logout")
    async def logout(pid: str):
        if pid == "claude":
            state.secrets.set("anthropic", None)
            state.settings.update({"providers": {"claude": {"auth_mode": "cli"}}})
            return {"ok": True}
        return await state.chat.adapter(pid).logout()

    # Projects --------------------------------------------------------------------------

    @app.get("/api/projects")
    async def list_projects():
        return state.db.list_projects()

    @app.post("/api/projects")
    async def create_project(body: ProjectBody):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        name = fields.pop("name", None) or "新しいプロジェクト"
        project = state.db.create_project(name, **fields)
        op("project.create", project=project["id"])
        return project

    @app.patch("/api/projects/{pid}")
    async def patch_project(pid: str, body: ProjectBody):
        before = state.db.get_project(pid)
        if not before:
            raise KeyError(pid)
        project = state.db.update_project(pid, {k: v for k, v in body.model_dump().items() if v is not None})
        if pid == state.active_project()["id"] and ChatService.endpoint(before) != ChatService.endpoint(project):
            await state.monitor.start(project["blender_host"], project["blender_port"])
        return project

    @app.delete("/api/projects/{pid}")
    async def delete_project(pid: str):
        if len(state.db.list_projects()) <= 1:
            raise ValueError("最後のプロジェクトは削除できません")
        for conv in state.db.list_conversations(pid):
            await state.chat.delete_conversation(conv["id"])
        state.db.delete_project(pid)
        if state.settings.get().get("last_project_id") == pid:
            state.settings.update({"last_project_id": None})
            project = state.active_project()
            await state.monitor.start(project["blender_host"], project["blender_port"])
        return {"ok": True}

    @app.post("/api/projects/{pid}/activate")
    async def activate_project(pid: str):
        project = state.db.get_project(pid)
        if not project:
            raise KeyError(pid)
        before = state.active_project()
        state.settings.update({"last_project_id": pid})
        if ChatService.endpoint(before) != ChatService.endpoint(project) or state.monitor.state["mcp"] != "running":
            await state.monitor.start(project["blender_host"], project["blender_port"])
        return project

    # Conversations -----------------------------------------------------------------------

    @app.get("/api/conversations")
    async def list_conversations(project_id: str | None = None):
        return state.db.list_conversations(project_id)

    @app.post("/api/conversations")
    async def create_conversation(body: ConversationCreate):
        if not state.db.get_project(body.project_id):
            raise KeyError(body.project_id)
        state.chat.adapter(body.provider)
        return state.db.create_conversation(body.project_id, body.provider, body.model)

    @app.get("/api/conversations/{cid}")
    async def get_conversation(cid: str):
        conv = state.db.get_conversation(cid)
        if not conv:
            raise KeyError(cid)
        messages = state.db.list_messages(cid)
        ids = [a for m in messages for a in (m.get("attachments") or [])]
        run = state.chat.runs.get(cid)
        return {
            "conversation": conv,
            "messages": messages,
            "attachments": {a["id"]: a for a in state.db.get_attachments(ids)},
            "sessions": state.db.list_ai_sessions(cid),
            "run": run.snapshot() if run else None,
        }

    @app.patch("/api/conversations/{cid}")
    async def patch_conversation(cid: str, body: ConversationPatch):
        conv = state.db.update_conversation(cid, {k: v for k, v in body.model_dump().items() if v is not None})
        if not conv:
            raise KeyError(cid)
        await state.hub.publish({"type": "conversation.updated", "conversation": conv})
        return conv

    @app.delete("/api/conversations/{cid}")
    async def delete_conversation(cid: str):
        await state.chat.delete_conversation(cid)
        await state.hub.publish({"type": "conversation.deleted", "conversation_id": cid})
        return {"ok": True}

    @app.post("/api/conversations/{cid}/messages")
    async def send(cid: str, body: SendBody):
        return await state.chat.send(cid, body.text, body.attachment_ids, body.provider, body.model)

    @app.post("/api/conversations/{cid}/messages/{mid}/edit")
    async def edit(cid: str, mid: str, body: EditBody):
        return await state.chat.edit_and_resend(cid, mid, body.text, body.attachment_ids, body.provider, body.model)

    @app.post("/api/conversations/{cid}/retry")
    async def retry(cid: str, body: RetryBody):
        return await state.chat.retry(cid, body.provider, body.model)

    @app.post("/api/conversations/{cid}/cancel")
    async def cancel(cid: str):
        return {"ok": await state.chat.cancel(cid)}

    @app.post("/api/conversations/{cid}/export")
    async def export(cid: str, body: ExportBody):
        path = state.chat.export(cid, "json" if body.format == "json" else "md")
        op("conversation.export", conversation=cid, path=str(path))
        return {"path": str(path)}

    @app.post("/api/approvals/{aid}")
    async def approval(aid: str, body: ApprovalBody):
        ok = state.chat.resolve_approval(aid, ApprovalDecision(**body.model_dump()))
        if not ok:
            raise KeyError(aid)
        return {"ok": True}

    @app.get("/api/search")
    async def search(q: str, project_id: str | None = None):
        if not q.strip():
            return []
        return state.db.search(q.strip(), project_id)

    # Attachments --------------------------------------------------------------------------

    @app.post("/api/attachments")
    async def upload(files: list[UploadFile] = File(...)):
        out = []
        for f in files:
            out.append(await state.attachments.save_upload(f))
        return out

    @app.get("/api/attachments/{aid}/file")
    async def attachment_file(aid: str, thumb: bool = False):
        att = state.db.get_attachment(aid)
        if not att:
            raise KeyError(aid)
        path = state.attachments.file_for(att, thumb=thumb)
        if path is None:
            raise KeyError(aid)
        media = "image/png" if thumb and path.name == "thumb.png" else att["mime"]
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=86400"})

    @app.delete("/api/attachments/{aid}")
    async def delete_attachment(aid: str):
        att = state.db.get_attachment(aid)
        if not att:
            raise KeyError(aid)
        if att["conversation_id"]:
            raise ValueError("送信済みメッセージの添付は削除できません")
        state.attachments.delete(aid)
        return {"ok": True}

    # Blender / MCP ---------------------------------------------------------------------------

    @app.get("/api/blender/state")
    async def blender_state():
        return state.monitor.public_state()

    @app.post("/api/blender/refresh")
    async def blender_refresh(body: RefreshBody):
        return await state.monitor.refresh(preview=body.preview)

    @app.post("/api/blender/reconnect")
    async def blender_reconnect():
        project = state.active_project()
        await state.monitor.start(project["blender_host"], project["blender_port"])
        return state.monitor.public_state()

    # System / debug ---------------------------------------------------------------------------

    @app.get("/api/logs/{name}")
    async def logs(name: str, lines: int = 200):
        return {"lines": tail(paths.logs, name, lines)}

    @app.post("/api/system/reveal")
    async def reveal(body: RevealBody):
        target = Path(body.path).resolve()
        allowed = [paths.exports.resolve(), paths.logs.resolve(), paths.root.resolve()]
        if not any(target == a or a in target.parents for a in allowed):
            raise ValueError("このパスは開けません")
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)] if target.is_file() else ["explorer", str(target)])
        return {"ok": True}

    # WebSocket ----------------------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        origin = socket.headers.get("origin")
        if (origin and origin not in ALLOWED_ORIGINS) or not authorized(socket.query_params.get("t")):
            await socket.close(code=4401)
            return
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        state.hub.clients.add(queue)
        await socket.send_json({"type": "hello", "blender": state.monitor.public_state(),
                                "runs": state.chat.active_runs()})

        async def pump():
            while True:
                await socket.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                msg = await socket.receive_json()
                if msg.get("type") == "ping":
                    await queue.put({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("ws closed: %s", e)
        finally:
            sender.cancel()
            state.hub.clients.discard(queue)

    return app
