import asyncio
import json
from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from fastapi.testclient import TestClient

from roxy_ai.adapters.base import ApprovalDecision, ApprovalRequest, RunRequest, RunResult, TextDelta
from roxy_ai.adapters.claude_code import ClaudeCodeAdapter
from roxy_ai.adapters.codex import CodexAdapter
from roxy_ai.api import create_app
from roxy_ai.config import Paths
from test_prompt_chat import PromptAdapter


QUESTIONS = [{
    "id": "shape", "header": "形状", "question": "どの形にしますか？", "multi": False,
    "options": [{"label": "丸形", "description": "丸い座面"}, {"label": "角形", "description": "四角い座面"}],
}]


@pytest.mark.parametrize("allow", [True, False])
def test_adapters_forward_choices_and_answers(allow):
    seen = []

    async def approve(request):
        seen.append(request)
        question = request.questions[0]
        labels = ["丸形", "木製"] if question["multi"] else ["丸形"]
        return ApprovalDecision(allow, answers={question["id"]: labels} if allow else {})

    async def check():
        codex = object.__new__(CodexAdapter)
        codex._turns = {"thread": SimpleNamespace(approve=approve)}
        result = await codex._on_request("item/tool/requestUserInput", {"threadId": "thread", "questions": QUESTIONS})
        assert seen[-1].kind == "question" and seen[-1].questions == QUESTIONS
        expected = {"shape": {"answers": ["丸形"]}} if allow else {}
        assert result == {"answers": expected}

        claude = object.__new__(ClaudeCodeAdapter)
        tool_input = {"questions": [{**QUESTIONS[0], "multiSelect": True}]}
        result = await claude._can_use_tool(SimpleNamespace(approve=approve), "AskUserQuestion", tool_input)
        assert seen[-1].kind == "question" and seen[-1].questions[0]["multi"]
        if allow:
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input["answers"] == {"どの形にしますか？": "丸形, 木製"}
        else:
            assert isinstance(result, PermissionResultDeny)

    asyncio.run(check())


def test_codex_question_flag_preserves_modeling_mcp_config(tmp_path):
    codex = object.__new__(CodexAdapter)
    codex._conf = lambda: {}
    params = codex._thread_params(RunRequest("c", [], str(tmp_path), mcp_servers={"blender": {"command": "test"}}))
    assert params["config"]["features"]["default_mode_request_user_input"] is True
    assert params["config"]["mcp_servers"]["blender"]["command"] == "test"


def test_question_websocket_and_answer_api_roundtrip(tmp_path):
    class QuestionAdapter(PromptAdapter):
        async def run(self, req, emit, approve):
            decision = await approve(ApprovalRequest("question", "AIからの質問", questions=QUESTIONS, allow_session=False))
            await emit(TextDelta(json.dumps(decision.answers, ensure_ascii=False)))
            return RunResult("question-session", "completed")

    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    app.state.roxy.adapters["codex"] = QuestionAdapter()
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client, client.websocket_connect("/ws?t=t") as ws:
        assert ws.receive_json()["type"] == "hello"
        conv = client.post("/api/conversations", json={"project_id": app.state.roxy.active_project()["id"], "provider": "codex", "mode": "prompt"}, headers=headers).json()
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "相談したい"}, headers=headers).status_code == 200
        while (event := ws.receive_json())["type"] != "approval.request":
            pass
        assert event["approval"]["questions"] == QUESTIONS
        assert client.post(f"/api/approvals/{event['approval']['id']}", json={"allow": True, "answers": {"shape": ["木製の六角形"]}}, headers=headers).status_code == 200
        while ws.receive_json()["type"] != "run.finished":
            pass
        detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
        assert json.loads(detail["messages"][-1]["content"]) == {"shape": ["木製の六角形"]}
