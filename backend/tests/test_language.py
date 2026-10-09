import json

import pytest
from fastapi.testclient import TestClient

from roxy_ai.api import create_app
from roxy_ai.adapters.base import SessionInfo
from roxy_ai.config import Paths, SettingsStore
from test_prompt_chat import PromptAdapter


@pytest.mark.parametrize("mode", ["prompt", "design_prompt"])
def test_language_setting_validation_persistence_and_ai_instructions(tmp_path, mode):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    class Adapter(PromptAdapter):
        async def run(self, req, emit, approve):
            await emit(SessionInfo("prompt-session", req.model))
            return await super().run(req, emit, approve)

    adapter = Adapter()
    state.adapters["codex"] = adapter
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        assert client.get("/api/settings", headers=headers).json()["ui"]["language"] == "ja"
        response = client.patch("/api/settings", json={"ui": {"language": "en"}}, headers=headers)
        assert response.status_code == 200 and response.json()["ui"]["language"] == "en"
        assert response.json()["ui"]["sidebar_open"] is True
        assert SettingsStore(tmp_path / "settings.json").get()["ui"]["language"] == "en"
        for value in ("fr", "JA", None, ["en"]):
            assert client.patch("/api/settings", json={"ui": {"language": value}}, headers=headers).status_code == 422
        assert client.patch("/api/settings", json={"ui": "en"}, headers=headers).status_code == 422
        project = state.active_project()
        conv = client.post("/api/conversations", json={"project_id": project["id"], "provider": "codex", "mode": mode}, headers=headers).json()
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "hello", "provider": "codex"}, headers=headers).status_code == 200
        assert "Respond in English" in adapter.requests[-1].system_append
        assert "selected English" in adapter.requests[-1].parts[0].text
        assert client.patch("/api/settings", json={"ui": {"language": "ja"}}, headers=headers).status_code == 200
        assert client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "continue", "provider": "codex"}, headers=headers).status_code == 200
        assert "Respond in Japanese" in adapter.requests[-1].system_append
        assert "selected Japanese" in adapter.requests[-1].parts[0].text
        assert adapter.requests[-1].session_id == "prompt-session"
