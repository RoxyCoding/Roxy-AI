import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from roxy_ai.mcp_monitor import ADDON_UPDATE_MSG, BlenderMonitor, UNRESPONSIVE_MSG


def reply(text="", data=None, error=False):
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structuredContent=data, isError=error)


SCENE = {"scene": "Scene", "object_counts": {"mesh": 1}, "active": "Cube | A",
         "selected": ["Cube | A"], "selected_count": 1, "mode": "OBJECT",
         "columns": ["name", "type", "location", "size"],
         "objects": [{"name": "Cube | A", "type": "MESH", "fields": {"size": "size 2x2x2"}}],
         "shown": 1, "total": 1}
STATE = {"version": 7, "file": None, "is_dirty": True, "scene": "Scene", "mode": "OBJECT",
         "active": "Cube | A", "selected_count": 1, "changed": True}


@pytest.fixture
def monitor(tmp_path):
    settings = {"mcp": {"auto_preview": True, "preview_max_size": 800}}
    monitor = BlenderMonitor(SimpleNamespace(get=lambda: settings), tmp_path,
                             AsyncMock(), AsyncMock(return_value={"id": "preview-image"}))
    monitor._ready.set()
    monitor.state["blender"] = "connected"
    monitor._scene_state_available = monitor._scene_state_supported = True
    monitor.call = AsyncMock()
    return monitor


@pytest.mark.parametrize("result", [
    reply("Blenderは接続を受け付けていますが応答しません。ダイアログを確認してください。"),
    asyncio.TimeoutError(),
])
def test_unresponsive_status_does_not_fetch_scene_or_preview(monitor, result):
    if isinstance(result, Exception):
        monitor.call.side_effect = result
    else:
        monitor.call.return_value = result
    outcome = asyncio.run(monitor.refresh())
    assert not outcome["ok"] and outcome["errors"]
    assert monitor.state["blender"] == "unresponsive"
    assert "応答しません" in monitor.state["addon"]["text"]
    assert [call.args[0] for call in monitor.call.call_args_list] == ["get_addon_status"]
    assert not monitor._refresh_lock.locked()


@pytest.mark.parametrize("result", [reply("Error checking addon status: not connected"),
                                      reply("Could not determine addon status."),
                                      reply("failure", error=True)])
def test_status_errors_are_not_success(monitor, result):
    monitor.call.return_value = result
    assert asyncio.run(monitor.refresh())["ok"] is False
    assert monitor.state["blender"] == "disconnected"
    assert monitor.call.await_count == 1


def test_structured_scene_is_used_without_parsing_names(monitor):
    monitor.call.return_value = reply("old text is intentionally unparseable", SCENE)
    scene = asyncio.run(monitor.scene_info(STATE))
    assert scene["selected"] == ["Cube | A"]
    assert scene["objects"] == SCENE["objects"]
    assert scene["object_counts"] == {"mesh": 1}
    assert scene["file"] is None and scene["is_dirty"] is True
    assert scene["version"] == 7 and scene["text"] == "old text is intentionally unparseable"


@pytest.mark.parametrize("data", [None, {"result": "legacy SDK wrapper"}])
def test_old_scene_output_uses_text_fallback(monitor, data):
    monitor.call.return_value = reply(
        "Scene 'Old' | 1 mesh | active Cube | selected Cube | mode OBJECT\n\n"
        "Showing 1 of 1 (name | type):\nCube | mesh", data)
    scene = asyncio.run(monitor.scene_info())
    assert scene["scene"] == "Old" and scene["rows"] == ["Cube | mesh"]
    assert "objects" not in scene


def wire_replies(monitor, state=STATE):
    async def call(tool, args=None, timeout=180):
        if tool == "get_addon_status":
            return reply(json.dumps({"source": "native", "capabilities": ["get_scene_state"]}))
        if tool == "scene_state":
            assert args == {"since": monitor._scene_version or 0}
            assert timeout == 8
            return reply(data=state)
        if tool == "get_scene_info":
            return reply("scene text", SCENE)
        if tool == "viewport_capture":
            return CallToolResult(content=[ImageContent(type="image", data=base64.b64encode(b"png").decode(),
                                                       mimeType="image/png")], structuredContent={"seq": 1})
        raise AssertionError(tool)

    monitor.call.side_effect = call


@pytest.mark.parametrize("previous", [None, 6, 20])
def test_first_poll_changes_and_counter_reset_refresh(monitor, previous):
    wire_replies(monitor)
    monitor._scene_version = previous
    asyncio.run(monitor._poll_scene_state())
    assert [call.args[0] for call in monitor.call.call_args_list] == [
        "scene_state", "get_scene_info", "viewport_capture"]
    assert monitor.call.call_args.args[1]["auto"] is True
    assert monitor._scene_version == 7
    assert monitor.state["preview"]["attachment_id"] == "preview-image"


def test_unchanged_version_does_not_fetch_details_or_preview(monitor):
    wire_replies(monitor)
    monitor._scene_version = 7
    monitor.state["scene"] = {**SCENE, **STATE}
    monitor.state["preview"] = {"attachment_id": "existing"}
    asyncio.run(monitor._poll_scene_state())
    assert monitor.call.await_count == 1
    monitor._publish.assert_not_awaited()


def test_initial_preview_is_captured_for_an_unchanged_empty_scene(monitor):
    wire_replies(monitor, state={**STATE, "version": 0, "changed": False})
    monitor._scene_version = 0
    monitor.state["scene"] = {**SCENE, **STATE, "version": 0}
    asyncio.run(monitor._poll_scene_state())
    assert monitor.state["preview"]["attachment_id"] == "preview-image"
    assert monitor._scene_version == 0


def test_dirty_flag_updates_without_recapture(monitor):
    wire_replies(monitor, state={**STATE, "is_dirty": False})
    monitor._scene_version = 7
    monitor.state["scene"] = {**SCENE, **STATE}
    monitor.state["preview"] = {"attachment_id": "existing"}
    asyncio.run(monitor._poll_scene_state())
    assert monitor.call.await_count == 1
    assert monitor.state["scene"]["is_dirty"] is False


def test_active_object_change_refreshes_even_without_counter_change(monitor):
    wire_replies(monitor, state={**STATE, "active": None})
    monitor._scene_version = 7
    monitor.state["scene"] = {**SCENE, **STATE}
    monitor.state["preview"] = {"attachment_id": "existing"}
    asyncio.run(monitor._poll_scene_state())
    assert [call.args[0] for call in monitor.call.call_args_list] == [
        "scene_state", "get_scene_info", "viewport_capture"]


def test_auto_preview_off_still_refreshes_scene(monitor):
    wire_replies(monitor)
    monitor._settings.get()["mcp"]["auto_preview"] = False
    asyncio.run(monitor._poll_scene_state())
    assert [call.args[0] for call in monitor.call.call_args_list] == ["scene_state", "get_scene_info"]
    assert monitor._scene_version == 7


@pytest.mark.parametrize("failure", ["get_scene_info", "viewport_capture"])
def test_failed_update_keeps_version_for_retry(monitor, failure):
    wire_replies(monitor)
    original = monitor.call.side_effect
    monitor._scene_version = 6

    async def fail(tool, *args, **kwargs):
        if tool == failure:
            return reply("update failed", error=True)
        return await original(tool, *args, **kwargs)

    monitor.call.side_effect = fail
    asyncio.run(monitor._poll_scene_state())
    assert monitor._scene_version == 6 and monitor.state["error"] == "update failed"
    monitor.call.side_effect = original
    asyncio.run(monitor._poll_scene_state())
    assert monitor._scene_version == 7
    assert monitor.state["error"] == ""


def test_empty_capture_is_not_acknowledged(monitor):
    wire_replies(monitor)
    original = monitor.call.side_effect

    async def no_image(tool, *args, **kwargs):
        return reply() if tool == "viewport_capture" else await original(tool, *args, **kwargs)

    monitor.call.side_effect = no_image
    asyncio.run(monitor._poll_scene_state())
    assert monitor._scene_version is None
    assert "画像を取得できません" in monitor.state["error"]


def test_old_addon_disables_polling_and_shows_update_hint(monitor):
    monitor.call.return_value = reply("The Blender addon is too old for scene state.", error=True)
    asyncio.run(monitor._poll_scene_state())
    assert monitor.state["error"] == ADDON_UPDATE_MSG
    assert monitor._scene_state_supported is False
    asyncio.run(monitor._poll_scene_state())
    assert monitor.call.await_count == 1


def test_old_addon_status_uses_scene_fallback_without_poll(monitor):
    monitor.call.side_effect = [reply(json.dumps({"source": "missing", "capabilities": []})),
                                reply("Scene 'Old' | empty | active none | selected none | mode OBJECT")]
    result = asyncio.run(monitor.refresh(preview=False))
    assert result["ok"] and monitor.state["error"] == ADDON_UPDATE_MSG
    assert not monitor._scene_state_supported
    assert [call.args[0] for call in monitor.call.call_args_list] == ["get_addon_status", "get_scene_info"]


def test_old_mcp_server_keeps_manual_refresh_without_new_tool(monitor):
    wire_replies(monitor)
    monitor._scene_state_available = False
    result = asyncio.run(monitor.refresh(preview=False))
    assert result["ok"] and not monitor._scene_state_supported
    assert monitor.state["error"] == ""
    assert [call.args[0] for call in monitor.call.call_args_list] == ["get_addon_status", "get_scene_info"]


def test_poll_timeout_marks_unresponsive(monitor):
    monitor.call.return_value = reply("Could not read scene state: Timeout waiting for Blender response", error=True)
    asyncio.run(monitor._poll_scene_state())
    assert monitor.state["blender"] == "unresponsive"
    assert monitor.state["addon"]["text"] == UNRESPONSIVE_MSG
    assert monitor._scene_version is None


def test_refresh_holds_lock_through_preview_and_poll_skips(monitor):
    async def check():
        wire_replies(monitor)
        original = monitor.call.side_effect
        entered = asyncio.Event()
        release = asyncio.Event()

        async def capture(tool, *args, **kwargs):
            if tool == "viewport_capture":
                entered.set()
                await release.wait()
            return await original(tool, *args, **kwargs)

        monitor.call.side_effect = capture
        task = asyncio.create_task(monitor.refresh())
        await asyncio.wait_for(entered.wait(), 1)
        assert monitor._refresh_lock.locked()
        calls = monitor.call.await_count
        await monitor._poll_scene_state()
        assert monitor.call.await_count == calls
        release.set()
        assert (await task)["ok"]
        assert not monitor._refresh_lock.locked()

    asyncio.run(check())
