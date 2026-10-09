"""The common interface every AI backend implements.

Adding a backend means writing one AIAdapter subclass and registering it in
adapters/__init__.py; the chat service, storage and GUI only see these types.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal


# Events an adapter streams while a run is in progress ------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class StatusUpdate:
    """Short human-readable progress line ("考え中", "MCPサーバー起動中" ...)."""

    text: str
    level: Literal["info", "warning"] = "info"


@dataclass
class ToolStart:
    call_id: str
    name: str  # display name, e.g. "blender.look" or "Bash"
    input: Any
    server: str = ""  # MCP server name if this is an MCP tool
    tool: str = ""  # bare tool name


@dataclass
class ToolEnd:
    call_id: str
    ok: bool
    output: str = ""
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (mime, data)


@dataclass
class SessionInfo:
    session_id: str
    model: str = ""


@dataclass
class Usage:
    data: dict


AdapterEvent = TextDelta | StatusUpdate | ToolStart | ToolEnd | SessionInfo | Usage
EventSink = Callable[[AdapterEvent], Awaitable[None]]


# Approvals ------------------------------------------------------------------


@dataclass
class ApprovalRequest:
    kind: Literal["tool", "command", "file_change", "permissions", "mcp_elicitation", "question"]
    title: str
    detail: str = ""
    tool_name: str = ""
    server: str = ""
    input: Any = None
    # For kind="question": [{"id", "header", "question", "options": [{"label", "description"}], "multi"}]
    questions: list[dict] = field(default_factory=list)
    # For kind="mcp_elicitation" in form mode: JSON schema of the requested fields.
    schema: dict | None = None
    allow_session: bool = True  # whether "always allow in this conversation" makes sense
    read_only: bool = False


@dataclass
class ApprovalDecision:
    allow: bool
    remember: bool = False  # allow the same tool for the rest of the conversation
    message: str = ""
    answers: dict[str, list[str]] = field(default_factory=dict)  # question id -> labels
    content: dict | None = None  # elicitation form content
    cancel_turn: bool = False


ApprovalFn = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


# Input ----------------------------------------------------------------------


@dataclass
class InputPart:
    kind: Literal["text", "image"]
    text: str = ""
    path: str = ""  # image: a file the backend owns, already in a format the AI accepts
    mime: str = ""


@dataclass
class RunRequest:
    conversation_id: str
    parts: list[InputPart]
    cwd: str
    model: str = ""
    effort: str = ""  # thinking/reasoning level; empty = model default
    session_id: str | None = None  # resume this AI-internal session/thread
    system_append: str = ""
    mcp_servers: dict[str, dict] = field(default_factory=dict)  # name -> {command, args, env}
    extra_dirs: list[str] = field(default_factory=list)
    prompt_only: bool = False
    full_access: bool = False  # no approvals, no sandbox (user opted in)


@dataclass
class RunResult:
    session_id: str | None
    status: Literal["completed", "cancelled", "error"]
    error: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)


class SessionLostError(Exception):
    """The AI no longer has the session we tried to resume; start fresh."""


class AdapterUnavailable(Exception):
    """CLI missing, not logged in, or otherwise unusable. Message is user-facing."""


@dataclass
class ModelInfo:
    id: str
    name: str
    description: str = ""
    is_default: bool = False
    supports_images: bool = True
    efforts: list[str] = field(default_factory=list)  # selectable thinking levels, empty = not selectable
    default_effort: str = ""


@dataclass
class ProviderStatus:
    id: str
    name: str
    available: bool
    version: str = ""
    account: str = ""  # human-readable login state
    auth_ok: bool = False
    detail: str = ""
    models: list[ModelInfo] = field(default_factory=list)
    supports_images: bool = True
    image_mimes: list[str] = field(default_factory=list)


class AIAdapter(abc.ABC):
    id: str
    name: str

    @abc.abstractmethod
    async def status(self, refresh: bool = False) -> ProviderStatus: ...

    @abc.abstractmethod
    async def run(self, req: RunRequest, emit: EventSink, approve: ApprovalFn) -> RunResult:
        """Run one user turn. Must return (not raise) on cancel via interrupt()."""

    @abc.abstractmethod
    async def interrupt(self, conversation_id: str) -> None: ...

    async def forget(self, conversation_id: str) -> None:
        """Drop any live process/client tied to this conversation."""

    async def shutdown(self) -> None: ...

    def image_mimes(self) -> set[str]:
        return {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def max_image_edge(self) -> int:
        return 2048

    async def usage(self, refresh: bool = False):
        """Plan usage limits (roxy_ai.usage.UsageInfo), or None if the AI has none."""
        return None

    async def login(self, method: str, api_key: str | None = None) -> dict:
        raise AdapterUnavailable(f"{self.name} のログインはこのアプリからは行えません")

    async def logout(self) -> dict:
        raise AdapterUnavailable(f"{self.name} のログアウトはこのアプリからは行えません")


def mcp_tool_parts(name: str) -> tuple[str, str]:
    """Claude Code names MCP tools mcp__<server>__<tool>."""
    if name.startswith("mcp__"):
        rest = name[5:]
        server, _, tool = rest.partition("__")
        return server, tool
    return "", name
