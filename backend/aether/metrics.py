"""Usage monitoring: exact token counts, context window use and timing from Ollama."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NS = 1e9  # Ollama reports durations in nanoseconds


def fmt_seconds(s: float) -> str:
    if s < 60:
        return f"{s:.1f} s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m} m {sec:02d} s"


@dataclass(slots=True)
class CallStats:
    """One model request."""
    prompt_tokens: int = 0   # everything sent in = how full the context window is
    cached_tokens: int = 0   # of those, reused from Ollama's KV cache
    output_tokens: int = 0
    total_s: float = 0.0
    load_s: float = 0.0
    prompt_s: float = 0.0
    gen_s: float = 0.0
    cost_usd: float | None = None  # cloud models with a known price

    @classmethod
    def from_ollama(cls, r: dict[str, Any]) -> "CallStats":
        return cls(prompt_tokens=r.get("prompt_eval_count", 0) or 0,
                   cached_tokens=r.get("prompt_eval_cached_count", 0) or 0,
                   output_tokens=r.get("eval_count", 0) or 0,
                   total_s=(r.get("total_duration", 0) or 0) / NS,
                   load_s=(r.get("load_duration", 0) or 0) / NS,
                   prompt_s=(r.get("prompt_eval_duration", 0) or 0) / NS,
                   gen_s=(r.get("eval_duration", 0) or 0) / NS)


@dataclass(slots=True)
class RunStats:
    """Everything spent answering one goal."""
    model: str
    context_limit: int
    wall_s: float = 0.0
    calls: list[CallStats] = field(default_factory=list)
    tool_seconds: list[tuple[str, float]] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def context_used(self) -> int:
        return max((c.prompt_tokens + c.output_tokens for c in self.calls), default=0)

    @property
    def context_pct(self) -> float:
        return 100 * self.context_used / self.context_limit if self.context_limit else 0.0

    @property
    def tokens_per_s(self) -> float:
        gen = sum(c.gen_s for c in self.calls)
        return self.output_tokens / gen if gen else 0.0

    def summary(self) -> str:
        n = len(self.calls)
        parts = [fmt_seconds(self.wall_s),
                 f"{self.prompt_tokens:,} in / {self.output_tokens:,} out tokens",
                 f"context {self.context_used:,}/{self.context_limit:,} ({self.context_pct:.0f}%)",
                 f"{self.tokens_per_s:.1f} tok/s",
                 f"{n} model call{'s' * (n != 1)}"]
        if self.tool_seconds:
            k = len(self.tool_seconds)
            parts.append(f"{k} tool{'s' * (k != 1)}")
        return " · ".join(parts)

    @property
    def cost_usd(self) -> float | None:
        costs = [c.cost_usd for c in self.calls if c.cost_usd is not None]
        return round(sum(costs), 6) if costs else None

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "wall_s": round(self.wall_s, 2), "cost_usd": self.cost_usd,
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
                "context_used": self.context_used, "context_limit": self.context_limit,
                "context_pct": round(self.context_pct, 1),
                "tokens_per_s": round(self.tokens_per_s, 1), "model_calls": len(self.calls),
                "tools": [{"name": n, "seconds": round(s, 2)} for n, s in self.tool_seconds],
                "summary": self.summary()}
