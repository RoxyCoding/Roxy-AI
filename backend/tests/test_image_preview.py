import asyncio
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from roxy_ai.adapters.base import ToolEnd, ToolStart
from roxy_ai.adapters.codex import CodexAdapter
from roxy_ai.api import create_app
from roxy_ai.attachments import MAX_UPLOAD
from roxy_ai.chat import Run
from roxy_ai.config import Paths


def test_tool_image_is_previewed_without_saving(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    project = state.db.create_project("P")
    conv = state.db.create_conversation(project["id"], "codex")
    msg = state.db.add_message(conv["id"], "assistant", "")
    run = Run("r", conv["id"], project["id"], "codex", "", "u", msg["id"], "blender")
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buffer, "PNG")
    data = buffer.getvalue()

    async def generate():
        await state.chat._on_event(run, ToolStart("tool", "generate_image", {}))
        await state.chat._on_event(run, ToolEnd("tool", True, images=[("image/png", data)]))

    asyncio.run(generate())
    aid = run.tool_calls[0]["images"][0]
    assert run.tool_calls[0]["status"] == "ok"
    assert state.db.get_attachment(aid) is None
    assert not list(state.paths.attachments.rglob("*"))
    with TestClient(app) as client:
        url = f"/api/attachments/{aid}/file"
        assert client.get(url).status_code == 401
        for suffix in ("", "&thumb=1"):
            response = client.get(url + "?t=t" + suffix)
            assert response.content == data
            assert response.headers["content-type"] == "image/png"
            assert response.headers["cache-control"] == "no-store"
        state.attachments.previews.clear()
        assert client.get(url + "?t=t").status_code == 404


def test_preview_validation_and_memory_limit(tmp_path, monkeypatch):
    app = create_app(Paths(tmp_path), "t")
    state = app.state.roxy
    store = state.attachments
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, "PNG")
    data = buffer.getvalue()
    with pytest.raises(OSError):
        store.preview_image(b"invalid", "image/png")
    with pytest.raises(ValueError):
        store.preview_image(b"x" * (MAX_UPLOAD + 1), "image/png")
    monkeypatch.setattr("roxy_ai.attachments.MAX_UPLOAD", len(data))
    first = store.preview_image(data, "image/png")
    second = store.preview_image(data, "image/png")
    assert list(store.previews) == [second]
    assert first not in store.previews
    state.db.close()


def test_codex_image_generation_emits_preview_bytes():
    adapter = object.__new__(CodexAdapter)
    turn = SimpleNamespace(emit=AsyncMock())
    item = {"type": "imageGeneration", "id": "ig", "status": "completed", "revisedPrompt": "cat",
            "result": base64.b64encode(b"image bytes").decode(), "savedPath": "unused.png"}

    async def generate():
        await adapter._item_started(turn, item)
        await adapter._item_completed(turn, item)

    asyncio.run(generate())
    start, end = [call.args[0] for call in turn.emit.call_args_list]
    assert isinstance(start, ToolStart) and start.input == {"prompt": "cat"}
    assert isinstance(end, ToolEnd) and end.ok
    assert end.images == [("image/png", b"image bytes")] and end.output == ""
    for result, failure in (("not base64!", None), ("", None), (item["result"], {"message": "failed"})):
        turn.emit.reset_mock()
        asyncio.run(adapter._item_completed(turn, {**item, "result": result, "failure": failure}))
        assert not turn.emit.call_args.args[0].ok
