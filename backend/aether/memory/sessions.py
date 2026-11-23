"""Short-term memory that survives restarts: a Session whose turns are saved to SQLite."""
from __future__ import annotations

from typing import Any

from ..engine.session import Session
from .store import Store


def _title_from(text: str) -> str:
    title = " ".join(text.split())
    return title if len(title) <= 60 else title[:57].rsplit(" ", 1)[0] + "…"


class PersistentSession(Session):
    def __init__(self, store: Store, session_id: str | None = None):
        super().__init__() if session_id is None else super().__init__(id=session_id)
        self.store = store
        store.create_session(self.id)
        self.turns = store.load_turns(self.id)

    def add_turn(self, messages: list[dict[str, Any]]) -> None:
        first = not self.turns
        super().add_turn(messages)
        self.store.append_turn(self.id, len(self.turns), messages)
        if first:
            user = next((m["content"] for m in messages if m["role"] == "user"), "")
            if user:
                self.store.rename_session(self.id, _title_from(user), source="first")

    def clear(self) -> None:
        super().clear()
        self.store.execute("DELETE FROM messages WHERE session_id = ?", (self.id,))
