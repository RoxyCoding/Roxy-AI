import asyncio
import hashlib
import io
import os
import zipfile
from unittest.mock import AsyncMock

import pytest

from roxy_ai import mcp_install
from roxy_ai.config import SettingsStore
from roxy_ai.mcp_install import MCPInstaller, _extract_source
from roxy_ai.mcp_monitor import mcp_server_command


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buffer.getvalue()


def test_missing_mcp_installs_once_and_preserves_custom_settings(tmp_path, monkeypatch):
    settings = SettingsStore(tmp_path / "settings.json")
    settings.update({"mcp": {"server_dir": str(tmp_path / "missing"), "server_name": "my-blender"}})
    installer = MCPInstaller(settings, tmp_path)
    source = archive({"repo/pyproject.toml": '[project]\nname = "roxy-blender-mcp"', "repo/src/server.py": ""})
    download = AsyncMock(return_value=source)
    monkeypatch.setattr(mcp_install, "_download", download)
    monkeypatch.setattr(mcp_install.shutil, "which", lambda command: "uv")
    commands = []

    async def run(command, timeout):
        commands.append(command)
        if command[1] == "sync":
            root = tmp_path / "mcp" / "Roxy-BlenderMCP-v2"
            bin_dir = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            bin_dir.mkdir(parents=True)
            (bin_dir / ("roxy-blender-mcp.exe" if os.name == "nt" else "roxy-blender-mcp")).write_text("")
        return 0, "installed"

    monkeypatch.setattr(mcp_install, "run_capture", run)

    async def check():
        await asyncio.gather(installer.ensure(), installer.ensure())
        await installer.ensure()

    asyncio.run(check())
    assert download.await_count == 1 and len(commands) == 2
    assert settings.get()["mcp"]["server_name"] == "my-blender"
    assert "--no-sync" not in mcp_server_command(settings.get(), "localhost", 9876)["args"]
    assert "setup_cli import setup_addon" in commands[1][-1]


def test_explicit_mcp_command_is_not_replaced(tmp_path, monkeypatch):
    settings = SettingsStore(tmp_path / "settings.json")
    settings.update({"mcp": {"command": "custom-mcp", "args": ["custom"]}})
    download = AsyncMock()
    monkeypatch.setattr(mcp_install, "_download", download)
    asyncio.run(MCPInstaller(settings, tmp_path).ensure())
    download.assert_not_awaited()
    assert settings.get()["mcp"]["command"] == "custom-mcp"


def test_install_failure_can_be_retried(tmp_path, monkeypatch):
    settings = SettingsStore(tmp_path / "settings.json")
    settings.update({"mcp": {"server_dir": str(tmp_path / "missing")}})
    installer = MCPInstaller(settings, tmp_path)
    download = AsyncMock(side_effect=OSError("offline"))
    monkeypatch.setattr(mcp_install, "_download", download)
    for _ in range(2):
        with pytest.raises(OSError, match="offline"):
            asyncio.run(installer.ensure())
    assert download.await_count == 2 and not installer._ready


def test_source_archive_cannot_escape_install_directory(tmp_path):
    target = tmp_path / "mcp"
    with pytest.raises(ValueError, match="不正なパス"):
        _extract_source(archive({"repo/../../escape.py": "bad"}), target)
    assert not target.exists() and not (tmp_path.parent / "escape.py").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows binary")
def test_uv_download_is_verified_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_install.shutil, "which", lambda command: None)
    data = archive({"uv.exe": b"uv-test"})
    monkeypatch.setattr(mcp_install, "_download", AsyncMock(return_value=data))
    installer = MCPInstaller(SettingsStore(tmp_path / "settings.json"), tmp_path)
    with pytest.raises(ValueError, match="検証"):
        asyncio.run(installer._uv())
    assert not (tmp_path / "tools" / "uv.exe").exists()
    monkeypatch.setattr(mcp_install, "UV_SHA256", hashlib.sha256(data).hexdigest())
    assert asyncio.run(installer._uv()) == str(tmp_path / "tools" / "uv.exe")
    assert (tmp_path / "tools" / "uv.exe").read_bytes() == b"uv-test"
