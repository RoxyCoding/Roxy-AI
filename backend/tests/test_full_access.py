from types import SimpleNamespace

from roxy_ai.adapters.base import RunRequest
from roxy_ai.adapters.claude_code import ClaudeCodeAdapter
from roxy_ai.adapters.codex import CodexAdapter
from roxy_ai.chat import ChatService
from roxy_ai.config import Secrets, SettingsStore


def _settings(tmp_path, full_access=False):
    s = SettingsStore(tmp_path / "settings.json")
    s.update({"approval": {"full_access": full_access}})
    return s


def _req(**kw):
    return RunRequest(conversation_id="c", parts=[], cwd=".", **kw)


def test_codex_turn_policy_follows_toggle(tmp_path):
    codex = CodexAdapter(_settings(tmp_path))
    assert codex._turn_access(_req(), "t") == {}  # normal: thread defaults
    assert codex._turn_access(_req(full_access=True), "t") == {
        "approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}}
    # Turned off again: the open thread gets the normal policy back once.
    assert codex._turn_access(_req(), "t") == {
        "approvalPolicy": "on-request", "sandboxPolicy": {"type": "workspaceWrite"}}
    assert codex._turn_access(_req(), "t") == {}
    params = codex._thread_params(_req(full_access=True))
    assert params["approvalPolicy"] == "never" and params["sandbox"] == "danger-full-access"


def test_claude_permission_mode(tmp_path):
    claude = ClaudeCodeAdapter(_settings(tmp_path), Secrets(), tmp_path)
    assert claude._options(_req(full_access=True), None).permission_mode == "bypassPermissions"
    assert claude._options(_req(), None).permission_mode == "default"
    assert claude._live_key(_req(full_access=True)) != claude._live_key(_req())


def test_full_access_never_applies_to_prompt_chat(tmp_path):
    service = SimpleNamespace(settings=_settings(tmp_path, full_access=True))
    assert ChatService.full_access_for(service, SimpleNamespace(mode="chat"))
    assert not ChatService.full_access_for(service, SimpleNamespace(mode="prompt"))
    service.settings.update({"approval": {"full_access": False}})
    assert not ChatService.full_access_for(service, SimpleNamespace(mode="chat"))
