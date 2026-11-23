"""Conversation history (short-term memory) for one chat session.

Phase 1 keeps it in memory. Phase 2 persists the same structure to SQLite.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


MODEL_KEYS = ("role", "content", "tool_calls", "tool_name")


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    # Each turn is the list of messages one goal produced: user, assistant/tool steps, answer.
    turns: list[list[dict[str, Any]]] = field(default_factory=list)

    def add_turn(self, messages: list[dict[str, Any]]) -> None:
        self.turns.append(messages)

    def history(self, max_chars: int, max_turns: int = 20) -> list[dict[str, Any]]:
        """The most recent whole turns that fit in the character budget (oldest dropped first)."""
        kept: list[list[dict[str, Any]]] = []
        used = 0
        for turn in reversed(self.turns[-max_turns:]):
            size = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))
                       for m in turn)
            if kept and used + size > max_chars:
                break
            kept.append(turn)
            used += size
        # Only what the model should see: no UI metadata, no images from earlier turns.
        return [{k: v for k, v in m.items() if k in MODEL_KEYS}
                for turn in reversed(kept) for m in turn]

    def clear(self) -> None:
        self.turns.clear()
