"""Wave 1: tool packs + hybrid router + meta-tool, analyze_dataset queries, sympy, universal file
ingestion (Word, Excel/CSV, code, notebooks, images) and the research tools (YouTube, arXiv,
Wikipedia). Everything offline: mocked HTTP, fake embeddings, fake vision."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import api
import tool_router
from aether.engine import AetherAgent
from aether.engine.session import Session
from aether.events import EventType
from aether.tools import REGISTRY, ToolError, code_data, research
from aether.tools.context import workspace
from tool_router import META_TOOL, ToolIndex, route

from .conftest import FakeLLM, call, say

ALL = REGISTRY.all()


@pytest.fixture(autouse=True)
def packs_reset():
    tool_router.set_state([], [])
    yield
    tool_router.set_state([], [])


# ------------------------------------------------------------- router + packs
def test_six_packs_with_levels_and_the_meta_tool_always_offered() -> None:
    summary = {p["id"]: p for p in tool_router.packs_summary(ALL)}
    assert list(summary) == ["system_mac", "knowledge_rag", "code_data", "research_web", "productivity", "mcp_bridge"]
    assert {"youtube_transcript", "arxiv_wiki_lookup"} <= {t["name"] for t in summary["research_web"]["tools"]}
    assert {t["level"] for t in summary["code_data"]["tools"]} == {"SAFE", "HIGH_RISK"}
    assert route("summarise this", ALL)[-1] == META_TOOL


def test_switched_off_packs_never_appear_and_pinned_packs_lead() -> None:
    tool_router.set_state(disabled=["research_web"])
    picked = route("search the web for the latest news", ALL)
    assert not {"web_search", "read_webpage"} & set(picked)
    tool_router.set_state(disabled=[], pinned=["productivity"])
    picked = route("hello there", ALL)
    assert picked[0] in {"add_task", "manage_tasks", "add_class", "manage_timetable", "recall_memory", "remember", "forget"}


def test_meaning_finds_a_tool_with_no_shared_words() -> None:
    prompt = "what is this clip about?"
    assert "youtube_transcript" not in route(prompt, ALL)
    sims = {s.name: 0.40 for s in ALL} | {"youtube_transcript": 0.78}
    assert route(prompt, ALL, similarity=sims)[0] == "youtube_transcript"


async def test_tool_index_caches_tool_vectors() -> None:
    calls = {"docs": 0}

    async def docs(texts: list[str]) -> list[list[float]]:
        calls["docs"] += 1
        return [[1.0, 0.0] if "youtube" in t else [0.0, 1.0] for t in texts]

    async def query(text: str) -> list[float]:
        return [1.0, 0.1]

    idx = ToolIndex(docs, query)
    sims = await idx.similarities("video", ALL)
    assert sims["youtube_transcript"] > 0.9 > sims["add_task"]
    await idx.similarities("another", ALL)
    assert calls["docs"] == 1                                    # tool vectors embedded once

    async def none(_: Any) -> None:
        return None
    assert await ToolIndex(none, none).similarities("x", ALL) == {}   # no embedding model: no scores


async def test_agent_loads_a_pack_mid_task() -> None:
    llm = FakeLLM([call(META_TOOL, pack="research_web"), say("Loaded.")])
    events = [e async for e in AetherAgent(llm=llm).run("hello", Session())]
    assert "arxiv_wiki_lookup" not in {t["function"]["name"] for t in llm.requests[0]["tools"]}
    assert {"arxiv_wiki_lookup", "youtube_transcript"} <= {t["function"]["name"] for t in llm.requests[1]["tools"]}
    assert any(e.type is EventType.STATUS and "arxiv_wiki_lookup" in (e.data.get("tools") or []) for e in events)


async def test_a_switched_off_pack_refuses_to_run() -> None:
    tool_router.set_state(disabled=["code_data"])
    llm = FakeLLM([call("python_calculator", expression="2+2"), say("ok")])
    events = [e async for e in AetherAgent(llm=llm).run("2+2", Session())]
    result = next(e for e in events if e.type is EventType.TOOL_RESULT)
    assert not result.data["ok"] and "switched off" in result.data["output"]


async def test_meta_tool_lists_and_loads() -> None:
    from aether.tools.capabilities import list_available_capabilities
    listing = await list_available_capabilities()
    assert "research_web" in listing and "youtube_transcript" in listing
    assert "Loaded the Research & web pack" in await list_available_capabilities("research_web")
    tool_router.set_state(disabled=["system_mac"])
    assert "switched off" in await list_available_capabilities("system_mac")
    assert "There's no pack" in await list_available_capabilities("nope")


def test_api_packs_persist(settings: Any, tmp_path: Path) -> None:
    s = settings.model_copy(update={"data_dir": tmp_path / "data", "auto_memory": False})
    app = api.create_app(s, llm=FakeLLM([]), db_path=":memory:")
    with TestClient(app) as c:
        assert len(c.get("/api/packs").json()) == 6
        r = c.put("/api/packs/research_web", json={"enabled": False}).json()
        assert r["enabled"] is False and r["count"] >= 4
        assert c.put("/api/packs/productivity", json={"pinned": True}).json()["pinned"] is True
        assert json.loads(app.state.ctx.memory.store.get_meta("tool_packs")) == {
            "disabled": ["research_web"], "pinned": ["productivity"]}
        assert "web_search" not in c.get("/api/tools/route", params={"q": "search the web"}).json()["tools"]
        assert c.put("/api/packs/nope", json={"enabled": True}).status_code == 404


# ------------------------------------------------------------- code & data
@pytest.fixture
def marks_csv() -> str:
    import pandas as pd
    up = workspace().root / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"student": ["A", "B", "C", "D", "E"], "subject": ["Maths", "Maths", "Physics", "Physics", "Maths"],
                  "marks": [78, 92, 85, 61, 70]}).to_csv(up / "marks.csv", index=False)
    return "uploads/marks.csv"


async def test_analyze_dataset_groups_filters_and_sorts(marks_csv: str) -> None:
    out = await code_data.analyze_dataset(marks_csv, group_by="subject", agg="mean", columns="marks", sort_by="marks")
    assert "grouped by subject (mean)" in out and "80" in out and "73" in out
    assert out.index("Maths") < out.index("Physics")                 # sorted high → low
    top = await code_data.analyze_dataset(marks_csv, filter="marks > 80 and subject == 'Maths'")
    assert "1 rows" in top and "92" in top
    count = await code_data.analyze_dataset(marks_csv, group_by="subject")
    assert "| Maths     |       3 |" in count or "Maths" in count and "3" in count
    assert "5 rows × 3 columns" in await code_data.analyze_dataset(marks_csv)   # plain profile


@pytest.mark.parametrize("bad", ["__import__('os').system('ls')", "marks.sum() > 1", "@x > 1",
                                 "student.str.contains('A')", "import os"])
async def test_analyze_dataset_rejects_code_in_filters(marks_csv: str, bad: str) -> None:
    with pytest.raises(ToolError, match="may only compare columns"):
        await code_data.analyze_dataset(marks_csv, filter=bad)


async def test_analyze_dataset_explains_bad_columns(marks_csv: str) -> None:
    with pytest.raises(ToolError, match="No column named grade"):
        await code_data.analyze_dataset(marks_csv, group_by="grade")


async def test_sandbox_has_sympy() -> None:
    out = await code_data.python_sandbox("import sympy as sp\nx = sp.symbols('x')\nprint(sp.solve(x**2 - 5*x + 6, x))")
    assert "[2, 3]" in out


# ------------------------------------------------------- universal ingestion
def _write_docx(path: Path) -> None:
    import docx
    d = docx.Document()
    d.add_heading("Normalisation", level=1)
    d.add_paragraph("Third normal form removes transitive dependencies.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Form", "Rule"
    t.cell(1, 0).text, t.cell(1, 1).text = "BCNF", "Every determinant is a key"
    d.save(str(path))


def test_readers_for_word_sheets_notebooks_and_code(tmp_path: Path) -> None:
    import pandas as pd
    from aether.memory.knowledge import citation_unit, read_pages
    _write_docx(tmp_path / "notes.docx")
    pages = read_pages(tmp_path / "notes.docx")
    assert "## Normalisation" in pages[0] and "BCNF | Every determinant is a key" in pages[0]
    with pd.ExcelWriter(tmp_path / "grades.xlsx") as w:
        pd.DataFrame({"subject": ["Maths"], "marks": [91]}).to_excel(w, sheet_name="Term 1", index=False)
        pd.DataFrame({"subject": ["Physics"], "marks": [77]}).to_excel(w, sheet_name="Term 2", index=False)
    sheets = read_pages(tmp_path / "grades.xlsx")
    assert len(sheets) == 2 and "Sheet Term 2" in sheets[1] and "subject: Physics; marks: 77" in sheets[1]
    (tmp_path / "nb.ipynb").write_text(json.dumps({"cells": [
        {"cell_type": "markdown", "source": ["# Gradient descent"]},
        {"cell_type": "code", "source": ["w -= lr * grad"], "outputs": [{"text": "SECRET OUTPUT"}]}]}))
    nb = read_pages(tmp_path / "nb.ipynb")[0]
    assert "# Gradient descent" in nb and "w -= lr * grad" in nb and "SECRET OUTPUT" not in nb
    (tmp_path / "algo.py").write_text("def binary_search(a, x):\n    ...\n")
    assert "binary_search" in read_pages(tmp_path / "algo.py")[0]
    assert [citation_unit(n) for n in ("a.docx", "b.xlsx", "c.png", "d.pdf", "e.pptx")] == \
        ["part", "sheet", "image", "p.", "slide"]


async def test_ingest_any_file_reads_word_and_images(mem: Any) -> None:
    from aether.tools.knowledge import ingest_any_file, search_knowledge_base
    up = workspace().root / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    _write_docx(up / "notes.docx")
    out = await ingest_any_file("uploads/notes.docx")
    assert out.startswith("Added notes.docx") and "part" in out
    found = await search_knowledge_base("transitive dependencies", document="notes.docx")
    assert "notes.docx part 1" in found
    assert (await ingest_any_file("uploads/notes.docx")).startswith("Already in the vault")

    (up / "board.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    async def vision(path: Path) -> str:
        return "[Image read by fake-vision]\nWhiteboard: Dijkstra's algorithm uses a priority queue."
    mem.knowledge.describe_image = vision
    assert "Added board.png" in await ingest_any_file("uploads/board.png")
    assert "board.png image" in await search_knowledge_base("Dijkstra priority queue", document="board.png")

    async def blind(path: Path) -> None:
        return None
    mem.knowledge.describe_image = blind
    (up / "other.png").write_bytes(b"\x89PNG\r\n\x1a\nfake2")
    with pytest.raises(ToolError, match="No vision model"):
        await ingest_any_file("uploads/other.png")


# ----------------------------------------------------------------- research
@pytest.mark.parametrize("raw,vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42", "dQw4w9WgXcQ"), ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("not a link", None),
])
def test_video_ids(raw: str, vid: str | None) -> None:
    assert research.video_id(raw) == vid


async def test_youtube_transcript_with_timestamps_and_clear_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research, "_fetch_transcript",
                        lambda vid, langs: ("English", [(0.0, "Welcome to the lecture."), (3725.4, "Thanks for watching.")]))
    out = await research.youtube_transcript("https://youtu.be/dQw4w9WgXcQ")
    assert "[0:00] Welcome to the lecture." in out and "[1:02:05] Thanks for watching." in out and "1:02:05 long" in out

    class TranscriptsDisabled(Exception):
        pass

    class ConnectionError(Exception):  # noqa: A001 - mimics requests' name
        pass
    for exc, msg in ((TranscriptsDisabled(), "no captions"), (ConnectionError("Max retries"), "offline")):
        def boom(vid: str, langs: list[str], e: Exception = exc) -> None:
            raise e
        monkeypatch.setattr(research, "_fetch_transcript", boom)
        with pytest.raises(ToolError, match=msg):
            await research.youtube_transcript("dQw4w9WgXcQ")
    with pytest.raises(ToolError, match="doesn't look like a YouTube link"):
        await research.youtube_transcript("https://vimeo.com/123")


ARXIV = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/1706.03762</id><published>2017-06-12T00:00:00Z</published>
<title>Attention Is All You Need</title><summary>The dominant sequence transduction models...</summary>
<author><name>Ashish Vaswani</name></author><author><name>Noam Shazeer</name></author></entry></feed>"""


def research_net(req: httpx.Request) -> httpx.Response:
    if req.url.host == "export.arxiv.org":
        assert "transformers" in str(req.url.params["search_query"])
        return httpx.Response(200, text=ARXIV)
    if req.url.path == "/w/api.php":
        return httpx.Response(200, json={"query": {"search": [{"title": "Transformer (deep learning)"}]}})
    if "/page/summary/" in req.url.path:
        return httpx.Response(200, json={"title": "Transformer (deep learning)", "extract": "A transformer is a deep learning architecture.",
                                         "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Transformer_(deep_learning)"}}})
    return httpx.Response(404)


async def test_arxiv_and_wikipedia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research, "_transport", httpx.MockTransport(research_net))
    out = await research.arxiv_wiki_lookup("transformers")
    assert "**Attention Is All You Need** (Ashish Vaswani, Noam Shazeer, 2017-06-12)" in out
    assert "http://arxiv.org/abs/1706.03762" in out and "A transformer is a deep learning architecture." in out
    only = await research.arxiv_wiki_lookup("transformers", source="wikipedia")
    assert "arXiv" not in only


async def test_research_says_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=req)
    monkeypatch.setattr(research, "_transport", httpx.MockTransport(down))
    with pytest.raises(ToolError, match="You're offline"):
        await research.arxiv_wiki_lookup("anything")
