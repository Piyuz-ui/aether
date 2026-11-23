"""Chat titles and feedback; MCP setup from the app (servers, remote URLs, Claude both ways)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import api
from aether.mcp.client import MCPError, MCPHttpServer, MCPServer
from aether.mcp.config import MASK, ServerConfig, connect_claude, find_claude_servers, import_claude_servers
from aether.mcp.manager import MCPManager
from aether.memory.titles import clean_title
from aether.tools import REGISTRY

from .conftest import FakeLLM, say
from .test_api import sse_events

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ titles
@pytest.mark.parametrize("raw,title", [
    ("Photosynthesis Basics", "Photosynthesis Basics"),
    ('Title: "Exam Plan for DBMS."', "Exam Plan for DBMS"),
    ("<think>hmm</think>\n\n**Weekly Timetable Setup**", "Weekly Timetable Setup"),
    ("", None), ("ok", None),
    ("one two three four five six seven eight nine ten", None),   # a sentence: rejected, not clipped
    ("How vaccines work", "How vaccines work"),
])
def test_clean_title(raw: str, title: str | None) -> None:
    assert clean_title(raw) == title


@pytest.fixture
def client(settings: Any, tmp_path: Path):
    made: list[TestClient] = []

    def factory(replies: list[dict[str, Any]], **state: Any) -> TestClient:
        s = settings.model_copy(update={"data_dir": tmp_path / "data", "auto_memory": False,
                                        "mcp_config": tmp_path / "mcp_servers.json"})
        app = api.create_app(s, llm=FakeLLM(replies), db_path=":memory:")
        for k, v in state.items():
            setattr(app.state, k, v)
        c = TestClient(app)
        c.__enter__()
        made.append(c)
        return c

    yield factory
    for c in made:
        c.__exit__(None, None, None)


def chat(c: TestClient, text: str, session_id: str | None = None) -> list[tuple[str, Any]]:
    body: dict[str, Any] = {"message": text}
    if session_id:
        body["session_id"] = session_id
    with c.stream("POST", "/api/chat", json=body) as r:
        return list(sse_events(r))


def title_of(c: TestClient, sid: str, expect: str | None = None) -> str:
    """Titles are written in the background: wait briefly for one to land."""
    import time
    for _ in range(40):
        title = c.get(f"/api/sessions/{sid}").json()["title"]
        if expect is None or title == expect:
            return title
        time.sleep(0.05)
    return title


def test_chats_are_named_after_the_first_and_third_reply(client: Any) -> None:
    c = client([say("Light becomes sugar."), say("Photosynthesis Basics"),
                say("Chlorophyll."), say("Leaves."), say("Photosynthesis in Plants")])
    events = chat(c, "how do plants make food?")
    sid = events[0][1]["session_id"]
    assert not [e for e, _ in events if e == "title"]                  # the reply isn't held up
    assert title_of(c, sid, "Photosynthesis Basics") == "Photosynthesis Basics"
    chat(c, "what's green?", sid)                                      # 2nd reply: no retitle
    assert title_of(c, sid) == "Photosynthesis Basics"
    chat(c, "where does it happen?", sid)
    assert title_of(c, sid, "Photosynthesis in Plants") == "Photosynthesis in Plants"


def test_a_name_you_type_is_kept(client: Any) -> None:
    c = client([say("Hi!"), say("Should Not Appear"), say("Again"), say("x"), say("Nope")])
    sid = chat(c, "hello")[0][1]["session_id"]
    title_of(c, sid, "Should Not Appear")                              # let the 1st auto-title land
    assert c.patch(f"/api/sessions/{sid}", json={"title": "My chat"}).status_code == 200
    chat(c, "two", sid)
    chat(c, "three", sid)                                              # 3rd reply: would retitle…
    import time
    time.sleep(0.3)
    assert c.get(f"/api/sessions/{sid}").json()["title"] == "My chat"  # …but it's yours
    # ✨ asks the model explicitly, even over your name
    c2 = client([say("Hi!"), say("First Title"), say("Better Title")])
    sid2 = chat(c2, "hello")[0][1]["session_id"]
    title_of(c2, sid2, "First Title")
    c2.patch(f"/api/sessions/{sid2}", json={"title": "Mine"})
    assert c2.post(f"/api/sessions/{sid2}/title").json()["title"] == "Better Title"


def test_feedback_is_saved_per_reply(client: Any) -> None:
    c = client([say("one"), say("Some Title"), say("two")])
    sid = chat(c, "a")[0][1]["session_id"]
    title_of(c, sid, "Some Title")
    chat(c, "b", sid)
    assert c.post(f"/api/sessions/{sid}/feedback", json={"turn": 0, "rating": "good"}).status_code == 200
    c.post(f"/api/sessions/{sid}/feedback", json={"turn": 1, "rating": "bad", "reason": "Wrong answer",
                                                  "model": "fake-model"})
    c.post(f"/api/sessions/{sid}/feedback", json={"turn": 0, "rating": "bad"})   # changed your mind
    replies = [m for m in c.get(f"/api/sessions/{sid}").json()["messages"] if m["role"] == "assistant"]
    assert replies[0]["feedback"]["rating"] == "bad"
    assert replies[1]["feedback"] == {"rating": "bad", "reason": "Wrong answer"}
    listed = next(s for s in c.get("/api/sessions").json() if s["id"] == sid)
    assert (listed["good"] or 0, listed["bad"]) == (0, 2)
    assert c.post(f"/api/sessions/{sid}/feedback", json={"turn": 0, "rating": "meh"}).status_code == 422
    assert c.post("/api/sessions/nope/feedback", json={"turn": 0, "rating": "good"}).status_code == 404


# -------------------------------------------------------------- MCP config
def test_server_config_masks_and_keeps_secrets(tmp_path: Path) -> None:
    cfg = ServerConfig(tmp_path / "mcp_servers.json")
    cfg.save("github", {"command": "docker", "args": ["run", "-i"], "env": {"TOKEN": "ghp_secret"}, "approval": "always"})
    from aether.mcp.config import masked
    assert masked(cfg.servers()["github"])["env"] == {"TOKEN": MASK}
    cfg.save("github", {"command": "docker", "args": ["run", "-i", "x"], "env": {"TOKEN": MASK}, "approval": "auto"})
    assert cfg.servers()["github"]["env"]["TOKEN"] == "ghp_secret"            # unchanged secret kept
    cfg.save("docs", {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer t"}})
    assert set(cfg.servers()) == {"github", "docs"} and (tmp_path / "mcp_servers.json").stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        cfg.save("bad name!", {"command": "x"})
    with pytest.raises(ValueError):
        cfg.save("x", {"url": "ftp://nope"})
    assert cfg.remove("docs") and not cfg.remove("docs")


# ----------------------------------------------------- remote MCP (HTTP)
def fake_remote(*, sse: bool = False, auth: str | None = None) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if auth and req.headers.get("authorization") != auth:
            return httpx.Response(401)
        msg = json.loads(req.content)
        if "id" not in msg:
            return httpx.Response(202)
        result: Any = {}
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "docs", "version": "2"}, "capabilities": {}}
        elif msg["method"] == "tools/list":
            result = {"tools": [{"name": "search_docs", "description": "Search docs",
                                 "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                                 "annotations": {"readOnlyHint": True}}]}
        elif msg["method"] == "tools/call":
            result = {"content": [{"type": "text", "text": f"found: {msg['params']['arguments']['q']}"}]}
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        headers = {"mcp-session-id": "sess-1"}
        if sse:
            body = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n' \
                   f"event: message\ndata: {json.dumps(reply)}\n\n"
            return httpx.Response(200, content=body.encode(), headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=reply, headers=headers)
    return httpx.MockTransport(handle), seen


@pytest.mark.parametrize("sse", [False, True])
async def test_remote_server_json_and_stream_replies(sse: bool) -> None:
    transport, seen = fake_remote(sse=sse, auth="Bearer t")
    s = MCPHttpServer("docs", "https://example.com/mcp", {"Authorization": "Bearer t"}, transport=transport)
    await s.start()
    assert s.server_info["name"] == "docs" and s.session_id == "sess-1"
    tools = await s.list_tools()
    assert tools[0]["name"] == "search_docs"
    assert await s.call_tool("search_docs", {"q": "mcp"}) == (True, "found: mcp")
    assert seen[-1].headers["mcp-session-id"] == "sess-1" and seen[-1].headers["mcp-protocol-version"]
    await s.stop()


async def test_remote_server_refusal_explains_tokens() -> None:
    transport, _ = fake_remote(auth="Bearer right")
    s = MCPHttpServer("docs", "https://example.com/mcp", {"Authorization": "Bearer wrong"}, transport=transport)
    with pytest.raises(MCPError, match="token"):
        await s.start()


async def test_manager_registers_a_remote_servers_tools(tmp_path: Path) -> None:
    from aether.tools.registry import ToolRegistry
    path = tmp_path / "mcp_servers.json"
    ServerConfig(path).save("docs", {"url": "https://example.com/mcp", "approval": "auto"})
    reg = ToolRegistry()
    m = MCPManager(path, registry=reg)
    m.http_transport, _ = fake_remote()
    await m.start_all()
    st = m.summary()[0]
    assert st["state"] == "connected" and st["kind"] == "remote" and st["tools"] == 1
    spec = reg.get("docs__search_docs")
    assert spec is not None and spec.risk.value == "safe"            # read-only + approval auto
    assert await spec.handler(q="hello") == "found: hello"
    await m.stop_all()


# --------------------------------------------------------------- Claude
def claude_files(tmp_path: Path) -> dict[str, Path]:
    desktop = tmp_path / "Claude/claude_desktop_config.json"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(json.dumps({"globalShortcut": "Cmd+Space", "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Desktop"]},
        "old-sse": {"type": "sse", "url": "https://x.example/sse"}}}))
    code = tmp_path / ".claude.json"
    code.write_text(json.dumps({"numStartups": 3, "mcpServers": {"linear": {"type": "http", "url": "https://mcp.linear.app/mcp"}},
                                "projects": {"/Users/me/app": {"mcpServers": {"db": {"command": "uvx", "args": ["db-mcp"]}}}}}))
    return {"desktop": desktop, "code": code}


def test_find_and_import_claude_servers(tmp_path: Path) -> None:
    paths = claude_files(tmp_path)
    found = {(e["source"], e["name"]): e for e in find_claude_servers(paths)}
    assert set(found) == {("Claude Desktop", "filesystem"), ("Claude Desktop", "old-sse"),
                          ("Claude Code", "linear"), ("Claude Code · app", "db")}
    assert not found[("Claude Desktop", "old-sse")]["supported"] and found[("Claude Code", "linear")]["kind"] == "remote"
    cfg = ServerConfig(tmp_path / "mcp_servers.json")
    added = import_claude_servers(cfg, [{"source": "Claude Desktop", "name": "filesystem"},
                                        {"source": "Claude Code", "name": "linear"},
                                        {"source": "Claude Desktop", "name": "old-sse"}], paths)
    assert added == ["filesystem", "linear"]
    assert cfg.servers()["filesystem"]["approval"] == "always"        # imported ones ask first
    assert cfg.servers()["linear"]["url"] == "https://mcp.linear.app/mcp"


def test_connect_aether_to_claude_desktop_keeps_everything_else(tmp_path: Path) -> None:
    paths = claude_files(tmp_path)
    note = connect_claude("desktop", True, paths)
    data = json.loads(paths["desktop"].read_text())
    assert "reopen Claude Desktop" in note and data["globalShortcut"] == "Cmd+Space"
    assert set(data["mcpServers"]) == {"filesystem", "old-sse", "aether"}
    assert data["mcpServers"]["aether"]["args"][0].endswith("mcp_server.py")
    assert (paths["desktop"].parent / "claude_desktop_config.json.aether-backup").exists()
    connect_claude("desktop", False, paths)
    assert "aether" not in json.loads(paths["desktop"].read_text())["mcpServers"]


def test_connect_aether_to_claude_code_uses_its_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import aether.mcp.config as mc
    calls: list[list[str]] = []
    monkeypatch.setattr(mc, "claude_cli", lambda: "/usr/local/bin/claude")
    connect_claude("code", True, claude_files(tmp_path),
                   runner=lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0, stderr=""))
    assert calls[-1][:6] == ["/usr/local/bin/claude", "mcp", "add", "--scope", "user", "aether"]
    assert calls[-1][-1].endswith("mcp_server.py")
    monkeypatch.setattr(mc, "claude_cli", lambda: None)
    with pytest.raises(FileNotFoundError, match="claude mcp add"):
        connect_claude("code", True, claude_files(tmp_path / "b"))


def test_api_mcp_setup(client: Any, tmp_path: Path) -> None:
    c = client([], claude_paths=claude_files(tmp_path))
    r = c.put("/api/mcp/servers/notes", json={"command": sys.executable, "args": ["-c", "pass"], "env": {"K": "secret"}})
    assert r.status_code == 200 and r.json()["name"] == "notes"
    cfg = c.get("/api/mcp/config").json()
    assert cfg["servers"][0]["config"]["env"] == {"K": MASK} and cfg["servers"][0]["status"]["name"] == "notes"
    claude = c.get("/api/mcp/claude").json()
    assert len(claude["found"]) == 4 and claude["aether"]["desktop"]["connected"] is False
    assert c.post("/api/mcp/claude/import", json={"items": [{"source": "Claude Code", "name": "linear"}]}).json() == {"added": ["linear"]}
    assert next(f for f in c.get("/api/mcp/claude").json()["found"] if f["name"] == "linear")["added"]
    assert c.post("/api/mcp/claude/connect", json={"target": "desktop"}).json()["connected"]
    assert c.get("/api/mcp/claude").json()["aether"]["desktop"]["connected"]
    assert c.delete("/api/mcp/servers/notes").status_code == 200
    assert c.delete("/api/mcp/servers/notes").status_code == 404


# ------------------------------------------- AETHER as an MCP server (real)
async def test_aether_mcp_server_over_real_stdio(tmp_path: Path) -> None:
    """Start mcp_server.py exactly as Claude would and drive it with AETHER's own MCP client."""
    env = {"AETHER_DATA_DIR": str(tmp_path / "data"), "AETHER_WORKSPACE_DIR": str(tmp_path / "ws")}
    s = MCPServer("aether", sys.executable, [str(BACKEND / "mcp_server.py")], env=env, cwd=str(tmp_path))
    await s.start(timeout_s=30)
    try:
        assert s.server_info["name"] == "aether"
        names = {t["name"] for t in await s.list_tools()}
        assert {"search_knowledge_base", "add_task", "manage_timetable", "remember"} <= names
        assert not names & {"run_shell_command", "run_python", "delete_file", "forget"}   # never offered
        ok, text = await s.call_tool("add_task", {"title": "Revise DBMS", "kind": "exam", "due": "2026-12-01"})
        assert ok and "Revise DBMS" in text
        ok, text = await s.call_tool("run_shell_command", {"command": "ls"})
        assert not ok and "no tool" in text
    finally:
        await s.stop()


_ = (asyncio, REGISTRY)
