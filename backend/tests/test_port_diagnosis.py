from pathlib import Path

from roxy_ai.port_diagnosis import other_blender_addons_on_port, parse_netstat

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:9876         0.0.0.0:0              LISTENING       86208
  TCP    127.0.0.1:9876         0.0.0.0:0              LISTENING       86208
  TCP    127.0.0.1:19876        0.0.0.0:0              LISTENING       5
  TCP    127.0.0.1:9876         127.0.0.1:5000         ESTABLISHED     86208
"""


def test_netstat_listeners():
    assert parse_netstat(NETSTAT, 9876) == [86208, 86208]
    assert parse_netstat(NETSTAT, 19876) == [5]


def test_finds_other_mcp_extension_but_not_roxy(tmp_path):
    ext = tmp_path / "5.1" / "extensions"
    lab = ext / "user_default" / "mcp"
    lab.mkdir(parents=True)
    (lab / "blender_manifest.toml").write_text('id = "mcp"\nname = "MCP"\n', encoding="utf-8")
    (lab / "server.py").write_text("import socket\nDEFAULT_PORT = 9876\n", encoding="utf-8")
    deps = lab / "big_dependencies"
    deps.mkdir()
    (deps / "x.py").write_text("import socket\nPORT = 9876\n", encoding="utf-8")
    addons = tmp_path / "5.1" / "scripts" / "addons"
    addons.mkdir(parents=True)
    (addons / "addon.py").write_text('import socket\nADDON = "Roxy Blender MCP"\nport = 9876\n', encoding="utf-8")
    assert other_blender_addons_on_port(9876, [ext, addons]) == ["MCP"]
    assert other_blender_addons_on_port(1234, [ext, addons]) == []
