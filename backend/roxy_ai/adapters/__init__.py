"""AI backend registry. To add a backend, implement AIAdapter and register it here."""

from __future__ import annotations

from pathlib import Path

from ..config import Secrets, SettingsStore
from .base import AIAdapter


def build_adapters(settings: SettingsStore, secrets: Secrets, probe_cwd: Path,
                   data_root: Path | None = None) -> dict[str, AIAdapter]:
    from .claude_code import ClaudeCodeAdapter
    from .codex import CodexAdapter
    from .local_llm import LocalLLMAdapter

    adapters: list[AIAdapter] = [
        ClaudeCodeAdapter(settings, secrets, probe_cwd),
        CodexAdapter(settings),
        LocalLLMAdapter(settings, data_root or probe_cwd.parent, (data_root or probe_cwd.parent) / "logs"),
    ]
    return {a.id: a for a in adapters}
