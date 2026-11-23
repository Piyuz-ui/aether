"""Test setup: an isolated workspace/data dir, and a scripted fake model."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

_TMP = Path(tempfile.mkdtemp(prefix="aether-tests-"))
os.environ["AETHER_WORKSPACE_DIR"] = str(_TMP / "workspace")
os.environ["AETHER_DATA_DIR"] = str(_TMP / "data")

import inspect  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

try:
    import pytest_asyncio  # noqa: E402,F401
except ImportError:  # pragma: no cover - depends on the interpreter used
    pytest.exit(
        f"These tests need AETHER's virtual environment, but they're running with "
        f"{sys.executable}.\nRun them like this:\n"
        "  cd backend && source .venv/bin/activate && python -m pytest -q\n"
        "or, without activating:  backend/.venv/bin/python -m pytest backend -q",
        returncode=4)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # works from any folder

from aether.config import get_settings  # noqa: E402
from aether.llm.ollama import ChatDelta  # noqa: E402
from aether.metrics import CallStats  # noqa: E402
from aether.tools.context import set_workspace  # noqa: E402
from aether.workspace import Workspace  # noqa: E402


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark async tests explicitly, so they run even when pytest starts outside backend/
    and doesn't read backend/pytest.ini (asyncio_mode = auto)."""
    for item in items:
        fn = getattr(item, "function", None)
        if fn is not None and inspect.iscoroutinefunction(fn) and "backend" in str(item.path):
            item.add_marker(pytest.mark.asyncio)


def say(text: str) -> dict[str, Any]:
    return {"content": text}


def call(name: str, **args: Any) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class FakeLLM:
    """Replays scripted replies as a token stream, like Ollama does."""

    def __init__(self, replies: list[dict[str, Any]]):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]:
        return "fake-model", None

    async def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None = None,
                          options: dict[str, Any] | None = None, think: bool = False):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply.get("content", "")
        for i in range(0, len(text), 7):  # stream in small chunks
            yield ChatDelta(content=text[i:i + 7])
        if reply.get("tool_calls"):
            yield ChatDelta(tool_calls=reply["tool_calls"])
        yield ChatDelta(done=True, stats=CallStats(prompt_tokens=100, output_tokens=10,
                                                   total_s=0.1, gen_s=0.05))


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
def fresh_workspace(tmp_path: Path):
    ws = Workspace(tmp_path / "workspace")
    (ws.root / "notes").mkdir(parents=True, exist_ok=True)
    set_workspace(ws)
    yield ws
