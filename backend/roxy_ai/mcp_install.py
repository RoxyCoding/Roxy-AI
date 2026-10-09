"""Install the Blender MCP into the app's data directory when it is missing."""

import asyncio
import hashlib
import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

from .config import SettingsStore, _is_mcp_checkout
from .procs import run_capture

logger = logging.getLogger(__name__)
MCP_SOURCE = "https://github.com/RoxyCoding/Roxy-BlenderMCP-v2/archive/7f4e53ab8b9538e28f8417b771911facbe717f43.zip"
UV_SOURCE = "https://github.com/astral-sh/uv/releases/download/0.12.24/uv-x86_64-pc-windows-msvc.zip"
UV_SHA256 = "7c38608c8a18ee137d748a1773053b07ec8f3a30fab49aebaa6f4e4efeceb019"


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


def _extract_source(data: bytes, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Stage a complete, validated checkout before exposing it to other clients.
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staging = Path(temp).resolve()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100_000_000:
                raise ValueError("MCPのダウンロードサイズが大きすぎます")
            for item in archive.infolist():
                relative = Path(*item.filename.replace("\\", "/").split("/")[1:])
                destination = (staging / relative).resolve()
                if not destination.is_relative_to(staging):
                    raise ValueError("MCPアーカイブに不正なパスがあります")
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(item))
        if not _is_mcp_checkout(staging):
            raise ValueError("Roxy Blender MCPのダウンロード内容を確認できませんでした")
        staging.rename(target)


class MCPInstaller:
    def __init__(self, settings: SettingsStore, root: Path):
        self.settings, self.root = settings, root
        self._lock = asyncio.Lock()
        self._ready = False

    async def _uv(self) -> str:
        installed = shutil.which("uv")
        if installed:
            return installed
        target = self.root / "tools" / "uv.exe"
        if target.is_file():
            return str(target)
        if os.name != "nt":
            raise RuntimeError("MCPの自動導入には uv が必要です。uv をインストールしてください。")
        data = await _download(UV_SOURCE)
        if hashlib.sha256(data).hexdigest() != UV_SHA256:
            raise ValueError("uvのダウンロード検証に失敗しました")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            binary = archive.read("uv.exe")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_bytes(binary)
        temp.replace(target)
        return str(target)

    async def ensure(self) -> None:
        async with self._lock:
            conf = self.settings.get()["mcp"]
            if conf.get("command"):
                return
            source = Path(conf.get("server_dir") or self.root / "mcp" / "Roxy-BlenderMCP-v2")
            if not _is_mcp_checkout(source):
                source = self.root / "mcp" / "Roxy-BlenderMCP-v2"
                if not source.exists():
                    await asyncio.to_thread(_extract_source, await _download(MCP_SOURCE), source)
                if not _is_mcp_checkout(source):
                    raise RuntimeError(f"MCPの保存先を確認してください: {source}")
            if conf.get("server_dir") != str(source):
                self.settings.update({"mcp": {"server_dir": str(source)}})
            bin_dir = source / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            exe = bin_dir / ("roxy-blender-mcp.exe" if os.name == "nt" else "roxy-blender-mcp")
            if not exe.is_file():
                uv = await self._uv()
                code, output = await run_capture([uv, "sync", "--directory", str(source), "--python", "3.10"], timeout=600)
                if code or not exe.is_file():
                    raise RuntimeError(f"MCPの依存関係を導入できませんでした: {output[-2000:]}")
            if self._ready:
                return
            python = bin_dir / ("python.exe" if os.name == "nt" else "python")
            # Only the addon setup is used; other apps' MCP configurations are untouched.
            script = (
                "from blender_mcp.addon_manager import find_existing_addon_installs; "
                "from blender_mcp.setup_cli import setup_addon; "
                "existing = find_existing_addon_installs(); "
                "outcome = None if existing else setup_addon(False); "
                "print('\\n'.join(outcome.lines) if outcome else 'Addon already installed'); "
                "raise SystemExit(0 if existing or outcome.installed else 1)"
            )
            code, output = await run_capture([str(python), "-c", script], timeout=180)
            if code:
                raise RuntimeError(f"Blenderアドオンの導入を確認できませんでした: {output[-2000:]}")
            logger.info("Blender MCP setup: %s", output)
            self._ready = True
