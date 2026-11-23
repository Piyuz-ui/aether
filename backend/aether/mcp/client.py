"""A small, dependency-free MCP (Model Context Protocol) client for stdio servers.

MCP stdio transport = newline-delimited JSON-RPC 2.0 over the server process's stdin/stdout.
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
