import json

import pytest
from fastapi.testclient import TestClient

from roxy_ai.api import create_app
from roxy_ai.config import Paths

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # No MCP subprocess in unit tests.
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), TOKEN)
    with TestClient(app) as c:
        yield c


def test_requires_token_and_known_origin(client):
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/bootstrap", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    r = client.get("/api/bootstrap", headers={**AUTH, "Origin": "http://tauri.localhost"})
    assert r.status_code == 200 and r.json()["projects"][0]["name"] == "デフォルトプロジェクト"


def test_websocket_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?t=nope") as ws:
            ws.receive_json()
    with client.websocket_connect(f"/ws?t={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_project_and_conversation_crud(client):
    p = client.post("/api/projects", json={"name": "家具", "blender_port": 9877}, headers=AUTH).json()
    assert client.post(f"/api/projects/{p['id']}/activate", headers=AUTH).status_code == 200
    c = client.post("/api/conversations", json={"project_id": p["id"], "provider": "claude"}, headers=AUTH).json()
    assert client.patch(f"/api/conversations/{c['id']}", json={"title": "椅子"}, headers=AUTH).json()["title"] == "椅子"
    assert [x["id"] for x in client.get(f"/api/conversations?project_id={p['id']}", headers=AUTH).json()] == [c["id"]]
    assert client.get("/api/search?q=椅子", headers=AUTH).json()[0]["conversation_id"] == c["id"]
    md = client.post(f"/api/conversations/{c['id']}/export", json={"format": "md"}, headers=AUTH).json()["path"]
    assert md.endswith(".md")
    assert client.delete(f"/api/conversations/{c['id']}", headers=AUTH).json()["ok"]
    assert client.get(f"/api/conversations/{c['id']}", headers=AUTH).status_code == 404
    assert client.post("/api/conversations", json={"project_id": p["id"], "provider": "nope"}, headers=AUTH).status_code == 503


def test_upload_serve_and_delete(client):
    r = client.post("/api/attachments", files=[("files", ("メモ.txt", "hello".encode(), "text/plain"))], headers=AUTH)
    att = r.json()[0]
    assert att["name"] == "メモ.txt" and att["conversation_id"] is None
    assert client.get(f"/api/attachments/{att['id']}/file").status_code == 401
    assert client.get(f"/api/attachments/{att['id']}/file?t={TOKEN}").content == b"hello"
    assert client.delete(f"/api/attachments/{att['id']}", headers=AUTH).json()["ok"]
    assert client.get(f"/api/attachments/{att['id']}/file?t={TOKEN}").status_code == 404


def test_unknown_approval_and_reveal_outside_data(client):
    assert client.post("/api/approvals/nope", json={"allow": True}, headers=AUTH).status_code == 404
    assert client.post("/api/system/reveal", json={"path": "C:\\Windows"}, headers=AUTH).status_code == 400


def test_settings_patch_keeps_unrelated_keys(client):
    s = client.patch("/api/settings", json={"theme": "dark"}, headers=AUTH).json()
    assert s["theme"] == "dark" and s["providers"]["claude"]["default_model"] == "default"
