import json
from pathlib import Path

from roxy_ai.adapters.base import RunRequest
from roxy_ai.adapters.claude_code import ClaudeCodeAdapter
from roxy_ai.adapters.codex import CodexAdapter
from roxy_ai.config import Secrets, SettingsStore
from roxy_ai.unreal_monitor import actor_name, level_of, parse_toolsets

REF = "/Game/ThirdPerson/Lvl_ThirdPerson.Lvl_ThirdPerson:PersistentLevel.StaticMeshActor_UAID_1"


def test_ref_path_helpers():
    assert actor_name(REF) == "StaticMeshActor_UAID_1"
    assert level_of(REF) == "/Game/ThirdPerson/Lvl_ThirdPerson"
    assert parse_toolsets("- A.B: desc\n  more\n- C.D: x") == ["A.B", "C.D"]


def test_http_mcp_server_reaches_both_ais(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    req = RunRequest(conversation_id="c", parts=[], cwd=".", mcp_servers={
        "blender": {"command": "roxy-blender-mcp.exe", "args": [], "env": {}},
        "unreal-mcp": {"url": "http://127.0.0.1:8000/mcp"},
    })
    claude = ClaudeCodeAdapter(settings, Secrets(), tmp_path)._options(req, None)
    assert claude.mcp_servers["unreal-mcp"] == {"type": "http", "url": "http://127.0.0.1:8000/mcp"}
    assert claude.mcp_servers["blender"]["type"] == "stdio"
    codex = CodexAdapter(settings)._thread_params(req)["config"]["mcp_servers"]
    assert codex["unreal-mcp"] == {"url": "http://127.0.0.1:8000/mcp", "tool_timeout_sec": 600}


def test_unreal_settings_default_and_injection(tmp_path):
    from roxy_ai.chat import ChatService

    settings = SettingsStore(tmp_path / "settings.json")
    svc = ChatService.__new__(ChatService)
    svc.settings = settings
    project = {"blender_host": "localhost", "blender_port": 9876}
    servers = svc._mcp_servers(project)
    assert servers["unreal-mcp"] == {"url": "http://127.0.0.1:8000/mcp"} and "blender" in servers
    settings.update({"unreal": {"enabled": False}})
    assert "unreal-mcp" not in svc._mcp_servers(project)


def test_unreal_read_only_detection():
    from roxy_ai.unreal_monitor import unreal_call_is_read_only as ro

    assert ro("list_toolsets", {}) and ro("describe_toolset", {"toolset_name": "X"})
    assert ro("call_tool", {"toolset_name": "EditorToolset.EditorAppToolset", "tool_name": "GetSelectedActors"})
    assert ro("call_tool", {"tool_name": "CaptureViewport"}) and ro("call_tool", {"tool_name": "get_actor_transform"})
    for writer in ("SetCameraTransform", "StartPIE", "SpawnActor", "DeleteActors", "SelectActors", "create_material"):
        assert not ro("call_tool", {"tool_name": writer}), writer
    assert not ro("call_tool", {"toolset_name": "editor_toolset.toolsets.programmatic.ProgrammaticToolset", "tool_name": "GetRun"})
    assert not ro("call_tool", None)
