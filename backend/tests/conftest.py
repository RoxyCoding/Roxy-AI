import pytest


@pytest.fixture(autouse=True)
def _no_live_mcp_install(monkeypatch):
    """Application tests must not install software or change Blender preferences."""
    from roxy_ai.mcp_monitor import BlenderMonitor

    async def ensure_installed(self):
        pass

    monkeypatch.setattr(BlenderMonitor, "ensure_installed", ensure_installed)


@pytest.fixture(autouse=True)
def _no_live_unreal(monkeypatch):
    """Tests must not connect to a real Unreal Editor that may be running on this PC."""
    from roxy_ai.unreal_monitor import UnrealMonitor

    async def start(self):
        await self._set(status="disabled")

    monkeypatch.setattr(UnrealMonitor, "start", start)


@pytest.fixture(autouse=True)
def _no_live_extra_mcp(monkeypatch):
    """Settings must not depend on MCP checkouts on this PC, nor probe running apps."""
    from roxy_ai import extra_mcp

    async def start(self):
        self.state = {}

    monkeypatch.setattr(extra_mcp, "detect_presets", lambda settings: [])
    monkeypatch.setattr(extra_mcp.ExtraMcpMonitor, "start", start)
