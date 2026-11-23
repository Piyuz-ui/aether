"""One SQLite file holds everything AETHER remembers.

    sessions / messages   short-term memory: every conversation, step by step
    facts                 long-term memory: what the agent knows about the user (+ embedding)
    tasks / timetable     deadlines, reminders, exams and weekly classes
    documents / chunks    the knowledge vault: indexed PDFs and notes (+ embeddings, FTS5)

Embeddings are stored as float32 BLOBs. Similarity search uses sqlite-vec's
vec_distance_cosine() inside SQL, with an exact NumPy fallback if the extension can't load.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_name TEXT,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, turn, id);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source TEXT NOT NULL DEFAULT 'user',
    embedding BLOB,
    created REAL NOT NULL,
    updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'task',
    due TEXT,
    notes TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(done, due);

CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course TEXT NOT NULL,
    weekday INTEGER NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    pages INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


def to_blob(vector: Sequence[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class Store:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: Path | str):
        self.path = Path(path) if path != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.has_vec = self._load_sqlite_vec()
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                          (str(SCHEMA_VERSION),))
        self.conn.commit()

    def _load_sqlite_vec(self) -> bool:
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            return True
        except Exception:
            return False  # fall back to NumPy similarity

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------------------------------------------------------------- basics
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                     "excluded.value", (key, value))

    # ---------------------------------------------------- vector similarity
    def nearest(self, table: str, query_vec: Sequence[float], k: int,
                where: str = "", params: Sequence[Any] = ()) -> list[tuple[int, float]]:
        """(row id, cosine similarity) of the k most similar rows with an embedding."""
        if table not in {"chunks", "facts"}:
            raise ValueError("unsupported table")
        clause = f"embedding IS NOT NULL{' AND ' + where if where else ''}"
        q = to_blob(query_vec)
        if self.has_vec:
            rows = self.query(
                f"SELECT id, 1 - vec_distance_cosine(embedding, ?) AS sim FROM {table} "
                f"WHERE {clause} AND length(embedding) = length(?) ORDER BY sim DESC LIMIT ?",
                (q, *params, q, k))
            return [(r["id"], float(r["sim"])) for r in rows]
        rows = self.query(f"SELECT id, embedding FROM {table} WHERE {clause}", tuple(params))
        if not rows:
            return []
        qv = np.asarray(query_vec, dtype=np.float32)
        ids, mats = [], []
        for r in rows:
            v = from_blob(r["embedding"])
            if v.shape == qv.shape:
                ids.append(r["id"])
                mats.append(v)
        if not mats:
            return []
        m = np.vstack(mats)
        sims = m @ qv / (np.linalg.norm(m, axis=1) * (np.linalg.norm(qv) or 1.0) + 1e-9)
        order = np.argsort(-sims)[:k]
        return [(ids[i], float(sims[i])) for i in order]

    # ------------------------------------------------------------- sessions
    def create_session(self, session_id: str, title: str = "New chat") -> None:
        now = time.time()
        self.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, ?)",
                     (session_id, title, now, now))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            "SELECT s.*, (SELECT COUNT(DISTINCT turn) FROM messages m WHERE m.session_id = s.id)"
            " AS turns FROM sessions s ORDER BY updated DESC LIMIT ?", (limit,))

    def rename_session(self, session_id: str, title: str) -> bool:
        return self.execute("UPDATE sessions SET title = ? WHERE id = ?",
                            (title, session_id)).rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        return self.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    def append_turn(self, session_id: str, turn: int, messages: list[dict[str, Any]]) -> None:
        now = time.time()
        rows = [(session_id, turn, m["role"], str(m.get("content") or ""),
                 json.dumps(m["tool_calls"]) if m.get("tool_calls") else None,
                 m.get("tool_name"), now) for m in messages]
        with self._lock:
            self.conn.executemany(
                "INSERT INTO messages (session_id, turn, role, content, tool_calls, tool_name, "
                "created) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self.conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session_id))
            self.conn.commit()

    def load_turns(self, session_id: str) -> list[list[dict[str, Any]]]:
        rows = self.query("SELECT * FROM messages WHERE session_id = ? ORDER BY turn, id",
                          (session_id,))
        turns: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            msg: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                msg["tool_calls"] = json.loads(r["tool_calls"])
            if r["tool_name"]:
                msg["tool_name"] = r["tool_name"]
            turns.setdefault(r["turn"], []).append(msg)
        return [turns[k] for k in sorted(turns)]
