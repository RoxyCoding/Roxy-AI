"""OpenAI Codex via the official `codex app-server` (JSON-RPC over stdio).

app-server is the interface Codex provides for rich clients: threads, turns,
streaming items, interrupt, approvals and model listing. Authentication is the
Codex CLI's own (ChatGPT sign-in or API key, stored by Codex itself); this app
can start those flows through app-server but never stores OpenAI credentials.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import re
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..config import SettingsStore
from ..procs import NO_WINDOW, resolve_cli
from .base import (
    AdapterUnavailable,
    AIAdapter,
    ApprovalDecision,
    ApprovalFn,
    ApprovalRequest,
    EventSink,
    InputPart,
    ModelInfo,
    ProviderStatus,
    RunRequest,
    RunResult,
    SessionInfo,
    SessionLostError,
    StatusUpdate,
    TextDelta,
    ToolEnd,
    ToolStart,
    Usage,
)

logger = logging.getLogger(__name__)

LOGIN_HINT = "設定 → AI から「ChatGPTでログイン」を実行するか、ターミナルで `codex login` を実行してください。"


class RpcError(Exception):
    def __init__(self, error: dict):
        self.error = error
        super().__init__(error.get("message") or json.dumps(error, ensure_ascii=False))


class AppServer:
    """One `codex app-server` process shared by all conversations."""

    def __init__(self, exe: str, on_notification, on_request):
        self._exe = exe
        self._on_notification = on_notification
        self._on_request = on_request
        self._proc: asyncio.subprocess.Process | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self.loaded_threads: set[str] = set()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> dict:
        self._proc = await asyncio.create_subprocess_exec(
            self._exe,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=128 * 1024 * 1024,
            creationflags=NO_WINDOW,
        )
        self._reader = asyncio.create_task(self._read_loop())
        init = await self.request(
            "initialize",
            {"clientInfo": {"name": "roxy_blender_ai", "title": "Roxy AI", "version": __version__}},
            timeout=60,
        )
        await self._send({"method": "initialized"})
        return init

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), 5)
            except Exception:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        if self._reader:
            self._reader.cancel()
        self._fail_pending("app-server stopped")

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AdapterUnavailable(f"Codex app-server が終了しました: {reason}"))
        self._pending.clear()

    async def _send(self, msg: dict) -> None:
        if not self.alive:
            raise AdapterUnavailable("Codex app-server が起動していません")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float | None = 120) -> Any:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def respond(self, rid: Any, result: Any = None, error: dict | None = None) -> None:
        msg: dict = {"id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        await self._send(msg)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("codex: non-JSON line %r", line[:200])
                    continue
                if "method" in msg and "id" in msg:
                    asyncio.create_task(self._handle_request(msg))
                elif "method" in msg:
                    try:
                        await self._on_notification(msg["method"], msg.get("params") or {})
                    except Exception:
                        logger.exception("codex notification handler failed")
                elif "id" in msg:
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RpcError(msg["error"]))
                        else:
                            fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("codex read loop crashed")
        finally:
            self.loaded_threads.clear()
            self._fail_pending("stdout closed")
            await self._on_notification("__exit__", {})

    async def _handle_request(self, msg: dict) -> None:
        try:
            result = await self._on_request(msg["method"], msg.get("params") or {})
            await self.respond(msg["id"], result=result)
        except NotImplementedError:
            await self.respond(msg["id"], error={"code": -32601, "message": f"unsupported: {msg['method']}"})
        except Exception as e:
            logger.exception("codex server request failed")
            try:
                await self.respond(msg["id"], error={"code": -32000, "message": str(e)})
            except Exception:
                pass


@dataclass
class _Turn:
    thread_id: str
    emit: EventSink
    approve: ApprovalFn
    turn_id: str = ""
    done: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = ""
    error: str = ""
    any_text: bool = False
    delta_items: set = field(default_factory=set)
    usage: dict = field(default_factory=dict)
    cancelled: bool = False
    servers: set = field(default_factory=set)  # MCP servers this app injected
    last_mcp_tool: dict = field(default_factory=dict)  # server -> tool most recently started


def _mcp_result_payload(result: dict | None) -> tuple[str, list[tuple[str, bytes]]]:
    texts: list[str] = []
    images: list[tuple[str, bytes]] = []
    for item in (result or {}).get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            texts.append(item.get("text", ""))
        elif item.get("type") == "image" and item.get("data"):
            try:
                images.append((item.get("mimeType", "image/png"), base64.b64decode(item["data"])))
            except ValueError:
                pass
    return "\n".join(texts), images


def _tool_from_message(message: str) -> str:
    m = re.search(r"[\"'`“]([A-Za-z0-9_.-]+)[\"'`”]", message or "")
    return m.group(1) if m else ""


class CodexAdapter(AIAdapter):
    id = "codex"
    name = "Codex"

    def __init__(self, settings: SettingsStore):
        self._settings = settings
        self._server: AppServer | None = None
        self._server_lock = asyncio.Lock()
        self._turns: dict[str, _Turn] = {}  # thread id -> active turn
        self._conv_thread: dict[str, str] = {}  # conversation id -> thread id
        self._full_access_threads: set[str] = set()
        self._status: ProviderStatus | None = None
        self._status_at = 0.0
        self._models: list[dict] = []
        self._login_waiters: list[asyncio.Future] = []

    def _conf(self) -> dict:
        return self._settings.get()["providers"]["codex"]

    async def _get_server(self) -> AppServer:
        async with self._server_lock:
            if self._server and self._server.alive:
                return self._server
            exe = resolve_cli(self._conf().get("cli_path", ""), "codex")
            if not exe:
                raise AdapterUnavailable("Codex CLI が見つかりません（npm i -g @openai/codex、または設定でパスを指定）。")
            server = AppServer(exe, self._on_notification, self._on_request)
            try:
                await server.start()
            except Exception as e:
                await server.stop()
                raise AdapterUnavailable(f"codex app-server を起動できませんでした: {e}")
            self._server = server
            return server

    # Status / account / models ---------------------------------------------

    async def status(self, refresh: bool = False) -> ProviderStatus:
        if self._status and not refresh and time.time() - self._status_at < 600:
            return self._status
        st = ProviderStatus(id=self.id, name=self.name, available=False, image_mimes=sorted(self.image_mimes()))
        try:
            server = await self._get_server()
            st.available = True
            acct = await server.request("account/read", {})
            account = (acct or {}).get("account")
            if account:
                st.auth_ok = True
                if account.get("type") == "chatgpt":
                    st.account = f"{account.get('email') or 'ChatGPT'} ({account.get('planType', '')})"
                else:
                    st.account = "APIキー" if account.get("type") == "apiKey" else account.get("type", "")
            else:
                st.detail = LOGIN_HINT
            models = await server.request("model/list", {})
            self._models = [m for m in (models or {}).get("data", []) if not m.get("hidden")]
            st.models = [
                ModelInfo(
                    id=m["id"],
                    name=m.get("displayName") or m["id"],
                    description=m.get("description", ""),
                    is_default=bool(m.get("isDefault")),
                    supports_images="image" in (m.get("inputModalities") or ["text", "image"]),
                    efforts=[o["reasoningEffort"] for o in m.get("supportedReasoningEfforts") or [] if o.get("reasoningEffort")],
                    default_effort=m.get("defaultReasoningEffort") or "",
                )
                for m in self._models
            ]
            st.version = await self._version()
        except AdapterUnavailable as e:
            st.detail = str(e)
        except Exception as e:
            logger.warning("Codex status failed: %s", e)
            st.detail = f"Codex の状態を取得できませんでした: {e}"
        self._status, self._status_at = st, time.time()
        return st

    async def _version(self) -> str:
        from ..procs import run_capture

        exe = resolve_cli(self._conf().get("cli_path", ""), "codex")
        if not exe:
            return ""
        code, out = await run_capture([exe, "--version"])
        return out.splitlines()[0] if code == 0 and out else ""

    def model_supports_images(self, model: str) -> bool:
        for m in self._models:
            if m["id"] == model or (not model and m.get("isDefault")):
                return "image" in (m.get("inputModalities") or ["text", "image"])
        return True

    async def login(self, method: str, api_key: str | None = None) -> dict:
        server = await self._get_server()
        if method == "api_key":
            if not api_key:
                raise AdapterUnavailable("APIキーを入力してください")
            await server.request("account/login/start", {"type": "apiKey", "apiKey": api_key})
            self._status = None
            return {"ok": True}
        res = await server.request("account/login/start", {"type": "chatgpt"})
        url = (res or {}).get("authUrl")
        if url:
            webbrowser.open(url)
        fut = asyncio.get_running_loop().create_future()
        self._login_waiters.append(fut)
        try:
            done = await asyncio.wait_for(fut, 600)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "ログインがタイムアウトしました", "auth_url": url}
        self._status = None
        return {"ok": bool(done.get("success")), "error": done.get("error"), "auth_url": url}

    async def usage(self, refresh: bool = False):
        from ..usage import UsageInfo, codex_usage

        cached = getattr(self, "_usage", None)
        if cached and not refresh and time.time() - cached.fetched_at < 120:
            return cached
        try:
            server = await self._get_server()
            self._usage = codex_usage(await server.request("account/rateLimits/read", {}, timeout=30))
        except AdapterUnavailable as e:
            self._usage = UsageInfo("codex", available=False, note=str(e))
        except Exception as e:
            logger.warning("Codex usage read failed: %s", e)
            self._usage = UsageInfo("codex", available=False, note=f"利用枠を取得できませんでした: {e}")
        return self._usage

    async def logout(self) -> dict:
        server = await self._get_server()
        await server.request("account/logout", {})
        self._status = None
        return {"ok": True}

    # Notifications and server requests ---------------------------------------

    async def _on_notification(self, method: str, params: dict) -> None:
        if method == "__exit__":
            for turn in self._turns.values():
                turn.status = "failed"
                turn.error = "Codex app-server が予期せず終了しました"
                turn.done.set()
            return
        if method == "account/rateLimits/updated":
            self._usage = None  # stale; next read fetches fresh numbers
            return
        if method == "account/login/completed":
            for fut in self._login_waiters:
                if not fut.done():
                    fut.set_result(params)
            self._login_waiters.clear()
            return
        turn = self._turns.get(params.get("threadId", ""))
        if turn is None:
            return
        if params.get("turnId") and turn.turn_id and params["turnId"] != turn.turn_id:
            return
        await self._handle_turn_event(turn, method, params)

    async def _handle_turn_event(self, turn: _Turn, method: str, params: dict) -> None:
        emit = turn.emit
        if method == "item/agentMessage/delta":
            item_id = params.get("itemId", "")
            if item_id not in turn.delta_items:
                turn.delta_items.add(item_id)
                if turn.any_text:
                    await emit(TextDelta("\n\n"))
            turn.any_text = True
            await emit(TextDelta(params.get("delta", "")))
        elif method == "item/started":
            item = params.get("item") or {}
            await self._item_started(turn, item)
        elif method == "item/completed":
            item = params.get("item") or {}
            await self._item_completed(turn, item)
        elif method == "turn/started":
            turn.turn_id = (params.get("turn") or {}).get("id", turn.turn_id)
        elif method == "turn/completed":
            t = params.get("turn") or {}
            turn.status = t.get("status", "completed")
            if t.get("error"):
                turn.error = t["error"].get("message", "")
            turn.done.set()
        elif method == "error":
            err = (params.get("error") or {}).get("message", "")
            if params.get("willRetry"):
                await emit(StatusUpdate(f"再試行中: {err}", "warning"))
            else:
                turn.error = err
        elif method == "thread/tokenUsage/updated":
            turn.usage = params.get("tokenUsage") or params
        elif method == "mcpServer/startupStatus/updated":
            if params.get("status") == "failed" and params.get("name") in turn.servers:
                await emit(StatusUpdate(f"MCPサーバー「{params.get('name')}」に接続できませんでした: {params.get('error') or ''}。"
                                       "Blender が起動・応答しているか右パネルで確認してください。", "warning"))
        elif method == "item/reasoning/summaryTextDelta":
            await emit(StatusUpdate("考え中…"))
        elif method == "thread/compacted":
            await emit(StatusUpdate("会話が長くなったため、Codex がコンテキストを圧縮しました"))

    async def _item_started(self, turn: _Turn, item: dict) -> None:
        t = item.get("type")
        if t == "mcpToolCall":
            turn.last_mcp_tool[item.get("server", "")] = item.get("tool", "")
            await turn.emit(ToolStart(item["id"], f"{item.get('server')}.{item.get('tool')}", item.get("arguments"),
                                      server=item.get("server", ""), tool=item.get("tool", "")))
        elif t == "commandExecution":
            await turn.emit(ToolStart(item["id"], "shell", {"command": item.get("command"), "cwd": item.get("cwd")}))
        elif t == "fileChange":
            await turn.emit(ToolStart(item["id"], "file_change", {"changes": item.get("changes")}))
        elif t == "webSearch":
            await turn.emit(ToolStart(item["id"], "web_search", {"query": item.get("query")}))
        elif t == "imageGeneration":
            await turn.emit(ToolStart(item["id"], "image_generation", {"prompt": item.get("revisedPrompt")}))
        elif t == "reasoning":
            await turn.emit(StatusUpdate("考え中…"))

    async def _item_completed(self, turn: _Turn, item: dict) -> None:
        t = item.get("type")
        if t == "agentMessage":
            if item.get("id") not in turn.delta_items and item.get("text"):
                if turn.any_text:
                    await turn.emit(TextDelta("\n\n"))
                turn.any_text = True
                await turn.emit(TextDelta(item["text"]))
        elif t == "mcpToolCall":
            out, images = _mcp_result_payload(item.get("result"))
            err = (item.get("error") or {}).get("message")
            await turn.emit(ToolEnd(item["id"], ok=item.get("status") == "completed" and not err,
                                    output=err or out, images=images))
        elif t == "commandExecution":
            await turn.emit(ToolEnd(item["id"], ok=item.get("exitCode") in (0, None) and item.get("status") == "completed",
                                    output=item.get("aggregatedOutput") or ""))
        elif t == "fileChange":
            await turn.emit(ToolEnd(item["id"], ok=item.get("status") == "completed",
                                    output="\n".join(c.get("path", "") for c in item.get("changes") or [])))
        elif t == "webSearch":
            await turn.emit(ToolEnd(item["id"], ok=True, output=item.get("query", "")))
        elif t == "imageGeneration":
            images = []
            error = ""
            if item.get("result"):
                try:
                    images.append(("image/png", base64.b64decode(item["result"], validate=True)))
                except ValueError:
                    error = "生成画像を読み込めませんでした"
            ok = item.get("status") == "completed" and bool(images) and not item.get("failure") and not error
            await turn.emit(ToolEnd(item["id"], ok=ok, output=error or ("" if ok else "画像生成に失敗しました"), images=images))

    async def _on_request(self, method: str, params: dict) -> Any:
        turn = self._turns.get(params.get("threadId", ""))
        if turn is None:
            raise NotImplementedError(method)
        if method == "item/commandExecution/requestApproval":
            d = await turn.approve(ApprovalRequest(
                kind="command", title="コマンドの実行", detail=params.get("reason") or "",
                input={"command": params.get("command"), "cwd": params.get("cwd")}))
            return {"decision": self._decision(d)}
        if method == "item/fileChange/requestApproval":
            d = await turn.approve(ApprovalRequest(
                kind="file_change", title="ファイルの変更", detail=params.get("reason") or "",
                input={"grantRoot": params.get("grantRoot")}))
            return {"decision": self._decision(d)}
        if method == "item/permissions/requestApproval":
            d = await turn.approve(ApprovalRequest(
                kind="permissions", title="追加の権限", detail=params.get("reason") or "",
                input=params.get("permissions")))
            if d.allow:
                return {"permissions": params.get("permissions") or {}, "scope": "session" if d.remember else "turn"}
            return {"permissions": {}}
        if method == "mcpServer/elicitation/request":
            meta = params.get("_meta") or {}
            server = params.get("serverName", "")
            if meta.get("codex_approval_kind") == "mcp_tool_call":
                # Codex asks before running a non-read-only MCP tool. Treat it like any
                # other tool approval so the same rules and UI apply.
                # item/started for the call arrives just before its approval request.
                tool = turn.last_mcp_tool.get(server) or _tool_from_message(params.get("message", ""))
                d = await turn.approve(ApprovalRequest(
                    kind="tool", title=f"{server}.{tool}" if tool else f"{server} のツール",
                    detail=params.get("message", ""), server=server,
                    tool_name=f"mcp__{server}__{tool}" if tool else "",
                    input=meta.get("tool_params")))
                if d.cancel_turn:
                    return {"action": "cancel", "content": None}
                if not d.allow:
                    return {"action": "decline", "content": None}
                res: dict[str, Any] = {"action": "accept", "content": None}
                if d.remember and "session" in (meta.get("persist") or []):
                    res["_meta"] = {"persist": "session"}
                return res
            schema = params.get("requestedSchema") if params.get("mode") == "form" else None
            detail = params.get("message", "")
            if params.get("mode") == "url":
                detail += f"\n{params.get('url', '')}"
            d = await turn.approve(ApprovalRequest(
                kind="mcp_elicitation", title=f"MCP「{params.get('serverName')}」からの確認",
                detail=detail, server=params.get("serverName", ""), schema=schema,
                input=params.get("_meta")))
            if d.cancel_turn:
                return {"action": "cancel", "content": None}
            if not d.allow:
                return {"action": "decline", "content": None}
            return {"action": "accept", "content": d.content if schema else None}
        if method == "item/tool/requestUserInput":
            questions = [
                {"id": q["id"], "header": q.get("header", ""), "question": q.get("question", ""),
                 "options": q.get("options") or [], "multi": False}
                for q in params.get("questions") or []
            ]
            d = await turn.approve(ApprovalRequest(kind="question", title="AIからの質問", questions=questions, allow_session=False))
            return {"answers": {qid: {"answers": labels} for qid, labels in d.answers.items()}}
        raise NotImplementedError(method)

    @staticmethod
    def _decision(d: ApprovalDecision) -> str:
        if d.cancel_turn:
            return "cancel"
        if not d.allow:
            return "decline"
        return "acceptForSession" if d.remember else "accept"

    # Running ---------------------------------------------------------------

    def _turn_access(self, req: RunRequest, thread_id: str) -> dict:
        """Per-turn approval/sandbox, so toggling full access also applies to an open thread."""
        if req.full_access:
            self._full_access_threads.add(thread_id)
            return {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}}
        if thread_id in self._full_access_threads:
            # Turned off again: restore the normal policy on this thread.
            self._full_access_threads.discard(thread_id)
            conf = self._conf()
            sandbox = "read-only" if req.prompt_only else (conf.get("sandbox") or "workspace-write")
            return {
                "approvalPolicy": conf.get("approval_policy") or "on-request",
                "sandboxPolicy": {"type": "readOnly" if sandbox == "read-only" else "workspaceWrite"},
            }
        return {}

    def _thread_params(self, req: RunRequest) -> dict:
        conf = self._conf()
        params: dict[str, Any] = {
            "cwd": req.cwd,
            "approvalPolicy": "never" if req.full_access else (conf.get("approval_policy") or "on-request"),
            "sandbox": "danger-full-access" if req.full_access else (conf.get("sandbox") or "workspace-write"),
            "config": {"features": {"default_mode_request_user_input": True}},
        }
        if req.model:
            params["model"] = req.model
        if req.system_append:
            params["developerInstructions"] = req.system_append
        if req.prompt_only:
            params["sandbox"] = "read-only"
            params["config"]["features"].update(
                plugins=False, apps=False, skip_host_skill_discovery=True,
                shell_tool=False, image_generation=req.image_generation,
            )
            params["config"]["web_search"] = "disabled"
            # Without skills the model calls image_generation directly instead of first trying to
            # read the bundled imagegen SKILL.md through tools this chat does not have.
            params["config"]["skills"] = {"bundled": {"enabled": False}, "include_instructions": False}
        elif req.mcp_servers:
            params["config"]["mcp_servers"] = {
                name: {**cfg, "tool_timeout_sec": 600} for name, cfg in req.mcp_servers.items()
            }
        return params

    async def run(self, req: RunRequest, emit: EventSink, approve: ApprovalFn) -> RunResult:
        server = await self._get_server()
        thread_id = req.session_id
        try:
            if thread_id and thread_id not in server.loaded_threads:
                await server.request("thread/resume", {**self._thread_params(req), "threadId": thread_id}, timeout=180)
                server.loaded_threads.add(thread_id)
            elif not thread_id:
                res = await server.request("thread/start", self._thread_params(req), timeout=180)
                thread_id = res["thread"]["id"]
                server.loaded_threads.add(thread_id)
        except RpcError as e:
            if req.session_id:
                raise SessionLostError(str(e))
            raise AdapterUnavailable(f"Codex のスレッドを開始できませんでした: {e}")
        self._conv_thread[req.conversation_id] = thread_id
        await emit(SessionInfo(thread_id, req.model))

        inputs: list[dict] = []
        for part in req.parts:
            if part.kind == "text" and part.text:
                inputs.append({"type": "text", "text": part.text})
            elif part.kind == "image":
                inputs.append({"type": "localImage", "path": part.path})

        turn = _Turn(thread_id=thread_id, emit=emit, approve=approve, servers=set(req.mcp_servers))
        self._turns[thread_id] = turn
        try:
            params: dict[str, Any] = {"threadId": thread_id, "input": inputs}
            if req.model:
                params["model"] = req.model
            if req.effort:
                params["effort"] = req.effort
            params.update(self._turn_access(req, thread_id))
            res = await server.request("turn/start", params, timeout=180)
            turn.turn_id = ((res or {}).get("turn") or {}).get("id", "")
            await turn.done.wait()
        finally:
            self._turns.pop(thread_id, None)
        if turn.usage:
            await emit(Usage(turn.usage))
        if turn.cancelled or turn.status == "interrupted":
            return RunResult(thread_id, "cancelled", model=req.model, usage=turn.usage)
        if turn.status == "failed" or turn.error:
            return RunResult(thread_id, "error", error=turn.error or "Codex のターンが失敗しました", model=req.model)
        return RunResult(thread_id, "completed", model=req.model, usage=turn.usage)

    async def steer(self, conversation_id: str, parts: list[InputPart]) -> None:
        thread_id = self._conv_thread.get(conversation_id)
        turn = self._turns.get(thread_id or "")
        if not turn or not turn.turn_id or turn.done.is_set() or turn.cancelled:
            raise AdapterUnavailable("追加指示を送れる状態ではありません。準備または中断の完了後に送信してください。")
        inputs = [{"type": "text", "text": p.text} if p.kind == "text" else
                  {"type": "localImage", "path": p.path} for p in parts if p.kind in ("text", "image")]
        try:
            await self._server.request("turn/steer", {
                "threadId": thread_id, "expectedTurnId": turn.turn_id, "input": inputs,
            })
        except RpcError as e:
            raise AdapterUnavailable(f"追加指示を送信できませんでした: {e}") from e

    async def interrupt(self, conversation_id: str) -> None:
        thread_id = self._conv_thread.get(conversation_id)
        turn = self._turns.get(thread_id or "")
        if not turn:
            return
        turn.cancelled = True
        try:
            server = await self._get_server()
            if turn.turn_id:
                await server.request("turn/interrupt", {"threadId": thread_id, "turnId": turn.turn_id}, timeout=30)
        except Exception as e:
            logger.warning("Codex interrupt failed: %s", e)
            turn.status = "interrupted"
            turn.done.set()

    async def forget(self, conversation_id: str) -> None:
        thread_id = self._conv_thread.pop(conversation_id, None)
        if thread_id and self._server and self._server.alive:
            try:
                await self._server.request("thread/unsubscribe", {"threadId": thread_id}, timeout=10)
            except Exception:
                pass
            self._server.loaded_threads.discard(thread_id)

    async def shutdown(self) -> None:
        async with self._server_lock:
            if self._server:
                await self._server.stop()
                self._server = None
            self._status = None
