import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from roxy_ai.adapters.base import AIAdapter, ApprovalDecision, ApprovalRequest, ModelInfo, ProviderStatus, RunRequest, RunResult, TextDelta
from roxy_ai.adapters.claude_code import ClaudeCodeAdapter
from roxy_ai.adapters.codex import CodexAdapter
from roxy_ai.api import create_app
from roxy_ai.chat import Run
from roxy_ai.config import Paths
from roxy_ai.db import Database


class PromptAdapter(AIAdapter):
    id = "codex"
    name = "Codex"

    def __init__(self):
        self.requests = []

    async def status(self, refresh=False):
        return ProviderStatus(self.id, self.name, True, auth_ok=True, models=[ModelInfo("", "Default", is_default=True)])

    async def run(self, req, emit, approve):
        self.requests.append(req)
        await emit(TextDelta("```text\nBlenderで木製の椅子を作ってください。\n```"))
        return RunResult("prompt-session", "completed")

    async def interrupt(self, conversation_id):
        pass


def test_old_conversations_migrate_to_modeling_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    project = db.create_project("P")
    conv = db.create_conversation(project["id"], "codex", title="keep")
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN mode")
    db = Database(path)
    assert db.get_conversation(conv["id"])["mode"] == "modeling"
    assert db.get_conversation(conv["id"])["title"] == "keep"
    db.close()


def test_prompt_chat_roundtrip_and_request_isolation(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    adapter = PromptAdapter()
    state.adapters["codex"] = adapter
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        project = state.active_project()
        body = {"project_id": project["id"], "provider": "codex"}
        normal = client.post("/api/conversations", json=body, headers=headers).json()
        assert normal["mode"] == "modeling"
        assert client.post("/api/conversations", json={**body, "mode": "invalid"}, headers=headers).status_code == 422
        conv = client.post("/api/conversations", json={**body, "mode": "prompt"}, headers=headers).json()
        assert conv["mode"] == "prompt"
        state.chat.locks[state.chat.endpoint(project)] = normal["id"]
        response = client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "何を作るか相談したい"}, headers=headers)
        assert response.status_code == 200
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        assert detail["conversation"]["mode"] == "prompt"
        request = adapter.requests[0]
        assert request.prompt_only and request.mcp_servers == {}
        assert "候補を2〜3案" in request.system_append and "```text" in request.system_append
        assert "木製の椅子" in detail["messages"][-1]["content"]
        assert state.chat.locks[state.chat.endpoint(project)] == normal["id"]
        assert client.patch(f"/api/conversations/{conv['id']}", json={"model": "new"}, headers=headers).json()["mode"] == "prompt"


def test_prompt_chat_denies_tool_actions_but_allows_questions(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    run = Run("r", "c", "p", "codex", "", "u", "a", "prompt:c", mode="prompt")
    state.chat.runs["c"] = run
    events = []

    async def publish(event):
        events.append(event)
        if event["type"] == "approval.request":
            state.chat.resolve_approval(event["approval"]["id"], ApprovalDecision(True, answers={"q": ["chair"]}))

    state.chat.publish = publish

    async def check():
        for kind in ("tool", "command", "file_change", "permissions", "mcp_elicitation"):
            assert not (await state.chat._approve(run, ApprovalRequest(kind, "action", tool_name="mcp__blender__execute"))).allow
        assert events == []
        result = await state.chat._approve(run, ApprovalRequest("question", "何を作りますか？"))
        assert result.allow and result.answers == {"q": ["chair"]}

    asyncio.run(check())
    state.db.close()


def test_prompt_adapters_disable_execution_tools(tmp_path):
    request = RunRequest("c", [], str(tmp_path), prompt_only=True)
    codex = object.__new__(CodexAdapter)
    codex._conf = lambda: {"sandbox": "workspace-write"}
    params = codex._thread_params(request)
    assert params["sandbox"] == "read-only"
    assert params["config"]["features"] == {"default_mode_request_user_input": True, "plugins": False, "shell_tool": False}
    claude = object.__new__(ClaudeCodeAdapter)
    claude._conf = lambda: {"permission_mode": "acceptEdits"}
    claude._cli_path = lambda: "claude"
    claude._env = lambda: {}
    options = claude._options(request, None)
    assert options.tools == ["AskUserQuestion"] and options.permission_mode == "plan"
    request.prompt_only = False
    assert claude._options(request, None).permission_mode == "acceptEdits"
