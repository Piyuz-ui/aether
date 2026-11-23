"""PDF export, honest failure reporting, and serving created files."""
from __future__ import annotations

from pypdf import PdfReader

from aether.engine import AetherAgent
from aether.events import EventType
from aether.tools import REGISTRY, Risk
from aether.tools.context import workspace
from aether.tools.notes import write_note

from .conftest import FakeLLM, call, say
from .test_agent_loop import done, run, types


def pdf_text(path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(str(path)).pages)


async def test_create_pdf_from_markdown_content(settings):
    md = "## Terms\n\n- **RAG**: retrieval + generation\n- café “quotes” ✓ → नमस्ते\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    res = await REGISTRY.execute(REGISTRY.get("create_pdf"), {"title": "AI terms", "content": md},
                                 timeout_s=60, max_output_chars=2000)
    assert res.ok and "exports/ai-terms.pdf" in res.output
    text = pdf_text(workspace().resolve("exports/ai-terms.pdf"))
    assert "AI terms" in text and "RAG" in text and "café" in text and "page 1/1" in text


async def test_create_pdf_from_a_saved_note_by_title(settings):
    write_note("DBMS revision", "Normal forms remove redundancy.")
    res = await REGISTRY.execute(REGISTRY.get("create_pdf"),
                                 {"title": "DBMS revision", "source": "DBMS revision"},
                                 timeout_s=60, max_output_chars=2000)
    assert res.ok, res.output
    assert "Normal forms" in pdf_text(workspace().resolve("exports/dbms-revision.pdf"))


def test_create_pdf_checks_and_risk():
    spec = REGISTRY.get("create_pdf")
    assert spec.risk_for({"title": "brand new"}) is Risk.SAFE
    workspace().resolve("exports").mkdir(exist_ok=True)
    workspace().resolve("exports/existing.pdf").write_bytes(b"%PDF")
    assert spec.risk_for({"title": "existing"}) is Risk.REQUIRES_APPROVAL  # overwrite asks first
    import pytest
    from aether.tools import ToolError
    with pytest.raises(ToolError, match="source"):
        spec.precheck({"title": "x"})
    with pytest.raises(ToolError, match="Couldn't find"):
        spec.precheck({"title": "x", "source": "notes/missing.md"})


async def test_failed_script_is_a_failure_with_a_pdf_hint(settings):
    spec = REGISTRY.get("run_python")
    res = await REGISTRY.execute(spec, {"code": "import pdfkit"}, timeout_s=30, max_output_chars=4000)
    assert not res.ok and "exit code 1" in res.output and "create_pdf" in res.output
    ok = await REGISTRY.execute(spec, {"code": "print(6*7)"}, timeout_s=30, max_output_chars=4000)
    assert ok.ok and "42" in ok.output


async def test_agent_recovers_from_failed_script_via_create_pdf(settings):
    llm = FakeLLM([call("run_python", code="import pdfkit"),
                   say("Done, your PDF is ready."),  # false claim after a failure → nudged
                   call("create_pdf", title="Notes", content="# Notes\n\nHello"),
                   say("Created exports/notes.pdf.")])
    events = await run(AetherAgent(settings, llm=llm), "make a pdf", approve=True)
    results = [e.data for e in events if e.type is EventType.TOOL_RESULT]
    assert results[0]["ok"] is False and results[1]["ok"] is True
    assert done(events)["answer"] == "Created exports/notes.pdf."
    assert EventType.APPROVAL_REQUIRED in types(events)  # run_python always asks
