"""Reasoning levels, PC → Mac failover, and Markdown skills (slash commands)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import api
from aether.engine import AetherAgent
from aether.engine.session import Session
from aether.events import EventType
from aether.llm.ollama import OllamaClient
from aether.llm.router import ModelRouter
from aether.skills import SKILLS_DIR, list_skills, load_skill, parse_skill

from .conftest import FakeLLM, say
from .test_api import sse_events
from .test_hosts import FakePC


class Thinker(FakeLLM):
    """A fake backend whose models can think, recording what `think` each request got."""
    caps = {"gpt-oss:20b": {"completion", "tools", "thinking"}, "qwen3:14b": {"completion", "tools", "thinking"},
            "fake-model": {"completion", "tools"}}

    def __init__(self, replies: list[dict[str, Any]], model: str):
        super().__init__(replies)
        self.model, self.thinks = model, []

    async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]:
        return self.model, None

    def chat_stream(self, **kw: Any) -> Any:
        self.thinks.append(kw.get("think"))
        return super().chat_stream(**kw)


# --------------------------------------------------------------- reasoning
@pytest.mark.parametrize("model,level,think,label", [
    ("gpt-oss:20b", "high", "high", "high"),          # gpt-oss takes the level itself
    ("gpt-oss:20b", "low", "low", "low"),
    ("qwen3:14b", "medium", True, "on"),              # other thinking models: on…
    ("qwen3:14b", "low", False, None),                # …or off
    ("fake-model", "high", False, None),              # can't think: never sent
    ("gpt-oss:20b", None, False, None),               # no level: the usual setting (off)
])
async def test_reasoning_levels(model: str, level: str | None, think: Any, label: str | None) -> None:
    llm = Thinker([say("ok")], model)
    events = [e async for e in AetherAgent(llm=llm).run("hi", Session(), reasoning=level)]
    assert llm.thinks[0] == think
    first = next(e for e in events if e.type is EventType.STATUS and "model" in e.data)
    assert first.data["reasoning"] == label


# ------------------------------------------------------------------ failover
def mac_and_pc(pc: FakePC, mac_models: dict[str, list[str]] | None = None) -> ModelRouter:
    mac = FakePC(mac_models or {"qwen2.5:7b": ["completion", "tools"]})
    return ModelRouter(OllamaClient("http://mac.test", transport=httpx.MockTransport(mac)),
                       hosts={"pc": "http://192.168.1.9:11434"}, host_transport=httpx.MockTransport(pc))


async def test_failover_keeps_the_pc_when_it_answers() -> None:
    r = mac_and_pc(FakePC({"gpt-oss:20b": ["completion", "tools", "thinking"]}))
    assert await r.failover("gpt-oss:20b@pc", ["qwen2.5:7b"]) == ("gpt-oss:20b@pc", None)


async def test_failover_moves_to_the_mac_and_remembers_the_failure() -> None:
    pc = FakePC({"gpt-oss:20b": []}, online=False)
    calls: list[str] = []
    original = pc.__call__

    def counting(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return original(req)
    r = ModelRouter(OllamaClient("http://mac.test", transport=httpx.MockTransport(FakePC({"qwen2.5:7b": []}))),
                    hosts={"pc": "http://192.168.1.9:11434"}, host_transport=httpx.MockTransport(counting))
    model, note = await r.failover("gpt-oss:20b@pc", ["gpt-oss:20b@pc", "qwen2.5:7b"])
    assert model == "qwen2.5:7b" and "pc isn't reachable" in (note or "") and "this Mac" in (note or "")
    tried = len(calls)
    await r.failover("gpt-oss:20b@pc", ["qwen2.5:7b"])                 # within 10 s: no new check
    assert len(calls) == tried
    # same model installed on the Mac too? use it
    r2 = mac_and_pc(FakePC({}, online=False), {"gpt-oss:20b": [], "qwen2.5:7b": []})
    assert (await r2.failover("gpt-oss:20b@pc", ["qwen2.5:7b"]))[0] == "gpt-oss:20b"
    assert await r.failover("qwen2.5:7b", []) == ("qwen2.5:7b", None)     # local models: untouched


async def test_agent_tells_you_when_it_falls_back() -> None:
    class Router(FakeLLM):
        async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]:
            return "gpt-oss:20b@pc", None

        async def failover(self, model: str, prefs: list[str]) -> tuple[str, str | None]:
            return "fake-model", "pc isn't reachable right now, so this answer runs on fake-model on this Mac."
    used: list[str] = []

    class Recording(Router):
        def chat_stream(self, **kw: Any) -> Any:
            used.append(kw["model"])
            return super().chat_stream(**kw)
    llm = Recording([say("hello")])
    events = [e async for e in AetherAgent(llm=llm).run("hi", Session())]
    assert next(e for e in events if e.type is EventType.STATUS and "model" in e.data).data["model"] == "fake-model"
    assert any(e.type is EventType.THOUGHT and "isn't reachable" in e.data["text"] for e in events)
    assert used == ["fake-model"]


# -------------------------------------------------------------------- skills
def test_parse_skill_front_matter() -> None:
    s = parse_skill("deep", "---\ndescription: Dig in  # comment\nusage: /deep <q>\ntools: [web_search, write_note]\n"
                            "reasoning: High\n---\nResearch {{input}} carefully.\n")
    assert s and s.description == "Dig in" and s.usage == "/deep <q>"
    assert s.tools == ["web_search", "write_note"] and s.reasoning == "high" and s.prompt == "Research {{input}} carefully."
    assert parse_skill("x", "---\ndescription: empty\n---\n") is None
    plain = parse_skill("plain", "Just do the thing.")
    assert plain and plain.description == "Just do the thing." and plain.tools == []


def test_builtin_skills_ship_and_name_real_tools() -> None:
    from aether.tools import REGISTRY
    names = {s.name for s in list_skills(SKILLS_DIR)}
    assert {"brief", "quiz", "deep"} <= names
    for s in list_skills(SKILLS_DIR):
        assert s.tools and all(t in REGISTRY.names for t in s.tools), s.name
    assert load_skill("/DEEP").reasoning == "high" and load_skill("nope") is None and load_skill("../x") is None


def test_api_skills_and_chat_with_a_skill(settings: Any, tmp_path: Path) -> None:
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "recap.md").write_text("---\ndescription: Recap a topic\ntools: search_knowledge_base\n---\n"
                                     "Recap {{input}} in three bullet points.")
    (folder / "Bad Name.md").write_text("ignored")
    llm = FakeLLM([say("• one • two • three")])
    s = settings.model_copy(update={"data_dir": tmp_path / "data", "auto_memory": False})
    app = api.create_app(s, llm=llm, db_path=":memory:")
    app.state.skills_dir = folder
    with TestClient(app) as c:
        assert [k["name"] for k in c.get("/api/skills").json()] == ["recap"]
        with c.stream("POST", "/api/chat", json={"message": "/recap photosynthesis", "skill": "recap"}) as r:
            events = list(sse_events(r))
        sid = events[0][1]["session_id"]
        sent = llm.requests[0]
        assert sent["messages"][-1]["content"] == "Recap photosynthesis in three bullet points."
        assert sent["tools"][0]["function"]["name"] == "search_knowledge_base"     # the skill's tool first
        user = c.get(f"/api/sessions/{sid}").json()["messages"][0]
        assert user["content"] == "/recap photosynthesis"                           # the chat keeps what you typed
        assert c.post("/api/chat", json={"message": "/nope", "skill": "nope"}).status_code == 404
        assert c.post("/api/chat", json={"message": "hi", "reasoning": "extreme"}).status_code == 422


_ = json


# ------------------------------------------------- thinking needs output room
@pytest.mark.parametrize("model,level,room", [
    ("gpt-oss:20b", "high", 9216), ("gpt-oss:20b", "medium", 6144), ("gpt-oss:20b", "low", 3072),
    ("qwen3:14b", "high", 6144),        # on/off models: "on" gets the medium room
    ("fake-model", "high", 2048),       # doesn't think: the usual cap
])
async def test_reasoning_gets_more_output_room(model: str, level: str, room: int) -> None:
    llm = Thinker([say("ok")], model)
    [e async for e in AetherAgent(llm=llm).run("hi", Session(), reasoning=level)]
    assert llm.requests[0]["options"]["num_predict"] == room


async def test_reasoning_that_runs_out_is_handed_back_not_thrown_away() -> None:
    long_thought = "Use a dict plus a doubly linked list so get and put are O(1). " * 40
    llm = Thinker([{"thinking": long_thought, "content": "", "done_reason": "length"},
                   say("Here's an O(1) LRU cache: a dict for lookups and a doubly linked list for order.")],
                  "gpt-oss:20b")
    events = [e async for e in AetherAgent(llm=llm).run("write an LRU cache in python", Session(), reasoning="high")]
    note = next(e for e in events if e.type is EventType.THOUGHT)
    assert "handing its reasoning back" in note.data["text"]
    retry = llm.requests[1]
    assert "<reasoning>" in retry["messages"][-1]["content"] and "doubly linked list" in retry["messages"][-1]["content"]
    assert retry["think"] == "low" and llm.thinks == ["high", "low"]
    done = next(e for e in events if e.type is EventType.DONE)
    assert done.data["answer"].startswith("Here's an O(1) LRU cache")


async def test_an_honest_message_when_thinking_never_finishes() -> None:
    stuck = {"thinking": "Still thinking about edge cases. " * 40, "content": "", "done_reason": "length"}
    llm = Thinker([stuck, stuck, stuck, say("")], "gpt-oss:20b")
    events = [e async for e in AetherAgent(llm=llm).run("prove P != NP", Session(), reasoning="high")]
    answer = next(e for e in events if e.type is EventType.DONE).data["answer"]
    assert "ran out of room while thinking" in answer and "Reasoning: Med" in answer
