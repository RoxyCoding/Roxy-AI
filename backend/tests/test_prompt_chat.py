import asyncio
import json
import sqlite3

import pytest
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


@pytest.mark.parametrize("mode", ["prompt", "design_prompt"])
def test_prompt_chat_roundtrip_and_request_isolation(tmp_path, monkeypatch, mode):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}, "approval": {"full_access": True}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    adapter = PromptAdapter()
    state.adapters["codex"] = adapter
    async def no_install():
        pytest.fail("Prompt chats must not prepare Blender MCP")

    monkeypatch.setattr(state.monitor, "ensure_installed", no_install)
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        project = state.active_project()
        body = {"project_id": project["id"], "provider": "codex"}
        normal = client.post("/api/conversations", json=body, headers=headers).json()
        assert normal["mode"] == "modeling"
        assert client.post("/api/conversations", json={**body, "mode": "invalid"}, headers=headers).status_code == 422
        conv = client.post("/api/conversations", json={**body, "mode": mode}, headers=headers).json()
        assert conv["mode"] == mode
        state.chat.locks[state.chat.endpoint(project)] = normal["id"]
        response = client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "何を作るか相談したい"}, headers=headers)
        assert response.status_code == 200
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        assert detail["conversation"]["mode"] == mode
        request = adapter.requests[0]
        assert request.prompt_only and request.mcp_servers == {} and not request.full_access
        assert request.image_generation == (mode == "design_prompt")
        if mode == "prompt":
            assert "候補を2〜3案" in request.system_append and "```text" in request.system_append
        else:
            for required in ("設定画", "四面図", "正投影", "共通の縮尺", "反対側面", "基本寸法", "仮寸法", "拡大図", "接合構造", "斜め3方向", "素材・色・質感", "整合性", "基準図", "高解像度", "```text", "roxy-brief"):
                assert required in request.system_append
            for step in ("手順1 イメージ画", "手順2 確認", "手順3 設定画", "手順4 モデリングプロンプト"):
                assert step in request.system_append
            assert request.system_append.index("手順1 イメージ画") < request.system_append.index("手順2 確認") < request.system_append.index("手順3 設定画") < request.system_append.index("手順4 モデリングプロンプト")
            assert "イメージ画を1枚、image_generationツールで実際に生成" in request.system_append
            assert "「このイメージで設定画に進めてよいか」を確認" in request.system_append and "承認されるまでこの確認を繰り返して" in request.system_append
            assert "この段階では設定画を生成せず、画像生成プロンプトやモデリングプロンプトを本文に書かず" in request.system_append
            assert "```textや```modelingのコードブロックも出力しない" in request.system_append
            assert "ユーザーの承認を得るまで、手順3・手順4に進まない" in request.system_append
            assert "roxy-stage" in request.system_append and "設定画に進む" in request.system_append
            assert "イメージ画の段階では、ユーザーから求められない限りプロンプトを提示しない" in request.system_append
            assert "承認されたイメージ画を基準" in request.system_append
            assert "ネイティブのimage_generationを直接使用" in request.system_append
            assert "画像生成ツールに渡したプロンプト全体" in request.system_append
            assert "今回の生成に使用したプロンプト全体も提示" in request.system_append
            for requirement in ("裏面・底面の正投影図", "厚み・断面寸法", "部品どうしの接合部", "装飾の幅・高さ・中心位置", "測定の始点・終点", "全体シートと補足シート", "ユーザーが1枚を指定"):
                assert requirement in request.system_append
            assert "```modeling" in request.system_append and "Blender向けのモデリングプロンプト" in request.system_append
            assert client.post(f"/api/conversations/{conv['id']}/handoff", json={"text": "椅子のモデリング指示"}, headers=headers).status_code == 200
        assert "木製の椅子" in detail["messages"][-1]["content"]
        assert state.chat.locks[state.chat.endpoint(project)] == normal["id"]
        assert client.patch(f"/api/conversations/{conv['id']}", json={"model": "new"}, headers=headers).json()["mode"] == mode
        assert next(c for c in client.get("/api/conversations", headers=headers).json() if c["id"] == conv["id"])["mode"] == mode


@pytest.mark.parametrize("mode", ["prompt", "design_prompt"])
def test_prompt_chat_denies_tool_actions_but_allows_questions(tmp_path, mode):
    state = create_app(Paths(tmp_path), "t").state.roxy
    state.settings.update({"approval": {"full_access": True}})
    run = Run("r", "c", "p", "codex", "", "u", "a", f"{mode}:c", mode=mode)
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
        image = await state.chat._approve(run, ApprovalRequest("tool", "画像生成", tool_name="image_generation"))
        assert image.allow == (mode == "design_prompt")
        assert not (await state.chat._approve(run, ApprovalRequest("tool", "MCP", tool_name="image_generation", server="blender"))).allow
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
    assert params["config"]["features"] == {
        "default_mode_request_user_input": True, "plugins": False, "apps": False,
        "skip_host_skill_discovery": True, "shell_tool": False, "image_generation": False,
    }
    # No skill list: otherwise the model first tries to read imagegen's SKILL.md and fails.
    assert params["config"]["skills"] == {"bundled": {"enabled": False}, "include_instructions": False}
    request.image_generation = True
    design_params = codex._thread_params(request)
    assert design_params["config"]["features"]["image_generation"] is True
    assert design_params["sandbox"] == "read-only" and "mcp_servers" not in design_params["config"]
    claude = object.__new__(ClaudeCodeAdapter)
    claude._conf = lambda: {"permission_mode": "acceptEdits"}
    claude._cli_path = lambda: "claude"
    claude._env = lambda: {}
    options = claude._options(request, None)
    assert options.tools == ["AskUserQuestion"] and options.permission_mode == "plan"
    request.prompt_only = False
    assert claude._options(request, None).permission_mode == "acceptEdits"


@pytest.mark.parametrize("provider", ["claude", "local"])
def test_design_chat_guides_providers_without_native_image_generation(tmp_path, provider):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    adapter = PromptAdapter()
    state.adapters[provider] = adapter
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        conv = client.post("/api/conversations", json={"project_id": state.active_project()["id"], "provider": provider, "mode": "design_prompt"}, headers=headers).json()
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": provider, "text": "設定画を生成して"}, headers=headers).status_code == 200
        req = adapter.requests[0]
        assert req.prompt_only and not req.image_generation and req.mcp_servers == {}
        assert any("画像生成ツールを利用できません" in part.text and "Codexへの切り替え" in part.text for part in req.parts)


def test_design_chat_holds_modeling_prompt_until_concept_is_approved(tmp_path):
    concept = "イメージ画です。\n```modeling\n早すぎるプロンプト\n```\n"
    sheet = "```roxy-stage\n{\"stage\": \"sheet\"}\n```\n設定画です。\n```modeling\n設定画どおりに作る\n```"

    class StagedAdapter(PromptAdapter):
        async def run(self, req, emit, approve):
            self.requests.append(req)
            await emit(TextDelta(concept if len(self.requests) == 1 else sheet))
            return RunResult("design-session", "completed")

    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    adapter = StagedAdapter()
    state.adapters["codex"] = adapter
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        conv = client.post("/api/conversations", json={"project_id": state.active_project()["id"], "provider": "codex", "mode": "design_prompt"}, headers=headers).json()
        assert conv["design_stage"] == ""
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "椅子のイメージ画を生成して"}, headers=headers).status_code == 200
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        assert "早すぎるプロンプト" not in detail["messages"][-1]["content"] and "イメージ画です" in detail["messages"][-1]["content"]
        assert detail["conversation"]["design_stage"] == ""
        assert "イメージ画の作成・確認中" in adapter.requests[0].parts[1].text
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "これで良いです"}, headers=headers).status_code == 200
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        content = detail["messages"][-1]["content"]
        assert "設定画どおりに作る" in content and "roxy-stage" not in content
        assert detail["conversation"]["design_stage"] == "sheet"
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "色を変えて"}, headers=headers).status_code == 200
        assert "承認済み" in adapter.requests[2].parts[1].text


def test_design_chat_question_approval_unlocks_modeling_prompt(tmp_path):
    class ApprovingAdapter(PromptAdapter):
        async def run(self, req, emit, approve):
            await emit(TextDelta("イメージ画です。\n```modeling\n承認前\n```\n"))
            await approve(ApprovalRequest("question", "AIからの質問", questions=[{"id": "ok", "question": "進めますか？", "options": []}], allow_session=False))
            await emit(TextDelta("```modeling\n承認後\n```"))
            return RunResult("design-session", "completed")

    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    state.adapters["codex"] = ApprovingAdapter()
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client, client.websocket_connect("/ws?t=t") as ws:
        assert ws.receive_json()["type"] == "hello"
        conv = client.post("/api/conversations", json={"project_id": state.active_project()["id"], "provider": "codex", "mode": "design_prompt"}, headers=headers).json()
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "生成して"}, headers=headers).status_code == 200
        while (event := ws.receive_json())["type"] != "approval.request":
            pass
        assert client.post(f"/api/approvals/{event['approval']['id']}", json={"allow": True, "answers": {"ok": ["これで良い（設定画に進む）"]}}, headers=headers).status_code == 200
        while ws.receive_json()["type"] != "run.finished":
            pass
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        assert "承認前" not in detail["messages"][-1]["content"] and "承認後" in detail["messages"][-1]["content"]
        assert detail["conversation"]["design_stage"] == "sheet"


def test_existing_design_chats_keep_modeling_prompts_after_migration(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    project = db.create_project("P")
    design = db.create_conversation(project["id"], "codex", mode="design_prompt")
    normal = db.create_conversation(project["id"], "codex")
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN design_stage")
    db = Database(path)
    assert db.get_conversation(design["id"])["design_stage"] == "sheet"
    assert db.get_conversation(normal["id"])["design_stage"] == ""
    db.close()
