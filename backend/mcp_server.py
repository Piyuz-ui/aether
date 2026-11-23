"""AETHER as an MCP server, so Claude (Desktop or Code) can use your study data.

    Claude starts this itself (Settings → MCP → "Let Claude use AETHER" sets it up):
        backend/.venv/bin/python backend/mcp_server.py

It speaks MCP over stdio (newline-delimited JSON-RPC on stdin/stdout; logs go to stderr) and
offers AETHER's own tools for your knowledge vault, memory, tasks, timetable and notes. Tools
that run code or delete things are left out: Claude has its own, and asks you before using any.
It reads the same local database as the app, so both stay in sync.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aether import __version__  # noqa: E402

EXPOSED = [  # AETHER tools Claude may use (by name)
    "search_knowledge_base", "list_documents", "recall_memory", "remember",
    "add_task", "manage_tasks", "add_class", "manage_timetable",
    "write_note", "read_file", "list_files", "create_pdf",
]
READ_ONLY = {"search_knowledge_base", "list_documents", "recall_memory", "read_file", "list_files"}
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "AETHER is the user's local study assistant. Its tools reach the user's own data on this computer: "
    "documents they uploaded (search_knowledge_base cites file and page), lasting facts about them "
    "(recall_memory / remember), their tasks, deadlines and exams, their weekly class timetable, and "
    "notes in AETHER's workspace. Search the documents before answering questions about course material.")


def log(*parts: Any) -> None:
    print("[aether-mcp]", *parts, file=sys.stderr, flush=True)


class AetherMCP:
    def __init__(self) -> None:
        from aether.config import get_settings
        from aether.llm.ollama import OllamaClient
        from aether.memory import MemorySystem
        from aether.tools import REGISTRY
        from aether.tools.context import set_memory

        self.settings = get_settings()
        llm = OllamaClient(self.settings.ollama_url, timeout_s=self.settings.request_timeout_s)
        self.memory = MemorySystem(self.settings, llm)  # embeddings for search come from local Ollama
        set_memory(self.memory)
        self.registry = REGISTRY
        self.tools = {n: REGISTRY.get(n) for n in EXPOSED if REGISTRY.get(n) is not None}

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name, "description": spec.description, "inputSchema": spec.parameters,
                 "annotations": {"title": name.replace("_", " ").capitalize(),
                                 "readOnlyHint": name in READ_ONLY,
                                 "destructiveHint": name == "manage_tasks" or name == "manage_timetable",
                                 "openWorldHint": False}}
                for name, spec in self.tools.items()]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self.tools.get(name)
        if spec is None:
            return {"content": [{"type": "text", "text": f"AETHER has no tool named '{name}'."}], "isError": True}
        clean, errors = self.registry.validate(spec, args or {})
        if not errors and spec.precheck is not None:
            try:
                spec.precheck(clean)
            except Exception as e:  # noqa: BLE001 - report every problem as text
                errors.append(str(e))
        if errors:
            return {"content": [{"type": "text", "text": "Can't run: " + " ".join(errors)}], "isError": True}
        result = await self.registry.execute(spec, clean, timeout_s=self.settings.tool_timeout_s,
                                             max_output_chars=self.settings.max_tool_output_chars)
        return {"content": [{"type": "text", "text": result.output}], "isError": not result.ok}

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None:  # a notification (e.g. notifications/initialized): no reply
            return None

        def ok(result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        if method == "initialize":
            asked = params.get("protocolVersion")
            return ok({"protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "aether", "title": "AETHER", "version": __version__},
                       "instructions": INSTRUCTIONS})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": self.list_tools()})
        if method == "tools/call":
            try:
                return ok(await self.call(params.get("name", ""), params.get("arguments") or {}))
            except Exception as e:  # noqa: BLE001 - a tool failure is a result, not a crash
                log("tool error:", e)
                return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def serve(reader: asyncio.StreamReader, write: Any, server: AetherMCP | None = None) -> None:
    server = server or AetherMCP()
    log(f"ready: {len(server.tools)} tools")
    while line := await reader.readline():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        for m in msg if isinstance(msg, list) else [msg]:
            if isinstance(m, dict):
                reply = await server.handle(m)
                if reply is not None:
                    write(json.dumps(reply, ensure_ascii=False) + "\n")


async def main() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    await serve(reader, write)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
