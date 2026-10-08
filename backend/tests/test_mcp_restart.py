import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from roxy_ai.adapters.codex import AppServer, CodexAdapter
from roxy_ai.api import create_app
from roxy_ai.chat import Conflict
from roxy_ai.config import Paths


def test_restart_closes_all_clients_and_preserves_conversation(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    with TestClient(app) as client:
        project = state.active_project()
        conv = state.db.create_conversation(project["id"], "codex")
        msg = state.db.add_message(conv["id"], "user", "keep me")
        state.db.set_ai_session(conv["id"], "codex", "saved-session", 1)
        adapters = [SimpleNamespace(shutdown=AsyncMock()), SimpleNamespace(shutdown=AsyncMock())]
        state.chat.adapters = dict(zip(("codex", "claude"), adapters))
        state.monitor.start = AsyncMock()

        async def during_shutdown():
            assert state.chat.mcp_restarting
            with pytest.raises(Conflict):
                state.chat._check_free(conv["id"], project)
            with pytest.raises(Conflict):
                await state.chat.restart_mcp(project)

        adapters[0].shutdown.side_effect = during_shutdown
        assert client.post("/api/blender/restart").status_code == 401
        headers = {"Authorization": "Bearer t"}
        state.chat.runs[conv["id"]] = object()
        assert client.post("/api/blender/restart", headers=headers).status_code == 409
        assert not adapters[0].shutdown.called
        state.chat.runs.clear()
        assert client.post("/api/blender/restart", headers=headers).status_code == 200
        for adapter in adapters:
            adapter.shutdown.assert_awaited_once()
        state.monitor.start.assert_awaited_once_with(project["blender_host"], project["blender_port"])
        assert not state.chat.mcp_restarting
        assert state.db.get_message(msg["id"])["content"] == "keep me"
        assert state.db.get_ai_session(conv["id"], "codex")["session_id"] == "saved-session"
        adapters[0].shutdown.side_effect = RuntimeError("shutdown failed")
        assert client.post("/api/blender/restart", headers=headers).status_code == 503
        assert not state.chat.mcp_restarting
        adapters[0].shutdown.side_effect = None
        assert client.post("/api/blender/restart", headers=headers).status_code == 200


def test_codex_shutdown_drops_stopped_server_and_cached_status():
    adapter = object.__new__(CodexAdapter)
    adapter._server_lock = asyncio.Lock()
    server = SimpleNamespace(stop=AsyncMock())
    adapter._server = server
    adapter._status = object()
    asyncio.run(adapter.shutdown())
    server.stop.assert_awaited_once()
    assert adapter._server is None and adapter._status is None


def test_codex_waits_for_forced_exit_before_restart():
    server = object.__new__(AppServer)
    process = SimpleNamespace(returncode=None, stdin=Mock(), kill=Mock(), wait=AsyncMock(side_effect=[TimeoutError(), None]))
    server._proc = process
    server._reader = None
    server._pending = {}
    asyncio.run(server.stop())
    process.kill.assert_called_once()
    assert process.wait.await_count == 2
    assert server._proc is None and not server.alive
