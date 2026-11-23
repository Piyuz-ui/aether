"""A small MCP (Model Context Protocol) client: local servers (stdio) and remote ones (by URL).

MCP stdio transport = newline-delimited JSON-RPC 2.0 over the server process's stdin/stdout.
Remote servers use the Streamable HTTP transport (MCPHttpServer, below).
This implements exactly what AETHER needs: initialize, tools/list (paginated), tools/call,
and answering a server's `ping`.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
from collections import deque
from typing import Any

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


class MCPServer:
    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | None = None,
                 request_timeout_s: float = 60.0):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.request_timeout_s = request_timeout_s
        self.proc: asyncio.subprocess.Process | None = None
        self.server_info: dict[str, Any] = {}
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    # ------------------------------------------------------------- lifecycle
    async def start(self, timeout_s: float = 30.0) -> None:
        env = {**os.environ, **self.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.command, *self.args, cwd=self.cwd, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        except FileNotFoundError as e:
            raise MCPError(f"Command not found: {self.command}") from e
        self._tasks = [asyncio.create_task(self._read_stdout()), asyncio.create_task(self._read_stderr())]
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aether", "version": "0.1.0"},
        }, timeout_s=timeout_s)
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        await self.notify("notifications/initialized")

    async def stop(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPError(f"MCP server '{self.name}' stopped."))
        self._pending.clear()
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    # --------------------------------------------------------------- JSON-RPC
    async def _send(self, message: dict[str, Any]) -> None:
        if not self.running or self.proc is None or self.proc.stdin is None:
            raise MCPError(f"MCP server '{self.name}' isn't running.")
        async with self._write_lock:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      timeout_s: float | None = None) -> Any:
        rid = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, timeout_s or self.request_timeout_s)
        except asyncio.TimeoutError as e:
            raise MCPError(f"MCP server '{self.name}' didn't answer {method} in time.") from e
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    async def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes print stray logs to stdout
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # a request from the server to us
                await self._answer_server(msg)
            elif "id" in msg:
                fut = self._pending.get(msg["id"])
                if fut and not fut.done():
                    if "error" in msg:
                        err = msg["error"] or {}
                        fut.set_exception(MCPError(f"{err.get('message', 'error')} (code {err.get('code')})"))
                    else:
                        fut.set_result(msg.get("result"))
        for fut in self._pending.values():  # the process exited
            if not fut.done():
                fut.set_exception(MCPError(f"MCP server '{self.name}' exited."
                                           + (f" Last log: {self.stderr_tail[-1]}" if self.stderr_tail else "")))

    async def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            self.stderr_tail.append(line.decode(errors="replace").rstrip())

    async def _answer_server(self, msg: dict[str, Any]) -> None:
        if msg.get("method") == "ping":
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:  # sampling, roots, elicitation… not supported by AETHER
            await self._send({"jsonrpc": "2.0", "id": msg["id"],
                              "error": {"code": -32601, "message": "Method not supported by AETHER"}})

    # ------------------------------------------------------------------ tools
    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(50):  # pagination guard
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += (result or {}).get("tools", [])
            cursor = (result or {}).get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        parts: list[str] = []
        for item in result.get("content", []) or []:
            kind = item.get("type")
            if kind == "text":
                parts.append(item.get("text", ""))
            elif kind == "resource":
                res = item.get("resource", {})
                parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
            elif kind == "resource_link":
                parts.append(f"[link {item.get('name', '')}: {item.get('uri', '')}]")
            else:
                parts.append(f"[{kind} content]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        return (not result.get("isError", False)), "\n".join(parts) or "(no output)"


class MCPHttpServer(MCPServer):
    """An MCP server reached by URL (the "Streamable HTTP" transport): each JSON-RPC message is
    a POST; the reply is JSON, or an event stream carrying it. Headers (e.g. an API token) are
    sent with every request. Servers that need an OAuth sign-in in a browser aren't supported."""

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None,
                 request_timeout_s: float = 60.0, transport: Any = None):
        super().__init__(name, command=url, request_timeout_s=request_timeout_s)
        import httpx

        self.url = url
        self.headers = dict(headers or {})
        self.session_id: str | None = None
        self._open = False
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(request_timeout_s, connect=10.0),
                                       transport=transport, follow_redirects=True)

    @property
    def running(self) -> bool:
        return self._open

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.server_info or self._open:
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return h

    async def start(self, timeout_s: float = 30.0) -> None:
        self._open = True
        try:
            result = await self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "aether", "version": "0.1.0"}}, timeout_s=timeout_s)
        except Exception:
            self._open = False
            raise
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        await self.notify("notifications/initialized")

    async def stop(self) -> None:
        if self._open and self.session_id:
            with contextlib.suppress(Exception):
                await self._http.delete(self.url, headers=self._headers(), timeout=3)
        self._open = False
        await self._http.aclose()

    async def _post(self, message: dict[str, Any], timeout_s: float) -> Any:
        import httpx

        try:
            async with self._http.stream("POST", self.url, json=message, headers=self._headers(),
                                         timeout=timeout_s) as r:
                if r.status_code in (401, 403):
                    raise MCPError(f"'{self.name}' refused access ({r.status_code}). If it needs a token, add it "
                                   "as a header (e.g. Authorization: Bearer …). Sign-in-in-a-browser (OAuth) "
                                   "servers aren't supported yet.")
                if r.status_code == 404 and self.session_id and message.get("method") != "initialize":
                    raise MCPError(f"'{self.name}' ended the session. Reload MCP servers to reconnect.")
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")[:200]
                    raise MCPError(f"'{self.name}' answered HTTP {r.status_code}: {body}")
                if sid := r.headers.get("mcp-session-id"):
                    self.session_id = sid
                if "id" not in message:  # a notification: 202 Accepted, nothing to read
                    return None
                ctype = r.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            msg = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        found = self._match(msg, message["id"])
                        if found is not _NO:
                            return found
                    raise MCPError(f"'{self.name}' closed the stream without answering.")
                payload = json.loads(await r.aread() or b"null")
                found = self._match(payload, message["id"])
                if found is _NO:
                    raise MCPError(f"'{self.name}' sent an unexpected reply.")
                return found
        except httpx.HTTPError as e:
            raise MCPError(f"Can't reach '{self.name}' at {self.url}: {type(e).__name__}") from e

    @staticmethod
    def _match(payload: Any, rid: int) -> Any:
        for msg in payload if isinstance(payload, list) else [payload]:
            if isinstance(msg, dict) and msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    err = msg["error"] or {}
                    raise MCPError(f"{err.get('message', 'error')} (code {err.get('code')})")
                return msg.get("result")
        return _NO

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      timeout_s: float | None = None) -> Any:
        if not self._open:
            raise MCPError(f"MCP server '{self.name}' isn't connected.")
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            msg["params"] = params
        try:
            return await asyncio.wait_for(self._post(msg, timeout_s or self.request_timeout_s),
                                          (timeout_s or self.request_timeout_s) + 5)
        except asyncio.TimeoutError as e:
            raise MCPError(f"MCP server '{self.name}' didn't answer {method} in time.") from e

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._post(msg, 10)


_NO = object()
