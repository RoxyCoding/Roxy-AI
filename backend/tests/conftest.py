import pytest


@pytest.fixture(autouse=True)
def _no_live_unreal(monkeypatch):
    """Tests must not connect to a real Unreal Editor that may be running on this PC."""
    from roxy_ai.unreal_monitor import UnrealMonitor

    async def start(self):
        await self._set(status="disabled")

    monkeypatch.setattr(UnrealMonitor, "start", start)
