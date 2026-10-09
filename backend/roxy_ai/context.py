"""Conversation hand-off between AIs.

Two things are kept apart on purpose:
- the conversation itself (our messages table, provider-neutral), which can
  always be handed to any AI as text, and
- each AI's internal session (Claude session / Codex thread), which holds tool
  state and can only be resumed by that same AI.

When sending to provider P:
- P has a session and has seen everything: resume it, send only the new message.
- P has a session but other AIs spoke since: resume it and prepend what it missed.
- P has no (usable) session: start fresh and prepend the whole conversation.
"""

from __future__ import annotations

PROVIDER_NAMES = {"claude": "Claude Code", "codex": "Codex", "local": "ローカルLLM"}

HANDOFF_INTRO_FRESH = (
    "以下は、このアプリでこれまでに行われた会話の記録です（別のAIとのやり取りを含む場合があります）。"
    "会話内容のみの引き継ぎで、以前のツール実行状態やAI内部のセッションは引き継がれていません。"
    "Blenderの現在の状態が必要な場合は、MCPツールで確認してください。"
)
HANDOFF_INTRO_MISSED = (
    "前回のあなたの応答以降に、このアプリ上で別のAIとの間で以下のやり取りがありました。"
    "Blenderのシーンが変更されている可能性があります。"
)


def plan_context(messages: list[dict], session: dict | None, new_seq: int) -> tuple[str | None, list[dict]]:
    """Return (session id to resume or None, earlier messages to include as text)."""
    prior = [
        m for m in messages
        if m["seq"] < new_seq and m["role"] in ("user", "assistant") and (m.get("content") or m.get("tool_calls"))
    ]
    if session:
        missed = [m for m in prior if m["seq"] > session["synced_seq"]]
        return session["session_id"], missed
    return None, prior


def _render_message(m: dict) -> str:
    if m["role"] == "user":
        who = "ユーザー"
    else:
        who = f"AI（{PROVIDER_NAMES.get(m.get('provider', ''), m.get('provider') or '不明')}）"
    body = m.get("content") or ""
    tools = m.get("tool_calls") or []
    if tools:
        names = ", ".join(dict.fromkeys(t.get("name", "?") for t in tools))
        body += f"\n（実行したツール: {names}）"
    if m.get("status") == "cancelled":
        body += "\n（この応答はユーザーにより中断されました）"
    elif m.get("status") == "error":
        body += f"\n（この応答はエラーで終了しました: {m.get('error', '')}）"
    return f"### {who}\n{body.strip()}"


def render_handoff(messages: list[dict], fresh: bool, max_chars: int = 40000) -> str:
    if not messages:
        return ""
    text = "\n\n".join(_render_message(m) for m in messages)
    if len(text) > max_chars:
        text = "…（古い部分は文字数制限のため省略）…\n" + text[-max_chars:]
    intro = HANDOFF_INTRO_FRESH if fresh else HANDOFF_INTRO_MISSED
    return f"<conversation_history>\n{intro}\n\n{text}\n</conversation_history>\n\n以下が今回のユーザーのメッセージです。"
