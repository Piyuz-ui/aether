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


def fake_embedding(text: str, dims: int = 96) -> list[float]:
    """Deterministic bag-of-words vector, so texts sharing words are 'similar'."""
    import hashlib
    import math
    import re as _re
    vec = [0.0] * dims
    for word in _re.findall(r"[a-z0-9]+", text.lower().replace("search_document:", "")
                            .replace("search_query:", "")):
        if len(word) < 3:
            continue
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeLLM:
    """Replays scripted replies as a token stream, like Ollama does, and embeds text."""

    def __init__(self, replies: list[dict[str, Any]] | None = None, *, embeddings: bool = True):
        self.replies = list(replies or [])
        self.requests: list[dict[str, Any]] = []
        self.embeddings = embeddings

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        if not self.embeddings:
            from aether.llm.ollama import OllamaError
            raise OllamaError(f"Model '{model}' isn't installed.", hint=f"Run: ollama pull {model}")
        return [fake_embedding(t) for t in texts]

    async def health(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["fake-model", "other-model"]

    async def aclose(self) -> None:
        return None

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
        if reply.get("hang"):  # simulate a model that keeps generating hidden junk forever
            import asyncio
            await asyncio.sleep(3600)
        yield ChatDelta(done=True, done_reason=reply.get("done_reason", "stop"),
                        stats=CallStats(prompt_tokens=100, output_tokens=10, total_s=0.1, gen_s=0.05))


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a small but real PDF with one line of text per page (no extra libraries)."""
    objs: list[bytes] = []
    n = len(pages)
    font_id = 3
    page_ids = [4 + 2 * i for i in range(n)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
                    f"{page_ids[i] + 1} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream +
                    b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def mem(settings, tmp_path):
    """A fresh MemorySystem (in-memory DB) wired into the tools, with fake embeddings."""
    from aether.memory import MemorySystem
    from aether.tools.context import set_memory
    s = settings.model_copy(update={"data_dir": tmp_path / "data"})
    system = MemorySystem(s, FakeLLM(), db_path=":memory:")
    set_memory(system)
    yield system
    set_memory(None)
    system.close()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
def fresh_workspace(tmp_path: Path):
    ws = Workspace(tmp_path / "workspace")
    (ws.root / "notes").mkdir(parents=True, exist_ok=True)
    set_workspace(ws)
    yield ws
