"""Cloud brains: message conversion, streaming, errors and routing, all offline.

The OpenAI-compatible providers (Gemini, Groq, OpenRouter) run against a mocked HTTP
transport, and Claude against a fake SDK client, so no keys or network are needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest

from aether.config import Settings
from aether.engine import AetherAgent
from aether.engine.session import Session
from aether.events import EventType
from aether.llm.cloud import ClaudeProvider, OpenAICompatProvider, split_model
from aether.llm.ollama import OllamaClient, OllamaError
from aether.llm.router import ModelRouter, save_env_value
from aether.tools import REGISTRY

from .conftest import FakeLLM


# ------------------------------------------------------------------ helpers
def sse(*chunks: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def delta(**d: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


class Recorder:
    """A mock HTTP server: replies in order and keeps every request."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def json(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


def compat(recorder: Recorder, provider: str = "groq") -> OpenAICompatProvider:
    return OpenAICompatProvider(provider, "https://api.example.test/v1", "test-key",
                                transport=httpx.MockTransport(recorder))


async def collect(stream: Any) -> list[Any]:
    return [d async for d in stream]


# --------------------------------------------------------------- naming
def test_split_model_only_claims_known_prefixes() -> None:
    assert split_model("gemini:gemini-2.5-flash") == ("gemini", "gemini-2.5-flash")
    assert split_model("openrouter:meta-llama/llama-3.3-70b-instruct:free") == (
        "openrouter", "meta-llama/llama-3.3-70b-instruct:free")
    assert split_model("qwen2.5:7b") == (None, "qwen2.5:7b")   # a local Ollama tag
    assert split_model("claude:") == (None, "claude:")


# ------------------------------------------------------- OpenAI-compatible
async def test_compat_streams_text_tool_calls_and_usage() -> None:
    rec = Recorder(httpx.Response(200, content=sse(
        delta(content="Let me "), delta(content="check."),
        delta(tool_calls=[{"index": 0, "id": "call_a", "type": "function",
                           "function": {"name": "calculator", "arguments": '{"expr'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'ession": "2+2"}'}}]),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 9}})))
    p = compat(rec)
    out = await collect(p.chat_stream(model="llama-3.3-70b-versatile",
                                      messages=[{"role": "user", "content": "2+2?"}],
                                      tools=REGISTRY.schemas()[:1]))
    assert "".join(d.content for d in out) == "Let me check."
    done = out[-1]
    assert done.done and done.done_reason == "stop"
    assert done.tool_calls == [{"id": "call_a",
                                "function": {"name": "calculator", "arguments": {"expression": "2+2"}}}]
    assert (done.stats.prompt_tokens, done.stats.output_tokens) == (120, 9)
    sent = rec.json(0)
    assert sent["model"] == "llama-3.3-70b-versatile" and sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True} and sent["tools"]
    assert rec.requests[0].headers["authorization"] == "Bearer test-key"


async def test_compat_pairs_tool_results_with_call_ids_and_echoes_signatures() -> None:
    p = compat(Recorder(), "gemini")
    # A Gemini call came back with a thought signature; it must be sent back untouched.
    raw = {"id": "call_g", "type": "function", "extra_content": {"google": {"thought_signature": "sig"}},
           "function": {"name": "web_search", "arguments": '{"query": "x"}'}}
    p._echo.put("call_g", raw)
    wire = p.to_wire([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "look", "images": ["iVBORw0KGgo="]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_g", "function": {"name": "web_search", "arguments": {"query": "x"}}},
            {"function": {"name": "calculator", "arguments": {"expression": "1"}}}]},
        {"role": "tool", "tool_name": "web_search", "content": "results"},
        {"role": "tool", "tool_name": "calculator", "content": "1"},
    ])
    assert wire[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    calls = wire[2]["tool_calls"]
    assert calls[0] is raw and wire[2]["content"] is None
    assert json.loads(calls[1]["function"]["arguments"]) == {"expression": "1"}
    assert wire[3]["tool_call_id"] == "call_g"
    assert wire[4]["tool_call_id"] == calls[1]["id"]


async def test_compat_drops_a_truncated_tool_call() -> None:
    rec = Recorder(httpx.Response(200, content=sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "create_pdf",
                                                              "arguments": '{"content": "abc'}}]),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})))
    out = await collect(compat(rec).chat_stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert out[-1].done_reason == "length" and out[-1].tool_calls == []


@pytest.mark.parametrize("status,needle", [(401, "rejected the API key"),
                                           (429, "rate limit or free quota"),
                                           (404, "doesn't offer")])
async def test_compat_errors_have_hints(status: int, needle: str) -> None:
    rec = Recorder(httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(OllamaError) as err:
        await collect(compat(rec).chat_stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert needle in err.value.message and err.value.hint


async def test_openrouter_lists_only_free_tool_models() -> None:
    listing = {"data": [
        {"id": "a/free-tools:free", "supported_parameters": ["tools"], "context_length": 64000,
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "b/no-tools:free", "supported_parameters": []},
        {"id": "c/paid", "supported_parameters": ["tools"]}]}
    p = compat(Recorder(httpx.Response(200, json=listing)), "openrouter")
    assert await p.list_models() == ["a/free-tools:free"]
    assert p.meta("a/free-tools:free").vision and p.meta("a/free-tools:free").context == 64000


# ------------------------------------------------------------------ Claude
class FakeStream:
    def __init__(self, events: list[Any], final: Any):
        self.events, self.final = events, final

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self) -> Any:
        return self.final


class FakeAnthropic:
    def __init__(self, *streams: FakeStream):
        self.streams = list(streams)
        self.calls: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        return self.streams.pop(0)

    async def close(self) -> None:
        return None


def text_event(t: str) -> Any:
    return NS(type="content_block_delta", delta=NS(type="text_delta", text=t))


def block(**kw: Any) -> Any:
    return NS(**kw)


def usage(i: int, o: int) -> Any:
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=0,
              cache_creation_input_tokens=0)


def test_claude_conversion() -> None:
    p = ClaudeProvider("k", client=FakeAnthropic())
    system, wire = p.to_wire([
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "what's this", "images": ["/9j/4AAQ"]},
        {"role": "assistant", "content": "checking", "tool_calls": [
            {"id": "toolu_1", "function": {"name": "web_search", "arguments": {"query": "q"}}},
            {"id": "toolu_2", "function": {"name": "calculator", "arguments": '{"expression": "1"}'}}]},
        {"role": "tool", "tool_name": "web_search", "content": "found"},
        {"role": "tool", "tool_name": "calculator", "content": "ERROR: bad"},
        {"role": "user", "content": "thanks"},
    ])
    assert system == "be helpful"
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    img = wire[0]["content"][0]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg"
    assert [b["type"] for b in wire[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert wire[1]["content"][2]["input"] == {"expression": "1"}
    results = wire[2]["content"]
    assert [b.get("tool_use_id") for b in results[:2]] == ["toolu_1", "toolu_2"]
    assert results[1]["is_error"] is True and results[2] == {"type": "text", "text": "thanks"}
    tools = p.to_tools(REGISTRY.schemas()[:1])
    assert set(tools[0]) == {"name", "description", "input_schema", "eager_input_streaming"}


async def test_claude_streams_and_echoes_its_own_blocks() -> None:
    thinking = block(type="thinking", thinking="", signature="sig")
    tool_use = block(type="tool_use", id="toolu_9", name="calculator", input={"expression": "6*7"})
    fake = FakeAnthropic(FakeStream([text_event("On it.")],
                                    NS(content=[thinking, block(type="text", text="On it."), tool_use],
                                       stop_reason="tool_use", usage=usage(300, 20))))
    p = ClaudeProvider("k", client=fake)
    out = await collect(p.chat_stream(model="claude-opus-5-5",
                                      messages=[{"role": "system", "content": "s"},
                                                {"role": "user", "content": "6*7"}],
                                      tools=REGISTRY.schemas()[:1]))
    assert out[0].content == "On it."
    assert out[-1].tool_calls == [{"id": "toolu_9",
                                   "function": {"name": "calculator", "arguments": {"expression": "6*7"}}}]
    assert out[-1].stats.prompt_tokens == 300
    sent = fake.calls[0]
    assert sent["system"] == "s" and "temperature" not in sent
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    # Next request: the assistant turn is sent back exactly as Claude produced it.
    _, wire = p.to_wire([{"role": "user", "content": "6*7"},
                         {"role": "assistant", "content": "On it.",
                          "tool_calls": [{"id": "toolu_9", "function": {"name": "python_calculator",
                                                                        "arguments": {}}}]},
                         {"role": "tool", "tool_name": "calculator", "content": "42"}])
    assert wire[1]["content"][0] is thinking and wire[2]["content"][0]["tool_use_id"] == "toolu_9"


async def test_claude_refusal_and_cutoff_never_run_tools() -> None:
    tool_use = block(type="tool_use", id="t", name="calculator", input={})
    fake = FakeAnthropic(FakeStream([], NS(content=[tool_use], stop_reason="refusal",
                                           usage=usage(1, 1))),
                         FakeStream([], NS(content=[tool_use], stop_reason="max_tokens",
                                           usage=usage(1, 1))))
    p = ClaudeProvider("k", client=fake)
    refused = await collect(p.chat_stream(model="claude-haiku-5-5",
                                          messages=[{"role": "user", "content": "x"}]))
    assert refused[0].content and refused[-1].tool_calls == []
    assert "fallbacks" not in fake.calls[0]  # only on models that support it
    cut = await collect(p.chat_stream(model="claude-opus-5-5", messages=[{"role": "user", "content": "x"}]))
    assert cut[-1].done_reason == "length" and cut[-1].tool_calls == []


# ------------------------------------------------------------------ router
def router(rec: Recorder | None = None, **keys: str) -> ModelRouter:
    ollama = OllamaClient("http://ollama.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})))
    return ModelRouter(ollama, keys=keys, transport=httpx.MockTransport(rec or Recorder()),
                       claude_client=FakeAnthropic())


async def test_router_routes_by_prefix_and_explains_missing_keys() -> None:
    rec = Recorder(httpx.Response(200, content=sse(delta(content="hi"))))
    r = router(rec, groq="g-key")
    out = await collect(r.chat_stream(model="groq:llama-3.3-70b-versatile",
                                      messages=[{"role": "user", "content": "x"}]))
    assert out[0].content == "hi" and rec.json(0)["model"] == "llama-3.3-70b-versatile"
    with pytest.raises(OllamaError) as err:
        r.chat_stream(model="gemini:gemini-2.5-flash", messages=[])
    assert "isn't set up" in err.value.message and "GEMINI_API_KEY" in err.value.hint
    assert r.context_limit("qwen2.5:7b") == 16384
    assert r.context_limit("claude:claude-opus-5-5") == 16384  # no Claude key: unknown
    assert "vision" in await router(claude="c").capabilities("claude:claude-opus-5-5")


async def test_router_resolves_cloud_default_or_falls_back_locally() -> None:
    assert await router(claude="c").resolve_model("claude:claude-opus-5-5", ["qwen2.5:7b"]) == (
        "claude:claude-opus-5-5", None)
    model, note = await router().resolve_model("claude:claude-opus-5-5", ["qwen2.5:7b"])
    assert model == "qwen2.5:7b" and "ANTHROPIC_API_KEY" in (note or "")


async def test_router_catalog_lists_configured_providers_only() -> None:
    rec = Recorder(httpx.Response(200, json={"data": [{"id": "llama-3.3-70b-versatile",
                                                       "context_window": 131072}]}))
    cat = {p["id"]: p for p in await router(rec, groq="g").catalog()}
    assert set(cat) == {"gemini", "groq", "openrouter", "claude"}
    assert cat["groq"]["configured"] and cat["groq"]["models"][0]["id"] == "groq:llama-3.3-70b-versatile"
    assert not cat["gemini"]["configured"] and cat["gemini"]["models"] == []
    assert cat["gemini"]["free"] and not cat["claude"]["free"]


async def test_engine_runs_a_cloud_tool_loop_end_to_end(tmp_path: Path) -> None:
    """A full agent turn on a cloud model: tool call → result paired by id → final answer."""
    rec = Recorder(
        httpx.Response(200, content=sse(
            delta(tool_calls=[{"index": 0, "id": "call_x", "function": {
                "name": "python_calculator", "arguments": '{"expression": "6*7"}'}}]),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})),
        httpx.Response(200, content=sse(delta(content="It's 42."))))
    agent = AetherAgent(Settings(), llm=router(rec, groq="g"))
    agent.set_model("groq:llama-3.3-70b-versatile")
    events = [e async for e in agent.run("what's 6*7", Session())]
    done = next((e for e in events if e.type is EventType.DONE), None)
    assert done is not None, [(e.type.value, e.data) for e in events]
    assert done.data["answer"] == "It's 42."
    second = rec.json(1)["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "call_x"
    assert second[-1] == {"role": "tool", "tool_call_id": "call_x", "name": "python_calculator",
                          "content": second[-1]["content"]} and "42" in second[-1]["content"]


# --------------------------------------------------------------------- api
def test_api_saves_checks_and_removes_keys(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import api

    rec = Recorder(httpx.Response(401, json={"error": {"message": "invalid api key"}}),
                   httpx.Response(200, json={"data": [{"id": "llama-3.3-70b-versatile"}]}),
                   httpx.Response(200, json={"data": [{"id": "llama-3.3-70b-versatile"}]}))
    app = api.create_app(Settings(), llm=router(rec), db_path=":memory:")
    env = tmp_path / ".env"
    env.write_text("AETHER_MODEL=qwen2.5:7b\n")
    app.state.env_file = env
    with TestClient(app) as client:
        bad = client.put("/api/providers/groq/key", json={"key": "gsk_wrong_key"})
        assert bad.status_code == 422 and "rejected" in bad.json()["detail"]["message"]
        assert "GROQ" not in env.read_text()
        ok = client.put("/api/providers/groq/key", json={"key": "gsk_good_key"})
        assert ok.status_code == 200 and ok.json()["configured"]
        assert env.read_text() == "AETHER_MODEL=qwen2.5:7b\nGROQ_API_KEY=gsk_good_key\n"
        assert env.stat().st_mode & 0o077 == 0
        models = client.get("/api/models").json()
        assert any(p["configured"] for p in models["cloud"]) and models["models"] == ["qwen2.5:7b"]
        assert client.post("/api/models", json={"model": "groq:llama-3.3-70b-versatile"}).json()["cloud"]
        assert client.post("/api/models", json={"model": "gemini:x"}).status_code == 409
        assert client.get("/api/health").json()["cloud"] is True
        assert client.delete("/api/providers/groq/key").status_code == 200
        assert "GROQ" not in env.read_text()
        assert client.put("/api/providers/nope/key", json={"key": "12345678"}).status_code == 404


def test_save_env_value_keeps_other_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    save_env_value(env, "GEMINI_API_KEY", "a")
    save_env_value(env, "GEMINI_API_KEY", "b")
    assert env.read_text() == "GEMINI_API_KEY=b\n"


def test_settings_read_conventional_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    s = Settings(_env_file=None)
    assert s.cloud_keys()["gemini"] == "from-env"
    assert "from-env" not in repr(s)  # keys never show up in logs


_ = FakeLLM  # the shared fixtures module is imported for its test setup
