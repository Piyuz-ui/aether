"""Monitoring: tokens, context window use and timing for every turn.

The numbers come straight from Ollama's response (prompt_eval_count etc.), so they're exact.
Every turn is also appended to data/metrics.jsonl to keep a history across sessions.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

NS = 1e9  # Ollama reports durations in nanoseconds


def fmt_seconds(s: float) -> str:
    if s < 60:
        return f"{s:.1f} s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m} m {sec:02d} s"


@dataclass
class CallStats:
    """One request to the model."""
    prompt_tokens: int = 0    # tokens sent in (= how much of the context window is filled)
    cached_tokens: int = 0    # of those, reused from Ollama's cache (fast)
    output_tokens: int = 0    # tokens generated
    total_s: float = 0.0      # whole request
    load_s: float = 0.0       # loading the model into memory
    prompt_s: float = 0.0     # reading the prompt
    gen_s: float = 0.0        # generating the reply

    @classmethod
    def from_ollama(cls, r: dict) -> "CallStats":
        return cls(prompt_tokens=r.get("prompt_eval_count", 0),
                   cached_tokens=r.get("prompt_eval_cached_count", 0),
                   output_tokens=r.get("eval_count", 0),
                   total_s=r.get("total_duration", 0) / NS,
                   load_s=r.get("load_duration", 0) / NS,
                   prompt_s=r.get("prompt_eval_duration", 0) / NS,
                   gen_s=r.get("eval_duration", 0) / NS)


@dataclass
class TurnStats:
    """Everything that happened while answering one user message."""
    model: str
    context_limit: int
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    wall_s: float = 0.0                       # what the user waited, end to end
    calls: list[CallStats] = field(default_factory=list)
    tools: list[tuple[str, float]] = field(default_factory=list)  # (tool name, seconds)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    @property
    def context_used(self) -> int:
        """Peak context window use: the biggest single request (prompt + its reply)."""
        return max((c.prompt_tokens + c.output_tokens for c in self.calls), default=0)

    @property
    def context_pct(self) -> float:
        return 100 * self.context_used / self.context_limit if self.context_limit else 0.0

    @property
    def llm_s(self) -> float:
        return sum(c.total_s for c in self.calls)

    @property
    def tool_s(self) -> float:
        return sum(s for _, s in self.tools)

    @property
    def tokens_per_s(self) -> float:
        gen = sum(c.gen_s for c in self.calls)
        return self.output_tokens / gen if gen else 0.0

    def summary(self) -> str:
        parts = [f"{fmt_seconds(self.wall_s)}",
                 f"{self.prompt_tokens:,} in / {self.output_tokens:,} out tokens",
                 f"context {self.context_used:,}/{self.context_limit:,} ({self.context_pct:.0f}%)",
                 f"{self.tokens_per_s:.1f} tok/s",
                 f"{len(self.calls)} model call{'s' if len(self.calls) != 1 else ''}"]
        if self.tools:
            parts.append(f"{len(self.tools)} tool{'s' if len(self.tools) != 1 else ''} "
                         f"({fmt_seconds(self.tool_s)})")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(prompt_tokens=self.prompt_tokens, output_tokens=self.output_tokens,
                 context_used=self.context_used, context_pct=round(self.context_pct, 1),
                 llm_s=round(self.llm_s, 2), tool_s=round(self.tool_s, 2),
                 tokens_per_s=round(self.tokens_per_s, 1))
        return d


class Monitor:
    """Keeps this session's turns and logs each one to data/metrics.jsonl."""

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or DATA_DIR / "metrics.jsonl"
        self.turns: list[TurnStats] = []

    def record(self, turn: TurnStats) -> None:
        self.turns.append(turn)
        try:
            with self.log_path.open("a") as f:
                f.write(json.dumps(turn.to_dict()) + "\n")
        except OSError:
            pass  # monitoring must never break the agent

    def session(self) -> dict:
        t = self.turns
        n = len(t)
        return {"turns": n,
                "prompt_tokens": sum(x.prompt_tokens for x in t),
                "output_tokens": sum(x.output_tokens for x in t),
                "total_tokens": sum(x.total_tokens for x in t),
                "total_s": sum(x.wall_s for x in t),
                "avg_s": sum(x.wall_s for x in t) / n if n else 0.0,
                "peak_context_pct": max((x.context_pct for x in t), default=0.0),
                "tool_calls": sum(len(x.tools) for x in t)}

    def history(self, limit: int = 1000) -> list[dict]:
        """All logged turns, across sessions (newest last)."""
        if not self.log_path.exists():
            return []
        rows = []
        for line in self.log_path.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows
