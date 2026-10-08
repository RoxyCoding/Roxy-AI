import asyncio
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from roxy_ai.adapters.base import RunResult, TextDelta
from roxy_ai.api import create_app
from roxy_ai.chat import Run
from roxy_ai.config import Paths
from roxy_ai.db import Database
from roxy_ai.workflow import visible_text
from test_prompt_chat import PromptAdapter

AUTH = {"Authorization": "Bearer t"}
BRIEF = {"target": "椅子", "dimensions": "幅45cm", "materials": "木材"}
PROGRESS = {"stage": "materials", "completed": ["shape", "details"], "detail": "木目を調整中"}


def report(kind, value):
    return f"```roxy-{kind}\n{json.dumps(value, ensure_ascii=False)}\n```\n"


@pytest.fixture()
def app(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    return create_app(Paths(tmp_path), "t")


def conversation(state, mode="prompt"):
    return state.db.create_conversation(state.active_project()["id"], "codex", mode=mode)


def test_brief_migration_preserves_conversation(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    conv = db.create_conversation(db.create_project("P")["id"], "codex", title="keep", mode="prompt")
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN brief")
        connection.execute("ALTER TABLE conversations DROP COLUMN draft")
    db = Database(path)
    restored = db.get_conversation(conv["id"])
    assert restored["brief"] == {} and restored["title"] == "keep" and restored["mode"] == "prompt"
    assert restored["draft"] == {}
    db.close()


@pytest.mark.parametrize("text, expected", [
    ("通常の説明", "通常の説明"),
    ("    indented code", "    indented code"),
    ("説明\n" + report("brief", BRIEF) + "続き", "説明\n続き"),
    ("説明\n```roxy-progress\n{\"stage\":", "説明"),
    ("```text\nプロンプト\n```", "```text\nプロンプト\n```"),
])
def test_metadata_hidden_without_hiding_normal_prompt(text, expected):
    assert visible_text(text) == expected


def test_streamed_reports_merge_once_and_reject_invalid_data(app):
    state = app.state.roxy
    conv = conversation(state)
    state.db.update_conversation(conv["id"], {"brief": {"colors": "青"}})
    run = Run("r", conv["id"], conv["project_id"], "codex", "", "u", "a", "lock", mode="prompt")
    events = []

    async def publish(event):
        events.append(event)

    state.chat.publish = publish

    async def check():
        text = report("brief", BRIEF)
        run.text = text[:-2]
        await state.chat._update_reports(run)
        assert events == []
        run.text = text
        await state.chat._update_reports(run)
        await state.chat._update_reports(run)
        assert len(events) == 1
        assert state.db.get_conversation(conv["id"])["brief"] == {"colors": "青", **BRIEF}
        run.text += report("brief", {"unexpected": "bad"}) + report("brief", {"target": ["bad"]})
        await state.chat._update_reports(run)
        assert len(events) == 1
        run.text += report("progress", PROGRESS)
        await state.chat._update_reports(run)
        assert run.progress is None
        run.mode = "modeling"
        run.text += report("progress", PROGRESS)
        await state.chat._update_reports(run)
        assert run.progress == PROGRESS and events[-1]["type"] == "run.progress"

    asyncio.run(check())
    state.db.close()


def test_manual_brief_validation_merge_and_running_conflict(app):
    state = app.state.roxy
    conv = conversation(state)
    with TestClient(app) as client:
        url = f"/api/conversations/{conv['id']}"
        assert client.patch(url, json={"brief": BRIEF}, headers=AUTH).json()["brief"] == BRIEF
        changed = client.patch(url, json={"brief": {"materials": "金属", "dimensions": ""}}, headers=AUTH).json()["brief"]
        assert changed == {"target": "椅子", "dimensions": "", "materials": "金属"}
        for invalid in ({"unexpected": "bad"}, {"materials": ["bad"]}, {"target": "a" * 2001}):
            assert client.patch(url, json={"brief": invalid}, headers=AUTH).status_code == 422
        state.chat.runs[conv["id"]] = object()
        assert client.patch(url, json={"brief": BRIEF}, headers=AUTH).status_code == 409
        state.chat.runs.clear()


@pytest.mark.parametrize("delete_source", [True, False])
def test_handoff_copies_images_and_conditions_without_starting_ai(app, delete_source):
    state = app.state.roxy
    conv = conversation(state)
    state.db.update_conversation(conv["id"], {"brief": BRIEF})
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buf, "PNG")
    image = state.attachments.save_bytes(buf.getvalue(), "参考.png", "image/png", "upload", conv["id"])
    memo = state.attachments.save_bytes(b"memo", "memo.txt", "text/plain", "upload", conv["id"])
    state.db.add_message(conv["id"], "user", "参考です", attachments=[image["id"], memo["id"]])
    state.db.add_message(conv["id"], "user", "同じ画像", attachments=[image["id"]])
    with TestClient(app) as client:
        result = client.post(f"/api/conversations/{conv['id']}/handoff", json={"text": "椅子を作ってください"}, headers=AUTH).json()
        target = result["conversation"]
        assert target["mode"] == "modeling" and target["brief"] == BRIEF
        assert not state.chat.runs and state.db.list_messages(target["id"]) == []
        assert len(result["attachments"]) == 1
        copy = result["attachments"][0]
        restored = client.get(f"/api/conversations/{target['id']}", headers=AUTH).json()
        assert restored["conversation"]["draft"] == {"text": "椅子を作ってください", "attachment_ids": [copy["id"]]}
        assert restored["attachments"][copy["id"]]["name"] == "参考.png"
        assert copy["id"] != image["id"] and copy["conversation_id"] == target["id"]
        assert Path(copy["path"]).read_bytes() == buf.getvalue()
        deleted, retained = (conv["id"], target["id"]) if delete_source else (target["id"], conv["id"])
        assert client.delete(f"/api/conversations/{deleted}", headers=AUTH).status_code == 200
        retained_image = copy if delete_source else image
        assert Path(retained_image["path"]).is_file() and state.db.get_conversation(retained)
        assert client.get(f"/api/attachments/{retained_image['id']}/file", headers=AUTH).content == buf.getvalue()


def test_handoff_missing_image_rolls_back_target(app):
    state = app.state.roxy
    conv = conversation(state)
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buf, "PNG")
    valid = state.attachments.save_bytes(buf.getvalue(), "参考.png", "image/png", "upload", conv["id"])
    image = state.attachments.save_bytes(b"missing", "missing.png", "image/png", "upload", conv["id"])
    state.db.add_message(conv["id"], "user", "参考", attachments=[valid["id"], image["id"]])
    Path(image["path"]).unlink()
    original_files = {p for p in state.paths.attachments.rglob("*") if p.is_file()}
    with TestClient(app) as client:
        assert client.post(f"/api/conversations/{conv['id']}/handoff", json={"text": "椅子"}, headers=AUTH).status_code == 400
        assert [c["id"] for c in state.db.list_conversations()] == [conv["id"]]
        assert {p for p in state.paths.attachments.rglob("*") if p.is_file()} == original_files
        normal = conversation(state, "modeling")
        assert client.post(f"/api/conversations/{normal['id']}/handoff", json={"text": "椅子"}, headers=AUTH).status_code == 400


def test_handoff_validation_and_active_source(app):
    state = app.state.roxy
    conv = conversation(state)
    with TestClient(app) as client:
        url = f"/api/conversations/{conv['id']}/handoff"
        assert client.post(url, json={"text": " "}, headers=AUTH).status_code == 400
        assert client.post(url, json={"text": "a" * 40001}, headers=AUTH).status_code == 422
        state.chat.runs[conv["id"]] = object()
        assert client.post(url, json={"text": "椅子"}, headers=AUTH).status_code == 409
        state.chat.runs.clear()
        assert len(state.db.list_conversations()) == 1


@pytest.mark.parametrize("status", ["completed", "cancelled", "error"])
def test_conditions_reach_ai_and_progress_survives_completion(app, status):
    class WorkflowAdapter(PromptAdapter):
        async def run(self, req, emit, approve):
            self.requests.append(req)
            for chunk in ("開始します。\n", report("progress", PROGRESS), "途中まで進みました。"):
                await emit(TextDelta(chunk))
            return RunResult("s", status)

    state = app.state.roxy
    adapter = WorkflowAdapter()
    state.adapters["codex"] = adapter
    conv = conversation(state, "modeling")
    state.db.update_conversation(conv["id"], {"brief": BRIEF})
    with TestClient(app) as client, client.websocket_connect("/ws?t=t") as ws:
        ws.receive_json()
        client.post(f"/api/conversations/{conv['id']}/messages", json={"provider": "codex", "text": "作ってください"}, headers=AUTH)
        while ws.receive_json()["type"] != "run.finished":
            pass
        assert any("幅45cm" in part.text and "現在の制作条件" in part.text for part in adapter.requests[0].parts)
        msg = state.db.list_messages(conv["id"])[-1]
        assert "roxy-progress" not in msg["content"]
        assert msg["meta"]["progress"] == PROGRESS
        assert msg["status"] == ("complete" if status == "completed" else status)
        assert msg["meta"]["progress"]["stage"] != "done"


def test_rewind_discards_later_brief(app):
    state = app.state.roxy
    conv = conversation(state)
    state.db.add_message(conv["id"], "user", "椅子")
    state.db.add_message(conv["id"], "assistant", "木製ですね", meta={"brief": BRIEF})
    later = state.db.add_message(conv["id"], "user", "机に変更")
    state.db.add_message(conv["id"], "assistant", "机ですね", meta={"brief": {"target": "机"}})
    state.db.update_conversation(conv["id"], {"brief": {"target": "机"}})
    asyncio.run(state.chat._rewind(conv["id"], later["seq"]))
    assert state.db.get_conversation(conv["id"])["brief"] == BRIEF
    state.db.close()
