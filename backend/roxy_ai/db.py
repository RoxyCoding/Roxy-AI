"""SQLite storage: projects, conversations, messages, AI sessions, attachments.

Messages are stored in one provider-neutral format so a conversation can move
between AIs. `ai_sessions` keeps each AI's own session/thread id separately:
that internal state belongs to the AI and is only resumed when it is still
consistent with the shared history (see context.py).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    blend_path TEXT NOT NULL DEFAULT '',
    work_dir TEXT NOT NULL DEFAULT '',
    blender_host TEXT NOT NULL DEFAULT 'localhost',
    blender_port INTEGER NOT NULL DEFAULT 9876,
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'modeling',
    brief TEXT NOT NULL DEFAULT '{}',
    draft TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,              -- user | assistant | notice
    content TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'complete',  -- complete | streaming | error | cancelled
    error TEXT NOT NULL DEFAULT '',
    tool_calls TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(conversation_id, seq)
);

CREATE TABLE IF NOT EXISTS ai_sessions (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    synced_seq INTEGER NOT NULL,     -- last message seq this session has seen
    model TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, provider)
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    kind TEXT NOT NULL,              -- upload | tool_image | preview
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

JSON_FIELDS = ("tool_calls", "attachments", "meta", "brief", "draft")


def new_id() -> str:
    return uuid.uuid4().hex


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except json.JSONDecodeError:
                pass
    return d


class Database:
    """Small synchronous wrapper; every call is a short local transaction."""

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was created."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(conversations)")}
        if "effort" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN effort TEXT NOT NULL DEFAULT ''")
        if "mode" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'modeling'")
        if "brief" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN brief TEXT NOT NULL DEFAULT '{}'")
        if "draft" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN draft TEXT NOT NULL DEFAULT '{}'")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    # Projects --------------------------------------------------------------

    def create_project(self, name: str, **fields: Any) -> dict:
        now = time.time()
        pid = new_id()
        self._exec(
            "INSERT INTO projects (id, name, blend_path, work_dir, blender_host, blender_port, notes,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                pid,
                name,
                fields.get("blend_path", ""),
                fields.get("work_dir", ""),
                fields.get("blender_host", "localhost"),
                int(fields.get("blender_port", 9876)),
                fields.get("notes", ""),
                now,
                now,
            ),
        )
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict | None:
        return self._one("SELECT * FROM projects WHERE id=?", (pid,))

    def list_projects(self) -> list[dict]:
        return self._all(
            "SELECT p.*, (SELECT COUNT(*) FROM conversations c WHERE c.project_id=p.id) AS conversation_count"
            " FROM projects p ORDER BY p.updated_at DESC"
        )

    def update_project(self, pid: str, fields: dict) -> dict | None:
        allowed = {"name", "blend_path", "work_dir", "blender_host", "blender_port", "notes"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            cols = ", ".join(f"{k}=:{k}" for k in sets)
            self._exec(f"UPDATE projects SET {cols}, updated_at=:now WHERE id=:id", {**sets, "now": time.time(), "id": pid})
        return self.get_project(pid)

    def touch_project(self, pid: str) -> None:
        self._exec("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), pid))

    def delete_project(self, pid: str) -> None:
        self._exec("DELETE FROM projects WHERE id=?", (pid,))

    # Conversations ---------------------------------------------------------

    def create_conversation(self, project_id: str, provider: str, model: str = "", title: str = "",
                            effort: str = "", mode: str = "modeling") -> dict:
        now = time.time()
        cid = new_id()
        self._exec(
            "INSERT INTO conversations (id, project_id, title, provider, model, effort, mode, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, project_id, title, provider, model, effort, mode, now, now),
        )
        return self.get_conversation(cid)

    def get_conversation(self, cid: str) -> dict | None:
        return self._one("SELECT * FROM conversations WHERE id=?", (cid,))

    def list_conversations(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            return self._all(
                "SELECT * FROM conversations WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
            )
        return self._all("SELECT * FROM conversations ORDER BY updated_at DESC")

    def update_conversation(self, cid: str, fields: dict) -> dict | None:
        allowed = {"title", "provider", "model", "effort", "project_id", "brief", "draft"}
        sets = {k: json.dumps(v, ensure_ascii=False) if k in {"brief", "draft"} else v for k, v in fields.items() if k in allowed}
        if sets:
            cols = ", ".join(f"{k}=:{k}" for k in sets)
            self._exec(f"UPDATE conversations SET {cols}, updated_at=:now WHERE id=:id", {**sets, "now": time.time(), "id": cid})
        return self.get_conversation(cid)

    def touch_conversation(self, cid: str) -> None:
        self._exec("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))

    def delete_conversation(self, cid: str) -> list[str]:
        """Delete a conversation; returns attachment file paths to remove."""
        paths = [r["path"] for r in self._all("SELECT path FROM attachments WHERE conversation_id=?", (cid,))]
        self._exec("DELETE FROM attachments WHERE conversation_id=?", (cid,))
        self._exec("DELETE FROM conversations WHERE id=?", (cid,))
        return paths

    def search(self, query: str, project_id: str | None = None, limit: int = 50) -> list[dict]:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        params: list[Any] = [like, like]
        where_project = ""
        if project_id:
            where_project = " AND c.project_id=?"
            params.append(project_id)
        params.append(limit)
        return self._all(
            "SELECT c.id AS conversation_id, c.title, c.project_id, c.updated_at,"
            " (SELECT m.content FROM messages m WHERE m.conversation_id=c.id AND m.content LIKE ? ORDER BY m.seq LIMIT 1) AS snippet"
            " FROM conversations c WHERE (c.title LIKE ? OR snippet IS NOT NULL)" + where_project +
            " ORDER BY c.updated_at DESC LIMIT ?",
            tuple(params),
        )

    # Messages --------------------------------------------------------------

    def add_message(self, conversation_id: str, role: str, content: str = "", **fields: Any) -> dict:
        mid = new_id()
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            seq = row[0]
            self._conn.execute(
                "INSERT INTO messages (id, conversation_id, seq, role, content, provider, model, status, error,"
                " tool_calls, attachments, meta, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    conversation_id,
                    seq,
                    role,
                    content,
                    fields.get("provider", ""),
                    fields.get("model", ""),
                    fields.get("status", "complete"),
                    fields.get("error", ""),
                    json.dumps(fields.get("tool_calls", []), ensure_ascii=False),
                    json.dumps(fields.get("attachments", []), ensure_ascii=False),
                    json.dumps(fields.get("meta", {}), ensure_ascii=False),
                    time.time(),
                ),
            )
        self.touch_conversation(conversation_id)
        return self.get_message(mid)

    def get_message(self, mid: str) -> dict | None:
        return self._one("SELECT * FROM messages WHERE id=?", (mid,))

    def list_messages(self, conversation_id: str) -> list[dict]:
        return self._all("SELECT * FROM messages WHERE conversation_id=? ORDER BY seq", (conversation_id,))

    def update_message(self, mid: str, fields: dict) -> dict | None:
        allowed = {"content", "status", "error", "tool_calls", "meta", "model", "attachments"}
        sets = {}
        for k, v in fields.items():
            if k in allowed:
                sets[k] = json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v
        if sets:
            cols = ", ".join(f"{k}=:{k}" for k in sets)
            self._exec(f"UPDATE messages SET {cols} WHERE id=:id", {**sets, "id": mid})
        return self.get_message(mid)

    def delete_messages_from(self, conversation_id: str, seq: int) -> None:
        """Delete messages with seq >= `seq` (used by edit / retry)."""
        self._exec("DELETE FROM messages WHERE conversation_id=? AND seq>=?", (conversation_id, seq))

    def mark_interrupted_streams(self) -> int:
        """After a crash/restart, streaming messages can never finish."""
        cur = self._exec(
            "UPDATE messages SET status='error', error='アプリ終了により応答が中断されました' WHERE status='streaming'"
        )
        return cur.rowcount

    # AI sessions -----------------------------------------------------------

    def get_ai_session(self, conversation_id: str, provider: str) -> dict | None:
        return self._one(
            "SELECT * FROM ai_sessions WHERE conversation_id=? AND provider=?", (conversation_id, provider)
        )

    def list_ai_sessions(self, conversation_id: str) -> list[dict]:
        return self._all("SELECT * FROM ai_sessions WHERE conversation_id=?", (conversation_id,))

    def set_ai_session(self, conversation_id: str, provider: str, session_id: str, synced_seq: int, model: str = "") -> None:
        self._exec(
            "INSERT INTO ai_sessions (conversation_id, provider, session_id, synced_seq, model, updated_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(conversation_id, provider) DO UPDATE SET"
            " session_id=excluded.session_id, synced_seq=excluded.synced_seq, model=excluded.model,"
            " updated_at=excluded.updated_at",
            (conversation_id, provider, session_id, synced_seq, model, time.time()),
        )

    def clear_ai_sessions(self, conversation_id: str, provider: str | None = None) -> None:
        if provider:
            self._exec("DELETE FROM ai_sessions WHERE conversation_id=? AND provider=?", (conversation_id, provider))
        else:
            self._exec("DELETE FROM ai_sessions WHERE conversation_id=?", (conversation_id,))

    # Attachments -----------------------------------------------------------

    def add_attachment(self, **fields: Any) -> dict:
        aid = fields.get("id") or new_id()
        self._exec(
            "INSERT INTO attachments (id, conversation_id, kind, name, mime, size, sha256, path, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                aid,
                fields.get("conversation_id"),
                fields["kind"],
                fields["name"],
                fields["mime"],
                int(fields["size"]),
                fields.get("sha256", ""),
                fields["path"],
                time.time(),
            ),
        )
        return self.get_attachment(aid)

    def get_attachment(self, aid: str) -> dict | None:
        return self._one("SELECT * FROM attachments WHERE id=?", (aid,))

    def get_attachments(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = {r["id"]: r for r in self._all(f"SELECT * FROM attachments WHERE id IN ({marks})", tuple(ids))}
        return [rows[i] for i in ids if i in rows]

    def assign_attachments(self, ids: list[str], conversation_id: str) -> None:
        for aid in ids:
            self._exec("UPDATE attachments SET conversation_id=? WHERE id=? AND conversation_id IS NULL", (conversation_id, aid))

    def delete_attachment(self, aid: str) -> str | None:
        row = self.get_attachment(aid)
        if not row:
            return None
        self._exec("DELETE FROM attachments WHERE id=?", (aid,))
        return row["path"]

    def orphan_uploads(self, older_than: float) -> list[dict]:
        return self._all(
            "SELECT * FROM attachments WHERE conversation_id IS NULL AND created_at<?", (older_than,)
        )
