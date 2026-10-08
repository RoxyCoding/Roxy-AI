"""Claude Code via the official Claude Agent SDK.

The SDK drives the user's Claude Code CLI in streaming mode. Authentication is
whatever that CLI already uses (its own /login, or ANTHROPIC_API_KEY); this app
never handles Claude credentials except an optional API key kept in the OS
credential store. Tool permission prompts arrive through `can_use_tool` and are
forwarded to the GUI, so the CLI's approval flow is kept, not bypassed.

One live SDK client is kept per conversation, so the CLI and its MCP servers
(including Roxy-BlenderMCP) stay up between turns; idle clients are closed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent, ThinkingBlock

from ..config import Secrets, SettingsStore
from ..procs import resolve_cli, run_capture
from .base import (
    AdapterUnavailable,
    AIAdapter,
    ApprovalFn,
    ApprovalRequest,
    EventSink,
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
    mcp_tool_parts,
)

logger = logging.getLogger(__name__)

IDLE_SECONDS = 20 * 60
LOGIN_HINT = "ターミナルで `claude` を起動し、/login で自分のアカウントにログインしてください。"


@dataclass
class _Live:
    client: ClaudeSDKClient
    key: str
    session_id: str | None = None
    model: str = ""
    approve: ApprovalFn | None = None
    running: bool = False
    cancelled: bool = False
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _tool_result_payload(content: Any) -> tuple[str, list[tuple[str, bytes]]]:
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[tuple[str, bytes]] = []
    for item in content:
        if not isinstance(item, dict):
            texts.append(str(item))
            continue
        if item.get("type") == "text":
            texts.append(item.get("text", ""))
        elif item.get("type") == "image":
            src = item.get("source") or {}
            if src.get("type") == "base64" and src.get("data"):
                try:
                    images.append((src.get("media_type", "image/png"), base64.b64decode(src["data"])))
                except ValueError:
                    pass
    return "\n".join(t for t in texts if t), images


class ClaudeCodeAdapter(AIAdapter):
    id = "claude"
    name = "Claude Code"

    def __init__(self, settings: SettingsStore, secrets: Secrets, probe_cwd: Path):
        self._settings = settings
        self._secrets = secrets
        self._probe_cwd = probe_cwd
        self._live: dict[str, _Live] = {}
        self._status: ProviderStatus | None = None
        self._status_at = 0.0
        self._status_lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    # Configuration ---------------------------------------------------------

    def _conf(self) -> dict:
        return self._settings.get()["providers"]["claude"]

    def _cli_path(self) -> str | None:
        return resolve_cli(self._conf().get("cli_path", ""), "claude")

    def _env(self) -> dict[str, str]:
        conf = self._conf()
        env: dict[str, str] = {}
        if conf.get("auth_mode") == "api_key":
            key = self._secrets.get("anthropic")
            if not key:
                raise AdapterUnavailable("APIキーモードですが、Anthropic APIキーが未設定です（設定 → AI）。")
            env["ANTHROPIC_API_KEY"] = key
        return env

    def _options(self, req: RunRequest | None, live: _Live | None) -> ClaudeAgentOptions:
        conf = self._conf()
        opts: dict[str, Any] = dict(
            cli_path=self._cli_path(),
            env=self._env(),
            stderr=lambda line: logger.debug("claude: %s", line.rstrip()),
            max_buffer_size=64 * 1024 * 1024,
        )
        if req is None:  # status probe
            opts["cwd"] = str(self._probe_cwd)
            return ClaudeAgentOptions(**opts)
        model = req.model if req.model and req.model != "default" else None

        async def can_use_tool(tool_name: str, tool_input: dict, ctx: Any):
            return await self._can_use_tool(live, tool_name, tool_input)

        opts.update(
            cwd=req.cwd,
            model=model,
            resume=req.session_id or None,
            include_partial_messages=True,
            mcp_servers={name: {"type": "stdio", **cfg} for name, cfg in req.mcp_servers.items()},
            permission_mode=conf.get("permission_mode") or "default",
            can_use_tool=can_use_tool,
            add_dirs=list(req.extra_dirs),
        )
        if req.system_append:
            opts["system_prompt"] = {"type": "preset", "preset": "claude_code", "append": req.system_append}
        return ClaudeAgentOptions(**opts)

    def _live_key(self, req: RunRequest) -> str:
        conf = self._conf()
        return json.dumps(
            [req.cwd, req.mcp_servers, req.extra_dirs, self._cli_path(), conf.get("auth_mode"),
             conf.get("permission_mode"), req.system_append],
            sort_keys=True,
        )

    # Status ----------------------------------------------------------------

    async def status(self, refresh: bool = False) -> ProviderStatus:
        async with self._status_lock:
            if self._status and not refresh and time.time() - self._status_at < 600:
                return self._status
            self._status = await self._probe()
            self._status_at = time.time()
            return self._status

    async def _probe(self) -> ProviderStatus:
        st = ProviderStatus(id=self.id, name=self.name, available=False, image_mimes=sorted(self.image_mimes()))
        cli = self._cli_path()
        if cli:
            code, out = await run_capture([cli, "--version"])
            st.version = out.splitlines()[0] if code == 0 and out else ""
        else:
            st.version = "SDK同梱CLI"
        client = None
        try:
            client = ClaudeSDKClient(self._options(None, None))
            await asyncio.wait_for(client.connect(), 90)
            info = await client.get_server_info() or {}
            st.available = True
            for m in info.get("models") or []:
                st.models.append(
                    ModelInfo(
                        id=m.get("value", ""),
                        name=m.get("displayName") or m.get("value", ""),
                        description=m.get("description", ""),
                        is_default=m.get("value") == "default",
                    )
                )
            account = info.get("account") or {}
            if account.get("email") or account.get("subscriptionType"):
                st.auth_ok = True
                plan = account.get("subscriptionType") or ""
                st.account = f"{account.get('email', '')} {f'({plan})' if plan else ''}".strip()
            elif self._conf().get("auth_mode") == "api_key" or account.get("apiKeySource"):
                st.auth_ok = True
                st.account = "APIキー"
            else:
                st.detail = LOGIN_HINT
        except AdapterUnavailable as e:
            st.detail = str(e)
        except Exception as e:
            logger.warning("Claude status probe failed: %s", e)
            st.detail = f"Claude Code CLI を起動できませんでした: {e}"
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        if not st.models:
            st.models = [ModelInfo(id="default", name="デフォルト", is_default=True)]
        return st

    # Live clients ----------------------------------------------------------

    async def _live_for(self, req: RunRequest) -> _Live:
        key = self._live_key(req)
        live = self._live.get(req.conversation_id)
        if live and (live.key != key or (req.session_id or None) != live.session_id):
            await self.forget(req.conversation_id)
            live = None
        if live is None:
            live = _Live(client=None, key=key, session_id=req.session_id, model=req.model)  # type: ignore[arg-type]
            client = ClaudeSDKClient(self._options(req, live))
            live.client = client
            try:
                await asyncio.wait_for(client.connect(), 120)
            except asyncio.TimeoutError:
                await self._safe_disconnect(client)
                raise AdapterUnavailable("Claude Code CLI の起動がタイムアウトしました。")
            except Exception as e:
                await self._safe_disconnect(client)
                if req.session_id and "conversation" in str(e).lower():
                    raise SessionLostError(str(e))
                raise AdapterUnavailable(f"Claude Code CLI を起動できませんでした: {e}")
            self._live[req.conversation_id] = live
            self._ensure_reaper()
        elif (req.model or "default") != (live.model or "default"):
            await live.client.set_model(req.model if req.model and req.model != "default" else None)
            live.model = req.model
        return live

    async def _safe_disconnect(self, client: ClaudeSDKClient) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), 15)
        except Exception as e:
            logger.debug("disconnect failed: %s", e)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_idle())

    async def _reap_idle(self) -> None:
        while self._live:
            await asyncio.sleep(60)
            now = time.time()
            for cid, live in list(self._live.items()):
                if not live.running and now - live.last_used > IDLE_SECONDS:
                    logger.info("Closing idle Claude client for %s", cid)
                    await self.forget(cid)

    async def forget(self, conversation_id: str) -> None:
        live = self._live.pop(conversation_id, None)
        if live and live.client is not None:
            await self._safe_disconnect(live.client)

    async def shutdown(self) -> None:
        for cid in list(self._live):
            await self.forget(cid)
        if self._reaper:
            self._reaper.cancel()

    # Approvals -------------------------------------------------------------

    async def _can_use_tool(self, live: _Live | None, tool_name: str, tool_input: dict):
        approve = live.approve if live else None
        if approve is None:
            return PermissionResultDeny(message="実行中のリクエストがありません")
        if tool_name == "AskUserQuestion":
            qs = tool_input.get("questions") or []
            questions = [
                {
                    "id": str(i),
                    "header": q.get("header", ""),
                    "question": q.get("question", ""),
                    "options": q.get("options") or [],
                    "multi": bool(q.get("multiSelect")),
                }
                for i, q in enumerate(qs)
            ]
            d = await approve(ApprovalRequest(kind="question", title="AIからの質問", questions=questions,
                                              tool_name=tool_name, allow_session=False))
            if not d.allow:
                return PermissionResultDeny(message=d.message or "ユーザーは回答しませんでした")
            answers = {q.get("question", ""): ", ".join(d.answers.get(str(i), [])) for i, q in enumerate(qs)}
            return PermissionResultAllow(updated_input={**tool_input, "answers": answers})

        server, tool = mcp_tool_parts(tool_name)
        d = await approve(
            ApprovalRequest(
                kind="tool",
                title=f"{server}.{tool}" if server else tool_name,
                tool_name=tool_name,
                server=server,
                input=tool_input,
            )
        )
        if d.allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message=d.message or "ユーザーが実行を拒否しました", interrupt=d.cancel_turn)

    # Running ---------------------------------------------------------------

    @staticmethod
    def _user_message(req: RunRequest) -> dict:
        content: list[dict] = []
        for part in req.parts:
            if part.kind == "text" and part.text:
                content.append({"type": "text", "text": part.text})
            elif part.kind == "image":
                data = base64.b64encode(Path(part.path).read_bytes()).decode("ascii")
                content.append({"type": "image", "source": {"type": "base64", "media_type": part.mime, "data": data}})
        return {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

    async def run(self, req: RunRequest, emit: EventSink, approve: ApprovalFn) -> RunResult:
        live = await self._live_for(req)
        async with live.lock:
            live.approve = approve
            live.running = True
            live.cancelled = False
            try:
                return await self._run_turn(live, req, emit)
            finally:
                live.approve = None
                live.running = False
                live.last_used = time.time()

    async def _run_turn(self, live: _Live, req: RunRequest, emit: EventSink) -> RunResult:
        message = self._user_message(req)

        async def stream():
            yield message

        await live.client.query(stream())
        session_id = live.session_id
        model = req.model
        any_text = False
        need_sep = False
        streamed_since_assistant = 0
        last_error = ""
        result: ResultMessage | None = None

        async def text(chunk: str) -> None:
            nonlocal any_text, need_sep
            if not chunk:
                return
            if need_sep and any_text:
                await emit(TextDelta("\n\n"))
            need_sep = False
            any_text = True
            await emit(TextDelta(chunk))

        async for msg in live.client.receive_response():
            if isinstance(msg, StreamEvent):
                if msg.parent_tool_use_id:
                    continue
                ev = msg.event or {}
                etype = ev.get("type")
                if etype == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        streamed_since_assistant += len(delta.get("text", ""))
                        await text(delta.get("text", ""))
                elif etype == "content_block_start":
                    block = ev.get("content_block") or {}
                    if block.get("type") == "thinking":
                        await emit(StatusUpdate("考え中…"))
                    elif block.get("type") == "text" and any_text:
                        need_sep = True
            elif isinstance(msg, AssistantMessage):
                if msg.error:
                    last_error = str(msg.error)
                for block in msg.content:
                    if isinstance(block, TextBlock) and not msg.parent_tool_use_id and streamed_since_assistant == 0:
                        await text(block.text)
                    elif isinstance(block, ToolUseBlock):
                        server, tool = mcp_tool_parts(block.name)
                        await emit(ToolStart(block.id, f"{server}.{tool}" if server else block.name,
                                             block.input, server=server, tool=tool))
                        need_sep = True
                    elif isinstance(block, ThinkingBlock):
                        pass
                streamed_since_assistant = 0
            elif isinstance(msg, UserMessage):
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            out, images = _tool_result_payload(block.content)
                            await emit(ToolEnd(block.tool_use_id, ok=not block.is_error, output=out, images=images))
            elif isinstance(msg, SystemMessage):
                data = msg.data or {}
                if msg.subtype == "init":
                    session_id = data.get("session_id") or session_id
                    model = data.get("model") or model
                    live.session_id = session_id
                    await emit(SessionInfo(session_id or "", model or ""))
                    for srv in data.get("mcp_servers") or []:
                        # Only ours matters here; the user's other servers are their business.
                        if srv.get("name") in req.mcp_servers and srv.get("status") not in ("connected", "pending"):
                            await emit(StatusUpdate(
                                f"MCPサーバー「{srv.get('name')}」に接続できませんでした（{srv.get('status')}）。"
                                "Blender が起動・応答しているか右パネルで確認してください。", "warning"))
                elif msg.subtype == "compact_boundary":
                    await emit(StatusUpdate("会話が長くなったため、Claude Code がコンテキストを圧縮しました"))
            elif isinstance(msg, ResultMessage):
                result = msg

        if result is None:
            return RunResult(session_id, "error", error=last_error or "Claude Code から結果が返りませんでした", model=model)
        session_id = result.session_id or session_id
        live.session_id = session_id
        usage = {"cost_usd": result.total_cost_usd, "turns": result.num_turns, **(result.usage or {})}
        await emit(Usage(usage))
        if live.cancelled:
            return RunResult(session_id, "cancelled", model=model, usage=usage)
        if result.is_error:
            err = result.result or last_error or result.subtype
            if req.session_id and "no conversation found" in err.lower():
                raise SessionLostError(err)
            return RunResult(session_id, "error", error=err, model=model, usage=usage)
        return RunResult(session_id, "completed", model=model, usage=usage)

    async def interrupt(self, conversation_id: str) -> None:
        live = self._live.get(conversation_id)
        if live and live.running:
            live.cancelled = True
            try:
                await live.client.interrupt()
            except Exception as e:
                logger.warning("interrupt failed, closing client: %s", e)
                await self.forget(conversation_id)

    def max_image_edge(self) -> int:
        return 1568  # larger images are downscaled by the API anyway

    async def login(self, method: str, api_key: str | None = None) -> dict:
        if method == "api_key":
            self._secrets.set("anthropic", api_key or None)
            self._settings.update({"providers": {"claude": {"auth_mode": "api_key" if api_key else "cli"}}})
            self._status = None
            return {"ok": True}
        raise AdapterUnavailable(LOGIN_HINT)
