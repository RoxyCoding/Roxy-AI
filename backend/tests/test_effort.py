import json
import sqlite3

from fastapi.testclient import TestClient

from roxy_ai.adapters.base import ModelInfo
from roxy_ai.api import create_app
from roxy_ai.chat import _model_info
from roxy_ai.config import Paths
from roxy_ai.db import Database

AUTH = {"Authorization": "Bearer t"}


def test_existing_database_gets_effort_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, blend_path TEXT NOT NULL DEFAULT '',"
        " work_dir TEXT NOT NULL DEFAULT '', blender_host TEXT NOT NULL DEFAULT 'localhost',"
        " blender_port INTEGER NOT NULL DEFAULT 9876, notes TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL);"
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "INSERT INTO projects VALUES ('p', 'P', '', '', 'localhost', 9876, '', 0, 0);"
        "INSERT INTO conversations VALUES ('c', 'p', 'old', 'claude', 'opus', 0, 0);"
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.get_conversation("c")["effort"] == ""
    assert db.update_conversation("c", {"effort": "high"})["effort"] == "high"
    db.close()


def test_model_info_matching():
    models = [
        ModelInfo("default", "Default", is_default=True, efforts=["low", "high"]),
        ModelInfo("haiku", "Haiku"),
    ]
    assert _model_info(models, "").id == "default"
    assert _model_info(models, "default").id == "default"
    assert _model_info(models, "haiku").efforts == []
    assert _model_info(models, "unknown") is None


def test_effort_roundtrip_through_api(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    with TestClient(create_app(Paths(tmp_path), "t")) as c:
        pid = c.get("/api/bootstrap", headers=AUTH).json()["active_project_id"]
        conv = c.post("/api/conversations", json={"project_id": pid, "provider": "codex", "model": "m", "effort": "high"},
                      headers=AUTH).json()
        assert conv["effort"] == "high"
        assert c.patch(f"/api/conversations/{conv['id']}", json={"effort": "low"}, headers=AUTH).json()["effort"] == "low"
        s = c.get("/api/settings", headers=AUTH).json()
        assert s["providers"]["claude"]["default_effort"] == "" and s["providers"]["codex"]["default_effort"] == ""
