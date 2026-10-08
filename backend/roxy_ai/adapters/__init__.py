"""AI backend registry. To add a backend, implement AIAdapter and register it here."""

from __future__ import annotations

from pathlib import Path

from ..config import Secrets, SettingsStore
from .base import AIAdapter


def build_adapters(settings: SettingsStore, secrets: Secrets, probe_cwd: Path) -> dict[str, AIAdapter]:
    from .claude_code import ClaudeCodeAdapter
    from .codex import CodexAdapter

    adapters: list[AIAdapter] = [
        ClaudeCodeAdapter(settings, secrets, probe_cwd),
        CodexAdapter(settings),
    ]
    return {a.id: a for a in adapters}
