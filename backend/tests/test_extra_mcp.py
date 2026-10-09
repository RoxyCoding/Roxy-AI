import asyncio

import pytest
from fastapi import HTTPException

from roxy_ai import extra_mcp
from roxy_ai.adapters.base import ApprovalRequest
from roxy_ai.api import _check_extra_mcp, create_app
from roxy_ai.chat import Run
from roxy_ai.config import Paths, SettingsStore

# Bound before conftest replaces extra_mcp.detect_presets for every test.
real_detect = extra_mcp.detect_presets

PAINTER = {**{k: v for k, v in extra_mcp.PRESETS["substance-painter"].items() if k != "folder"},
           "server_dir": "C:/nowhere/Roxy-Substance-3D-Painter-MCP"}


def test_server_spec_prefers_venv_exe(tmp_path):
    exe = tmp_path / ".venv" / "Scripts" / "roxy-painter-mcp.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    spec = extra_mcp.server_spec({**PAINTER, "server_dir": str(tmp_path)})
    assert spec == {"command": str(exe), "args": [], "env": {}}


def test_server_spec_fallbacks():
    spec = extra_mcp.server_spec(PAINTER)  # no venv: uv run in the checkout
    assert spec["args"][-3:] == ["--directory", PAINTER["server_dir"], "roxy-painter-mcp"]
    assert extra_mcp.server_spec({"name": "x", "url": "http://h/mcp"}) == {"url": "http://h/mcp"}
    assert extra_mcp.server_spec({"name": "x", "command": "a.exe", "args": ["-v"]})["args"] == ["-v"]
    assert extra_mcp.server_spec({"name": "x"}) is None


def test_detect_presets_adds_only_once(tmp_path):
    found = real_detect({"extra_mcp": {"servers": [], "auto_detected": []}})
    if found:  # this PC has the Painter checkout next to the app
        assert found[0]["name"] == "substance-painter" and found[0]["server_dir"]
    assert real_detect({"extra_mcp": {"servers": [], "auto_detected": ["substance-painter"]}}) == []
    assert real_detect({"extra_mcp": {"servers": [{"name": "substance-painter"}], "auto_detected": []}}) == []


def test_injected_into_ai_with_prompt(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    state.settings.update({"extra_mcp": {"servers": [PAINTER]}})
    project = {"id": "p", "name": "P", "blender_host": "localhost", "blender_port": 9876}
    servers = state.chat._mcp_servers(project)
    assert "substance-painter" in servers and "blender" in servers
    assert "substance-painter" in state.chat._system_append(project)
    state.extra.state = {"substance-painter": "offline"}
    assert PAINTER["offline_hint"] in state.chat._system_append(project)
    state.settings.update({"extra_mcp": {"servers": [{**PAINTER, "enabled": False}]}})
    assert "substance-painter" not in state.chat._mcp_servers(project)


def test_approval_rules(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    state.settings.update({"extra_mcp": {"servers": [PAINTER]}})
    run = Run("r", "c", "p", "codex", "", "u", "a", "lock")
    state.chat.runs["c"] = run

    async def check():
        read = await state.chat._approve(run, ApprovalRequest("tool", "t", tool_name="mcp__substance-painter__get_layer_stack"))
        assert read.allow
        with pytest.raises(asyncio.TimeoutError):  # edits wait for the user
            await asyncio.wait_for(state.chat._approve(
                run, ApprovalRequest("tool", "t", tool_name="mcp__substance-painter__add_layer")), 0.2)
        state.settings.update({"extra_mcp": {"servers": [{**PAINTER, "auto_approve_all": True}]}})
        assert (await state.chat._approve(run, ApprovalRequest("tool", "t", tool_name="mcp__substance-painter__add_layer"))).allow

    asyncio.run(check())


def test_lock_per_app(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    state.settings.update({"extra_mcp": {"servers": [PAINTER]}})
    project = {"id": "p", "name": "P", "blender_host": "localhost", "blender_port": 9876}
    assert "extra:substance-painter" in state.chat._check_free("c1", project).split("|")


def test_settings_validation(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json").get()
    _check_extra_mcp({"servers": [PAINTER]}, settings)
    for bad in ([{"name": "blender"}], [{"name": "a b"}], [{"name": "x"}, {"name": "x"}], "nope"):
        with pytest.raises(HTTPException):
            _check_extra_mcp({"servers": bad}, settings)
