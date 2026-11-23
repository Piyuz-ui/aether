"""Usage and spend for cloud models: recording, prices, cost, context, budget, free-tier limits."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from aether.config import Settings
from aether.llm.ollama import OllamaClient
from aether.llm.router import ModelRouter
from aether.memory.store import Store
from aether.metrics import CallStats, RunStats
from aether.usage import CLAUDE_PRICES, UsageBook, cost_usd

M = 1_000_000


@pytest.fixture()
def book() -> UsageBook:
    return UsageBook(Store(":memory:"))


def stats(prompt: int, out: int, cached: int = 0, secs: float = 1.0) -> CallStats:
    return CallStats(prompt_tokens=prompt, cached_tokens=cached, output_tokens=out, total_s=secs)


def test_cost_maths() -> None:
    opus = CLAUDE_PRICES["claude-opus-5-5"]                       # $4 in / $20 out / $0.20 cache read
    assert cost_usd(opus, M, 0, M // 2) == pytest.approx(4 + 10)
    assert cost_usd(opus, M, M // 2, 0) == pytest.approx(0.5 * 4 + 0.5 * 0.20)
    haiku = CLAUDE_PRICES["claude-haiku-5-5"]                      # dearer above 100K prompt tokens
    assert cost_usd(haiku, 50_000, 0, 0) == pytest.approx(50_000 * 0.10 / M)
    assert cost_usd(haiku, 200_000, 0, 1000) == pytest.approx((200_000 * 0.50 + 1000 * 2.50) / M)
    assert cost_usd(None, 100, 0, 100) is None


def test_prices_official_free_unknown_and_yours(book: UsageBook) -> None:
    assert book.price_for("claude:claude-opus-5-5").source == "official"
    assert book.price_for("groq:llama-3.3-70b-versatile").input_per_m == 0
    assert book.price_for("openai:gpt-5") is None                 # never guessed
    book.set_price("openai:gpt-5", 1.25, 10.0)
    assert book.price_for("openai:gpt-5").source == "yours"
    book.set_price("claude:claude-opus-5-5", 3.0, 15.0)            # yours overrides the built-in
    assert book.price_for("claude:claude-opus-5-5").input_per_m == 3.0
    assert book.clear_price("claude:claude-opus-5-5") and book.price_for("claude:claude-opus-5-5").input_per_m == 4.0


def test_summary_month_today_models_context_and_chart(book: UsageBook) -> None:
    now = datetime(2026, 11, 23, 15, 0)
    last_month = datetime(2026, 10, 30, 12, 0).timestamp()
    yesterday = (now - timedelta(days=1)).timestamp()
    book.record("claude:claude-opus-5-5", stats(100_000, 2_000), 1_000_000, ts=last_month)   # not this month
    book.record("claude:claude-opus-5-5", stats(200_000, 4_000), 1_000_000, ts=yesterday)
    book.record("claude:claude-opus-5-5", stats(500_000, 1_000), 1_000_000, ts=now.timestamp())
    book.record("groq:llama-3.3-70b-versatile", stats(30_000, 500), 131_072, ts=now.timestamp())
    book.record("openai:gpt-5", stats(10_000, 1_000), 400_000, ts=now.timestamp())             # no price yet

    s = book.summary(now=now)
    opus_cost = (200_000 * 4 + 4_000 * 20 + 500_000 * 4 + 1_000 * 20) / M
    assert s["month"]["label"] == "November 2026" and s["month"]["requests"] == 4
    assert s["month"]["cost_usd"] == pytest.approx(opus_cost) and s["month"]["unpriced_requests"] == 1
    assert s["today"]["requests"] == 3
    by = {m["model"]: m for m in s["by_model"]}
    opus = by["claude:claude-opus-5-5"]
    assert opus["requests"] == 2 and opus["max_context_pct"] == 50.0 and opus["avg_context_pct"] == 35.0
    assert opus["price"]["source"] == "official" and opus["provider_label"] == "Anthropic Claude"
    assert by["groq:llama-3.3-70b-versatile"]["cost_usd"] == 0.0
    assert by["openai:gpt-5"]["cost_usd"] is None and by["openai:gpt-5"]["price"] is None
    assert len(s["daily"]) == 14 and s["daily"][-1]["date"] == "2026-11-23" and s["daily"][-1]["requests"] == 3

    book.set_price("openai:gpt-5", 1.25, 10.0)                     # a price applies to past usage too
    after = book.summary(now=now)
    assert {m["model"]: m for m in after["by_model"]}["openai:gpt-5"]["cost_usd"] == pytest.approx(
        (10_000 * 1.25 + 1_000 * 10) / M)


def test_budget_states(book: UsageBook) -> None:
    now = datetime(2026, 11, 23, 15, 0)
    assert book.summary(now=now)["budget"] == {"monthly_usd": None, "used_pct": None, "state": None}
    book.record("claude:claude-opus-5-5", stats(1_000_000, 0), 1_000_000, ts=now.timestamp())   # $4.00
    book.set_budget(10)
    assert book.summary(now=now)["budget"]["state"] == "ok"
    book.set_budget(4.5)
    assert book.summary(now=now)["budget"]["state"] == "near"
    book.set_budget(3)
    b = book.summary(now=now)["budget"]
    assert b["state"] == "over" and b["used_pct"] == pytest.approx(133.3, abs=0.1)
    book.set_budget(None)
    assert book.budget() is None


def test_run_stats_add_up_cost() -> None:
    r = RunStats(model="m", context_limit=1000)
    r.calls = [CallStats(cost_usd=0.01), CallStats(cost_usd=None), CallStats(cost_usd=0.02)]
    assert r.to_dict()["cost_usd"] == pytest.approx(0.03)
    assert RunStats(model="m", context_limit=1).to_dict()["cost_usd"] is None


def sse(*chunks: dict[str, Any]) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


async def test_router_records_cloud_requests_and_free_tier_limits(book: UsageBook) -> None:
    def groq(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(
            {"choices": [{"index": 0, "delta": {"content": "hi"}}]},
            {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 40}}),
            headers={"content-type": "text/event-stream",
                     "x-ratelimit-limit-requests": "14400", "x-ratelimit-remaining-requests": "14370",
                     "x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "4760",
                     "x-ratelimit-reset-requests": "2m59.56s"})
    r = ModelRouter(OllamaClient("http://mac.test"), keys={"groq": "k"}, transport=httpx.MockTransport(groq))
    r.usage_sink = book.record
    out = [d async for d in r.chat_stream(model="groq:llama-3.3-70b-versatile",
                                          messages=[{"role": "user", "content": "x"}])]
    assert out[-1].stats.cost_usd == 0.0                          # free tier
    rows = book.store.query("SELECT * FROM usage")
    assert len(rows) == 1 and rows[0]["prompt_tokens"] == 1200 and rows[0]["provider"] == "groq"
    lim = r.rate_limits()["groq"]["windows"]
    assert lim["requests"] == {"limit": 14400, "remaining": 14370, "reset": "2m59.56s"}
    assert lim["tokens"]["remaining"] == 4760


def test_api_usage_prices_and_budget() -> None:
    from fastapi.testclient import TestClient

    import api
    from .conftest import FakeLLM

    app = api.create_app(Settings(), llm=FakeLLM([]), db_path=":memory:")
    with TestClient(app) as client:
        book: UsageBook = app.state.ctx.memory.usage
        book.record("openai:gpt-5", stats(10_000, 1_000), 400_000)
        s = client.get("/api/usage").json()
        assert s["month"]["unpriced_requests"] == 1 and s["limits"] == []
        assert client.put("/api/usage/prices", json={"model": "openai:gpt-5", "input_per_m": 1.25,
                                                      "output_per_m": 10}).status_code == 200
        assert client.put("/api/usage/prices", json={"model": "qwen2.5:7b", "input_per_m": 1,
                                                      "output_per_m": 1}).status_code == 422
        assert client.get("/api/usage").json()["month"]["cost_usd"] == pytest.approx(0.0225)
        assert client.put("/api/usage/budget", json={"monthly_usd": 5}).json() == {"monthly_usd": 5.0}
        assert client.get("/api/usage").json()["budget"]["state"] == "ok"
        assert client.delete("/api/usage/prices/openai:gpt-5").json()["removed"] is True
