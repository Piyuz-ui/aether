"""Usage and spend for cloud models: every request's tokens, how full the context was, and what
it cost, plus a monthly budget.

Prices are USD per million tokens. Built in: Claude (Anthropic's published prices) and the free
tiers ($0). Providers whose prices change often (OpenAI, DeepSeek, xAI, Hugging Face credits)
aren't guessed: you enter them once in the Usage tab, and they apply to past usage too, because
cost is always computed from the stored token counts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .llm.cloud import PROVIDERS, split_model
from .memory.store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    context_limit INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS model_prices (
    model TEXT PRIMARY KEY,
    input_per_m REAL NOT NULL,
    output_per_m REAL NOT NULL,
    cached_per_m REAL
);
"""


@dataclass(frozen=True, slots=True)
class Price:
    input_per_m: float
    output_per_m: float
    cached_per_m: float | None = None   # reads from the prompt cache (None: same as input)
    source: str = "official"            # "official" · "yours" · "free tier"
    # Claude Haiku 5.5 charges more above 100K prompt tokens: (threshold, input, output)
    long_prompt: tuple[int, float, float] | None = None


# Anthropic's published prices, USD per 1M tokens (input, output, cache read).
CLAUDE_PRICES: dict[str, Price] = {
    "claude-fable-5-2": Price(8.0, 40.0, 0.20),
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-fable-5": Price(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.10),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-5-5": Price(0.10, 0.50, long_prompt=(100_000, 0.50, 2.50)),
    "claude-haiku-4-5": Price(1.0, 5.0),
}
# Free tiers cost nothing (OpenRouter: only its free ":free" models are offered).
FREE_TIER = {"gemini", "groq", "openrouter", "mistral", "cerebras", "github"}


def cost_usd(price: Price | None, prompt: int, cached: int, output: int) -> float | None:
    if price is None:
        return None
    inp, out = price.input_per_m, price.output_per_m
    if price.long_prompt and prompt > price.long_prompt[0]:
        inp, out = price.long_prompt[1], price.long_prompt[2]
    cached = min(cached, prompt)
    cache_rate = price.cached_per_m if price.cached_per_m is not None else inp * 0.1 if cached else inp
    return ((prompt - cached) * inp + cached * cache_rate + output * out) / 1_000_000


class UsageBook:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    # ------------------------------------------------------------- prices
    def price_for(self, model: str) -> Price | None:
        """`model` is the full name, e.g. "claude:claude-opus-5-5"."""
        row = self.store.one("SELECT * FROM model_prices WHERE model = ?", (model,))
        if row:
            return Price(row["input_per_m"], row["output_per_m"], row["cached_per_m"], "yours")
        provider, bare = split_model(model)
        if provider == "claude" and bare in CLAUDE_PRICES:
            return CLAUDE_PRICES[bare]
        if provider in FREE_TIER:
            return Price(0.0, 0.0, 0.0, "free tier")
        return None

    def set_price(self, model: str, input_per_m: float, output_per_m: float,
                  cached_per_m: float | None = None) -> None:
        self.store.execute(
            "INSERT INTO model_prices VALUES (?, ?, ?, ?) ON CONFLICT(model) DO UPDATE SET "
            "input_per_m = excluded.input_per_m, output_per_m = excluded.output_per_m, "
            "cached_per_m = excluded.cached_per_m", (model, input_per_m, output_per_m, cached_per_m))

    def clear_price(self, model: str) -> bool:
        return self.store.execute("DELETE FROM model_prices WHERE model = ?", (model,)).rowcount > 0

    # ------------------------------------------------------------- budget
    def budget(self) -> float | None:
        v = self.store.get_meta("monthly_budget_usd")
        return float(v) if v else None

    def set_budget(self, usd: float | None) -> None:
        self.store.set_meta("monthly_budget_usd", "" if not usd else f"{usd:.2f}")

    # ------------------------------------------------------------ record
    def record(self, model: str, stats: Any, context_limit: int, ts: float | None = None) -> float | None:
        """Save one finished cloud request. Returns its cost (None if no price is known)."""
        provider, _ = split_model(model)
        self.store.execute(
            "INSERT INTO usage (ts, provider, model, prompt_tokens, cached_tokens, output_tokens, "
            "context_limit, seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts or time.time(), provider or "local", model, stats.prompt_tokens, stats.cached_tokens,
             stats.output_tokens, context_limit, stats.total_s))
        return cost_usd(self.price_for(model), stats.prompt_tokens, stats.cached_tokens, stats.output_tokens)

    # ----------------------------------------------------------- summary
    def summary(self, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(month_start, day_start - timedelta(days=days - 1)).timestamp()
        rows = self.store.query("SELECT * FROM usage WHERE ts >= ? ORDER BY ts", (since,))

        prices: dict[str, Price | None] = {}
        models: dict[str, dict[str, Any]] = {}
        chart_days = [(day_start - timedelta(days=i)).date() for i in range(13, -1, -1)]
        daily = {d: {"date": d.isoformat(), "cost_usd": 0.0, "tokens": 0, "requests": 0} for d in chart_days}
        month = {"cost_usd": 0.0, "requests": 0, "prompt_tokens": 0, "output_tokens": 0, "unpriced_requests": 0}
        today = {"cost_usd": 0.0, "requests": 0, "tokens": 0}

        for r in rows:
            m = r["model"]
            if m not in prices:
                prices[m] = self.price_for(m)
            c = cost_usd(prices[m], r["prompt_tokens"], r["cached_tokens"], r["output_tokens"])
            when = datetime.fromtimestamp(r["ts"])
            tokens = r["prompt_tokens"] + r["output_tokens"]
            if when >= month_start:
                month["requests"] += 1
                month["prompt_tokens"] += r["prompt_tokens"]
                month["output_tokens"] += r["output_tokens"]
                month["cost_usd"] += c or 0.0
                month["unpriced_requests"] += c is None
                e = models.setdefault(m, {
                    "model": m, "provider": r["provider"], "requests": 0, "prompt_tokens": 0,
                    "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "priced": True,
                    "max_context_pct": 0.0, "context_pcts": [], "seconds": 0.0, "last_used": 0.0})
                e["requests"] += 1
                e["prompt_tokens"] += r["prompt_tokens"]
                e["cached_tokens"] += r["cached_tokens"]
                e["output_tokens"] += r["output_tokens"]
                e["cost_usd"] += c or 0.0
                e["priced"] = e["priced"] and c is not None
                e["seconds"] += r["seconds"]
                e["last_used"] = max(e["last_used"], r["ts"])
                if r["context_limit"]:
                    pct = 100 * r["prompt_tokens"] / r["context_limit"]
                    e["context_pcts"].append(pct)
                    e["max_context_pct"] = max(e["max_context_pct"], pct)
            if when >= day_start:
                today["requests"] += 1
                today["tokens"] += tokens
                today["cost_usd"] += c or 0.0
            if when.date() in daily:
                d = daily[when.date()]
                d["cost_usd"] += c or 0.0
                d["tokens"] += tokens
                d["requests"] += 1

        by_model = []
        for e in sorted(models.values(), key=lambda e: (-e["cost_usd"], -e["requests"])):
            pcts = e.pop("context_pcts")
            p = prices.get(e["model"])
            label = PROVIDERS[e["provider"]].label if e["provider"] in PROVIDERS else e["provider"]
            by_model.append({
                **e, "provider_label": label,
                "name": split_model(e["model"])[1],
                "avg_context_pct": round(sum(pcts) / len(pcts), 1) if pcts else 0.0,
                "max_context_pct": round(e["max_context_pct"], 1),
                "avg_seconds": round(e["seconds"] / e["requests"], 1),
                "cost_usd": round(e["cost_usd"], 6) if e["priced"] else None,
                "price": None if p is None else {"input_per_m": p.input_per_m, "output_per_m": p.output_per_m,
                                                 "cached_per_m": p.cached_per_m, "source": p.source},
            })
        budget = self.budget()
        spent = round(month["cost_usd"], 6)
        return {
            "month": {**month, "cost_usd": spent, "label": now.strftime("%B %Y")},
            "today": {**today, "cost_usd": round(today["cost_usd"], 6)},
            "budget": {"monthly_usd": budget, "used_pct": round(100 * spent / budget, 1) if budget else None,
                       "state": None if not budget else "over" if spent >= budget
                       else "near" if spent >= 0.8 * budget else "ok"},
            "by_model": by_model,
            "daily": [{**d, "cost_usd": round(d["cost_usd"], 6)} for d in daily.values()],
        }
