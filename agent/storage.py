"""Local SQLite storage for memories, notes and tasks. Everything stays on this machine."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    embedding TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT,
    kind TEXT NOT NULL DEFAULT 'task',
    done INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="minutes")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # --------------------------------------------------------------- memories
    def add_memory(self, text: str, embedding: list[float] | None) -> int:
        cur = self.execute(
            "INSERT INTO memories (text, embedding, created) VALUES (?, ?, ?)",
            (text, json.dumps(embedding) if embedding else None, now_iso()))
        return cur.lastrowid

    def all_memories(self) -> list[dict]:
        rows = self.query("SELECT * FROM memories ORDER BY id")
        for r in rows:
            r["embedding"] = json.loads(r["embedding"]) if r["embedding"] else None
        return rows

    def delete_memory(self, memory_id: int) -> bool:
        return self.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    # ------------------------------------------------------------------ notes
    def add_note(self, title: str, body: str) -> int:
        return self.execute("INSERT INTO notes (title, body, created) VALUES (?, ?, ?)",
                            (title, body, now_iso())).lastrowid

    def search_notes(self, query: str) -> list[dict]:
        like = f"%{query}%"
        return self.query(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? ORDER BY id DESC LIMIT 20",
            (like, like))

    def recent_notes(self, limit: int = 20) -> list[dict]:
        return self.query("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ tasks
    def add_task(self, title: str, due: str | None, kind: str) -> int:
        return self.execute("INSERT INTO tasks (title, due, kind, created) VALUES (?, ?, ?, ?)",
                            (title, due or None, kind, now_iso())).lastrowid

    def list_tasks(self, include_done: bool = False) -> list[dict]:
        where = "" if include_done else "WHERE done = 0"
        # Tasks with a due date first (soonest first), then undated ones.
        return self.query(f"SELECT * FROM tasks {where} "
                          "ORDER BY due IS NULL, due, id")

    def set_task_done(self, task_id: int, done: bool = True) -> bool:
        return self.execute("UPDATE tasks SET done = ? WHERE id = ?",
                            (int(done), task_id)).rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        return self.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(DATA_DIR / "agent.db")
    return _store
