"""MCP client: handshake, paginated discovery, calls, errors, approval policy, shutdown."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aether.engine import AetherAgent
from aether.events import EventType
from aether.mcp import MCPManager
from aether.tools import REGISTRY, Risk

from .conftest import FakeLLM, call, say
from .test_agent_loop import run, types

SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))


def write_config(tmp_path: Path, servers: dict) -> Path:
    p = tmp_path / "mcp_servers.json"
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


@pytest.fixture
async def manager(tmp_path):
    mgr = MCPManager(write_config(tmp_path, {
        "dict": {"command": sys.executable, "args": [SERVER]},
        "off": {"command": sys.executable, "args": [SERVER], "disabled": True},
        "broken": {"command": "definitely-not-a-real-command-xyz"},
    }))
    await mgr.start_all(timeout_s=10)
    yield mgr
    await mgr.stop_all()


async def test_discovers_tools_with_pagination_and_policy(manager):
    status = {s["name"]: s for s in manager.summary()}
    assert status["dict"]["state"] == "connected" and status["dict"]["tools"] == 3
    assert status["dict"]["server"] == "fake-dictionary 1.0"
    assert status["off"]["state"] == "disabled"
    assert status["broken"]["state"] == "error" and "not found" in status["broken"]["error"]
    # approval "auto": read-only tools run freely, anything else asks first
    assert REGISTRY.get("dict__lookup_word").risk is Risk.SAFE
    assert REGISTRY.get("dict__save_bookmark").risk is Risk.REQUIRES_APPROVAL
    assert REGISTRY.get("dict__lookup_word").parameters["required"] == ["word"]


async def test_calls_and_errors_through_the_registry(manager):
    ok = await REGISTRY.execute(REGISTRY.get("dict__lookup_word"), {"word": "aether"},
                                timeout_s=10, max_output_chars=1000)
    assert ok.ok and "aether: a word" in ok.output
    bad = await REGISTRY.execute(REGISTRY.get("dict__explode"), {}, timeout_s=10, max_output_chars=1000)
    assert not bad.ok and "boom" in bad.output


async def test_agent_uses_mcp_tools_with_the_safety_gate(settings, manager):
    llm = FakeLLM([call("dict__save_bookmark", url="https://example.com"), say("Saved it.")])
    events = await run(AetherAgent(settings, llm=llm), "bookmark example.com", approve=True)
    assert EventType.APPROVAL_REQUIRED in types(events)
    req = next(e for e in events if e.type is EventType.APPROVAL_REQUIRED).data
    assert "external MCP server 'dict'" in req["summary"]
    assert next(e for e in events if e.type is EventType.TOOL_RESULT).data["output"] == "saved https://example.com"


async def test_stop_unregisters_tools(tmp_path):
    mgr = MCPManager(write_config(tmp_path, {"d": {"command": sys.executable, "args": [SERVER],
                                                    "include": ["lookup_word"], "approval": "always"}}))
    await mgr.start_all(timeout_s=10)
    assert "d__lookup_word" in REGISTRY.names and "d__save_bookmark" not in REGISTRY.names
    assert REGISTRY.get("d__lookup_word").risk is Risk.REQUIRES_APPROVAL  # "always" overrides read-only
    await mgr.stop_all()
    assert not any(n.startswith("d__") for n in REGISTRY.names)


async def test_missing_or_invalid_config(tmp_path):
    assert MCPManager(tmp_path / "nope.json").load_config() == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    mgr = MCPManager(bad)
    await mgr.start_all()
    assert mgr.summary()[0]["state"] == "error"
