"""Find out why the Blender port accepts connections but never answers.

The usual cause is another server on the same port: Blender's own "MCP"
extension (Blender Lab) also defaults to 9876, and on Windows SO_REUSEADDR
lets two sockets listen on one port, so commands can land on the wrong one.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from .procs import run_capture

TOKEN_DIR = Path.home() / ".roxy-blender-mcp"
ROXY_MARKERS = ("Roxy Blender MCP", "roxy_blender_mcp", "Roxy-BlenderMCP")


def roxy_token(port: int) -> str | None:
    try:
        return (TOKEN_DIR / f"token-{port}").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def parse_netstat(text: str, port: int) -> list[int]:
    """PIDs listening on `port` from `netstat -ano -p TCP` output."""
    pids = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            try:
                pids.append(int(parts[4]))
            except ValueError:
                pass
    return pids


async def listening_pids(port: int) -> list[int]:
    if os.name != "nt":  # macOS / Linux: lsof prints one PID per line with -t
        code, out = await run_capture(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout=10)
        return sorted({int(x) for x in out.split() if x.isdigit()}) if code == 0 else []
    code, out = await run_capture(["netstat", "-ano", "-p", "TCP"], timeout=10)
    return parse_netstat(out, port) if code == 0 else []


async def process_name(pid: int) -> str:
    if os.name != "nt":
        code, out = await run_capture(["ps", "-p", str(pid), "-o", "comm="], timeout=10)
        return Path(out.strip()).name if code == 0 else ""
    code, out = await run_capture(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
    m = re.match(r'^"([^"]+)"', out.strip()) if code == 0 else None
    return m.group(1) if m else ""


async def roxy_answers(host: str, port: int, token: str | None) -> bool:
    """Send one read-only command the Roxy addon always answers (even 'Unauthorized')."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    except Exception:
        return False
    try:
        cmd = {"type": "get_addon_info", "params": {}}
        if token:
            cmd["auth"] = token
        writer.write(json.dumps(cmd).encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4096), 3)
        return bool(data) and b'"status"' in data
    except Exception:
        return False
    finally:
        writer.close()


def blender_config_root() -> Path:
    """Blender's per-user config folder; versions (e.g. 4.5) are its subfolders."""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    return Path.home() / ".config" / "blender"


def other_blender_addons_on_port(port: int, roots: list[Path] | None = None) -> list[str]:
    """Names of non-Roxy Blender add-ons/extensions whose code mentions this port."""
    if roots is None:
        base = blender_config_root()
        roots = [p for v in base.glob("*") for p in (v / "extensions", v / "scripts" / "addons")]
    found: list[str] = []
    pattern = re.compile(rf"\b{port}\b")
    for root in roots:
        if not root.is_dir():
            continue
        for py in _addon_sources(root):
            try:
                if py.stat().st_size > 400_000:
                    continue
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not pattern.search(text) or "socket" not in text or any(m in text for m in ROXY_MARKERS):
                continue
            pkg = _package_root(py, root)
            name = _manifest_name(pkg) or pkg.name
            if name not in found:
                found.append(name)
    return found


_SKIP_DIRS = {"__pycache__", "site-packages", "node_modules", ".git", "lib", "libs", "vendor", "wheels", "bin", "share"}


def _addon_sources(root: Path, max_depth: int = 4):
    """Python files of add-ons, skipping bundled third-party libraries (they can be huge)."""
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in _SKIP_DIRS and "dependencies" not in d.lower() and depth < max_depth]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _package_root(py: Path, root: Path) -> Path:
    rel = py.relative_to(root).parts
    # extensions/<repo>/<pkg>/... or addons/<pkg>/...; a single-file addon is itself
    if root.name == "extensions" and len(rel) >= 2:
        return root / rel[0] / rel[1]
    return root / rel[0]


def _manifest_name(pkg: Path) -> str:
    manifest = pkg / "blender_manifest.toml"
    try:
        m = re.search(r'^name\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


async def diagnose(host: str, port: int) -> dict | None:
    """Return {"kind", "message"} when a port conflict explains the silence, else None."""
    pids = await listening_pids(port)
    token = roxy_token(port)
    if not pids:
        return None
    names = [await process_name(pid) for pid in dict.fromkeys(pids)]
    owner = names[0] if names else ""
    in_blender = owner.lower().startswith("blender")
    if not in_blender and owner:
        return {"kind": "other_app",
                "message": f"{port} 番ポートを別のアプリ（{owner}）が使っています。そのアプリを終了するか、"
                           "プロジェクト設定で Blender のポート番号を変えて Roxy Blender MCP 側も同じ番号にしてください。"}
    if await roxy_answers(host, port, token):
        return None  # Roxy itself answers: no conflict, Blender is just busy
    addons = await asyncio.to_thread(other_blender_addons_on_port, port)
    who = "・".join(f"「{a}」" for a in addons) or "別の MCP アドオン（Blender 公式の「MCP」拡張など）"
    if len(pids) > 1:
        detail = f"{port} 番ポートで複数のサーバーが待ち受けています。"
    elif not token:
        detail = "Roxy Blender MCP のサーバーが起動できていません。"
    else:
        detail = "このポートに応答しているのは Roxy Blender MCP ではありません。"
    return {"kind": "port_conflict", "addons": addons,
            "message": f"{detail}Blender 内の{who}が同じ {port} 番ポートを使っています。"
                       "Blender の 環境設定 → アドオン でそのアドオンを無効にするかポート番号を変更し、"
                       "作業を保存して Blender を再起動してから、Roxy Blender MCP で接続してください。"}
