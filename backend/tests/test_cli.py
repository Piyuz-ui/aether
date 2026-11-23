"""Render every kind of event through the real CLI, approval panel included.

Rich normally ignores styles it can't parse, so a typo only loses its colour, except in a
few places (like panel titles) where it crashes. These tests make style lookups strict, so
any bad style fails here instead of at someone's approval prompt.
"""
from __future__ import annotations

import io

import pytest
from rich.console import Console

import cli
from aether.events import AgentEvent, EventType
from aether.tools.context import workspace

from .conftest import FakeLLM, call, say


@pytest.fixture
def term(monkeypatch):
    original = Console.get_style

    def strict_get_style(self, name, *, default=None):  # noqa: ARG001 - ignore the fallback
        return original(self, name)

    monkeypatch.setattr(Console, "get_style", strict_get_style)
    buf = io.StringIO()
    console = Console(theme=cli.THEME, file=buf, force_terminal=True, width=100,
                      color_system="truecolor")
    monkeypatch.setattr(cli, "console", console)
    return buf


def make_cli(replies, monkeypatch, approve: bool):
    c = cli.CLI(llm=FakeLLM(replies), db_path=":memory:")
    c.agent.model = "fake-model"
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: approve)
    return c


async def test_full_goal_with_approval_renders(term, monkeypatch):
    target = workspace().resolve("notes/old.md")
    target.write_text("bye")
    c = make_cli([say("Let me check that.") | call("delete_file", path="notes/old.md"),
                  say("Done, it's deleted.")], monkeypatch, approve=True)
    await c.run_goal("delete notes/old.md")
    out = term.getvalue()
    assert "wants to run delete_file" in out and "can't be undone" in out
    assert "approved" in out and "Done, it's deleted." in out
    assert not target.exists()


async def test_denied_approval_renders(term, monkeypatch):
    target = workspace().resolve("notes/keep.md")
    target.write_text("keep")
    c = make_cli([call("run_shell_command", command="sudo rm -rf notes"), say("Okay.")],
                 monkeypatch, approve=False)
    await c.run_goal("wipe notes")
    out = term.getvalue()
    assert "administrator" in out and "denied" in out
    assert target.exists()


def test_every_event_type_renders(term, monkeypatch):
    c = make_cli([], monkeypatch, approve=True)
    events = [
        AgentEvent(EventType.STATUS, {"state": "thinking"}),
        AgentEvent(EventType.TOKEN, {"text": "hmm", "channel": "thinking"}),
        AgentEvent(EventType.TOKEN, {"text": "Hello", "channel": "content"}),
        AgentEvent(EventType.THOUGHT, {"text": "I'll search.", "kind": "retry"}),
        AgentEvent(EventType.THOUGHT, {"text": "I'll search."}),
        AgentEvent(EventType.STATUS, {"state": "using_tool", "tool": "web_search"}),
        AgentEvent(EventType.TOOL_START, {"name": "web_search", "args": {"query": "x"},
                                          "risk": "safe"}),
        AgentEvent(EventType.TOOL_RESULT, {"name": "web_search", "ok": False, "output": "boom",
                                           "duration_s": 0.1}),
        AgentEvent(EventType.APPROVAL_RESOLVED, {"approved": False, "reason": "no"}),
        AgentEvent(EventType.DONE, {"answer": "**Hi**", "stats": {"summary": "1 s"}}),
        AgentEvent(EventType.MEMORY, {"saved": [{"id": 1, "text": "The user likes tea.",
                                                 "category": "preference"}]}),
        AgentEvent(EventType.ERROR, {"message": "Ollama is off", "hint": "Open Ollama"}),
    ]
    for e in events:
        c.handle(e)
    c.view.stop()
    out = term.getvalue()
    assert "Ollama is off" in out and "Open Ollama" in out


async def test_banner_and_commands_render(term, monkeypatch):
    c = make_cli([], monkeypatch, approve=True)
    assert await c.banner()
    for command in ["/help", "/tools", "/stats", "/verbose", "/reset", "/nope", "/docs",
                    "/memory", "/forget 99", "/tasks", "/timetable", "/sessions", "/resume 1",
                    "/ingest /does/not/exist.pdf"]:
        assert await c.command(command)
    assert not await c.command("/exit")
    assert "A E T H E R" in term.getvalue()
