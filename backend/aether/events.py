"""Typed lifecycle events. The agent loop yields these; every interface renders them."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    STATUS = "status"                        # data: state (AgentState)
    TOKEN = "token"                          # data: text, channel ("content" | "thinking")
    THOUGHT = "thought"                      # data: text
    TOOL_START = "tool_start"                # data: call_id, name, args, risk, icon
    APPROVAL_REQUIRED = "approval_required"  # data: approval_id, call_id, name, args, summary
    APPROVAL_RESOLVED = "approval_resolved"  # data: approval_id, approved, reason
    TOOL_RESULT = "tool_result"              # data: call_id, name, ok, output, duration_s
    DONE = "done"                            # data: answer, stats
    ERROR = "error"                          # data: message, hint


class AgentState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    USING_TOOL = "using_tool"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass(slots=True)
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    step: int = 0
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "step": self.step, "ts": round(self.ts, 3),
                "data": self.data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)
