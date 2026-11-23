"""Loads mcp_servers.json, starts each MCP server, and registers its tools with AETHER.

Config (same shape as Claude Desktop's, plus two optional AETHER keys):

    {"mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
                       "approval": "auto",          # auto | always | never   (default: auto)
                       "include": ["read_file"],    # optional allow-list of tool names
                       "disabled": false}}}

approval: "auto" runs tools the server marks read-only (readOnlyHint) freely and asks before
anything else; "always" asks before every call; "never" trusts the server completely.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..tools.registry import REGISTRY, Risk, ToolError, ToolRegistry, ToolSpec
from .client import MCPError, MCPServer

SEP = "__"


def tool_name(server: str, tool: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{server}{SEP}{tool}")
    return safe[:64]


def _schema(raw: Any) -> dict[str, Any]:
    schema = dict(raw) if isinstance(raw, dict) else {}
    schema["type"] = "object"
    schema.setdefault("properties", {})
    schema["required"] = [r for r in schema.get("required", []) if r in schema["properties"]]
    return schema


class MCPManager:
    def __init__(self, config_path: Path, registry: ToolRegistry = REGISTRY, base_dir: Path | None = None):
        self.config_path = config_path
        self.registry = registry
        self.base_dir = base_dir or config_path.parent
        self.servers: dict[str, MCPServer] = {}
        self.status: dict[str, dict[str, Any]] = {}
        self._registered: list[str] = []

    def load_config(self) -> dict[str, dict[str, Any]]:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            self.status["_config"] = {"name": "_config", "state": "error", "tools": 0,
                                      "error": f"{self.config_path.name} isn't valid JSON: {e}"}
            return {}
        servers = data.get("mcpServers", data.get("servers", {}))
        return servers if isinstance(servers, dict) else {}

    async def start_all(self, timeout_s: float = 45.0) -> None:
        self.status.pop("_config", None)
        config = self.load_config()
        await asyncio.gather(*(self._start(name, cfg, timeout_s) for name, cfg in config.items()))

    async def _start(self, name: str, cfg: dict[str, Any], timeout_s: float) -> None:
        base = {"name": name, "command": cfg.get("command", ""), "tools": 0, "error": None,
                "approval": cfg.get("approval", "auto")}
        if cfg.get("disabled"):
            self.status[name] = {**base, "state": "disabled"}
            return
        if not cfg.get("command"):
            self.status[name] = {**base, "state": "error", "error": "No `command` given (only stdio servers are supported)."}
            return
        self.status[name] = {**base, "state": "starting"}
        cwd = cfg.get("cwd")
        server = MCPServer(name, cfg["command"], cfg.get("args"), cfg.get("env"),
                           str((self.base_dir / cwd).resolve()) if cwd else str(self.base_dir))
        try:
            await asyncio.wait_for(server.start(timeout_s=timeout_s), timeout_s + 5)
            tools = await server.list_tools()
        except (MCPError, asyncio.TimeoutError, OSError) as e:
            await server.stop()
            hint = f" Last log: {server.stderr_tail[-1]}" if server.stderr_tail else ""
            self.status[name] = {**base, "state": "error", "error": f"{e or type(e).__name__}{hint}"}
            return
        include = set(cfg.get("include") or [])
        registered = 0
        for t in tools:
            if include and t.get("name") not in include:
                continue
            self._register(name, server, t, cfg.get("approval", "auto"))
            registered += 1
        self.servers[name] = server
        info = server.server_info
        self.status[name] = {**base, "state": "connected", "tools": registered,
                             "server": f"{info.get('name', name)} {info.get('version', '')}".strip()}

    def _register(self, server_name: str, server: MCPServer, t: dict[str, Any], approval: str) -> None:
        remote = t["name"]
        name = tool_name(server_name, remote)
        read_only = bool((t.get("annotations") or {}).get("readOnlyHint"))
        risk = Risk.SAFE if approval == "never" or (approval == "auto" and read_only) else Risk.REQUIRES_APPROVAL

        async def handler(**args: Any) -> str:
            try:
                ok, text = await server.call_tool(remote, args)
            except MCPError as e:
                raise ToolError(f"MCP server '{server_name}': {e}") from e
            if not ok:
                raise ToolError(text)
            return text

        spec = ToolSpec(
            name=name,
            description=f"[MCP · {server_name}] {(t.get('description') or remote).strip()[:900]}",
            handler=handler, parameters=_schema(t.get("inputSchema")), risk=risk,
            category="mcp", icon="plug", timeout_s=90,
            risk_summary=lambda args, r=remote, s=server_name: (
                f"Call '{r}' on the external MCP server '{s}' ({server.command}). It runs outside "
                "AETHER's sandbox with whatever access that server has."))
        if name in self.registry.names:
            self.registry.unregister(name)
        self.registry.register_spec(spec)
        self._registered.append(name)

    async def stop_all(self) -> None:
        for name in self._registered:
            self.registry.unregister(name)
        self._registered.clear()
        await asyncio.gather(*(s.stop() for s in self.servers.values()), return_exceptions=True)
        self.servers.clear()

    async def reload(self) -> None:
        await self.stop_all()
        self.status.clear()
        await self.start_all()

    def summary(self) -> list[dict[str, Any]]:
        return list(self.status.values())
