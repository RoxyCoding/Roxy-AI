import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import ResultMessage

from roxy_ai.adapters.base import AdapterUnavailable, InputPart, RunRequest, RunResult
from roxy_ai.adapters.claude_code import ClaudeCodeAdapter, _Live
from roxy_ai.adapters.codex import CodexAdapter, _Turn
from roxy_ai.api import create_app
from roxy_ai.chat import Conflict
from roxy_ai.config import Paths
from roxy_ai.context import plan_context
from test_prompt_chat import PromptAdapter


def test_running_chat_accepts_additional_input_without_new_run(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    adapter = PromptAdapter()
    adapter.steer = AsyncMock()
    state.adapters["codex"] = adapter
    project = state.db.create_project("P")
    conv = state.db.create_conversation(project["id"], "codex", mode="prompt")

    async def check():
        started, finish = asyncio.Event(), asyncio.Event()

        async def execute(run, message, project, fresh):
            run.session_id = "s"
            started.set()
            await finish.wait()
            return RunResult("s", "completed")

        state.chat._run_with_context = execute
        first = await state.chat.send(conv["id"], "first", [], "codex", "")
        await started.wait()
        run = state.chat.runs[conv["id"]]
        second = await state.chat.send(conv["id"], "additional", [], "codex", "")
        assert second["run"]["id"] == first["run"]["id"]
        assert state.chat.runs[conv["id"]] is run
        adapter.steer.assert_awaited_once_with(conv["id"], [InputPart("text", text="additional")])
        with pytest.raises(ValueError):
            await state.chat.send(conv["id"], "", [], "codex", "")
        adapter.steer.side_effect = AdapterUnavailable("rejected")
        with pytest.raises(AdapterUnavailable):
            await state.chat.send(conv["id"], "rejected", [], "codex", "")
        run.cancel_requested = True
        with pytest.raises(Conflict):
            await state.chat.send(conv["id"], "cancelled", [], "codex", "")
        run.cancel_requested = False
        finish.set()
        await run.task
        messages = state.db.list_messages(conv["id"])
        assert [m["content"] for m in messages if m["role"] == "user"] == ["first", "additional"]
        session = state.db.get_ai_session(conv["id"], "codex")
        assert session["synced_seq"] == second["user_message"]["seq"]
        assert plan_context(messages, session, session["synced_seq"] + 1) == ("s", [])

    asyncio.run(check())
    state.db.close()


def test_codex_steers_expected_turn_and_preserves_images():
    async def check():
        adapter = object.__new__(CodexAdapter)
        turn = _Turn("thread", AsyncMock(), AsyncMock(), turn_id="turn")
        adapter._conv_thread = {"c": "thread"}
        adapter._turns = {"thread": turn}
        adapter._server = SimpleNamespace(request=AsyncMock())
        await adapter.steer("c", [InputPart("text", text="change"), InputPart("image", path="image.png")])
        adapter._server.request.assert_awaited_once_with("turn/steer", {
            "threadId": "thread", "expectedTurnId": "turn", "input": [
                {"type": "text", "text": "change"}, {"type": "localImage", "path": "image.png"},
            ],
        })
        turn.done.set()
        with pytest.raises(AdapterUnavailable):
            await adapter.steer("c", [InputPart("text", text="late")])

    asyncio.run(check())


def test_claude_consumes_results_for_additional_input():
    async def check():
        adapter = object.__new__(ClaudeCodeAdapter)
        sent = []
        live = _Live(SimpleNamespace(), "key", running=True)
        adapter._live = {"c": live}

        async def query(stream):
            sent.extend([m async for m in stream])

        async def messages():
            await adapter.steer("c", [InputPart("text", text="additional")])
            for text in ("first", "additional"):
                yield ResultMessage("success", 1, 1, False, 1, "s", result=text)

        live.client.query = query
        live.client.receive_messages = messages
        result = await adapter._run_turn(live, RunRequest("c", [InputPart("text", text="first")], "."), AsyncMock())
        assert result.status == "completed" and live.pending_inputs == 0
        assert [m["message"]["content"][0]["text"] for m in sent] == ["first", "additional"]

    asyncio.run(check())
