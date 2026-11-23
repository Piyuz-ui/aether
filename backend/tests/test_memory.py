"""Phase 2: store, sessions, facts, tasks, timetable, knowledge vault (RAG), memory tools."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from aether.engine import AetherAgent
from aether.events import EventType
from aether.memory import MemorySystem
from aether.memory.embedder import Embedder
from aether.memory.knowledge import chunk_text
from aether.memory.taskbook import TaskError, describe_due, parse_clock, parse_due, parse_weekday
from aether.tools import REGISTRY, Risk

from .conftest import FakeLLM, call, make_pdf, say
from .test_agent_loop import run, types


# ----------------------------------------------------------------- chunking
def test_chunks_respect_size_and_overlap():
    para = " ".join(f"Sentence number {i} talks about databases and normal forms." for i in range(60))
    chunks = chunk_text(para + "\n\nShort closing paragraph.")
    assert len(chunks) > 2
    assert all(len(c) <= 1300 for c in chunks)
    # consecutive chunks share some text (overlap keeps context across boundaries)
    assert any(chunks[i][-40:].split()[-1] in chunks[i + 1] for i in range(len(chunks) - 1))
    assert chunk_text("   ") == []


# ----------------------------------------------------------------- sessions
def test_sessions_persist_and_reload(mem):
    s1 = mem.session()
    s1.add_turn([{"role": "user", "content": "What is paging in operating systems?"},
                 {"role": "assistant", "content": "", "tool_calls": [
                     {"function": {"name": "web_search", "arguments": {"query": "paging"}}}]},
                 {"role": "tool", "tool_name": "web_search", "content": "results"},
                 {"role": "assistant", "content": "Paging splits memory into frames."}])
    reloaded = mem.session(s1.id)
    assert reloaded.turns == s1.turns
    assert reloaded.turns[0][1]["tool_calls"][0]["function"]["name"] == "web_search"
    listed = mem.store.list_sessions()
    assert listed[0]["title"].startswith("What is paging") and listed[0]["turns"] == 1


# -------------------------------------------------------------------- facts
async def test_facts_dedupe_search_and_delete(mem):
    f1, created = await mem.facts.add("The user studies BCA AI and Data Science.", "academic")
    assert created
    f2, created2 = await mem.facts.add("The user studies BCA AI and Data Science.", "academic")
    assert not created2 and f2["id"] == f1["id"]
    await mem.facts.add("The user prefers short answers with examples.", "preference")
    hits = await mem.facts.search("what does the user study at college? BCA data science")
    assert hits and hits[0]["id"] == f1["id"]
    assert mem.facts.delete(f1["id"]) and len(mem.facts.all()) == 1


async def test_facts_fall_back_to_keywords_without_embeddings(settings, tmp_path):
    s = settings.model_copy(update={"data_dir": tmp_path / "d"})
    system = MemorySystem(s, FakeLLM(embeddings=False), db_path=":memory:")
    await system.facts.add("The user plays the guitar on weekends.", "general")
    hits = await system.facts.search("guitar")
    assert hits and "guitar" in hits[0]["text"]
    assert system.embedder.available is False and "ollama pull" in system.embedder.last_error.hint
    system.close()


# -------------------------------------------------------------------- tasks
def test_date_and_time_parsing():
    assert parse_due("2026-12-04") == "2026-12-04"
    assert parse_due("2026-12-04 17:30") == "2026-12-04T17:30"
    assert parse_due("") is None
    assert parse_weekday("tue") == 1 and parse_weekday("Sunday") == 6 and parse_weekday(2) == 2
    assert parse_clock("2pm") == "14:00" and parse_clock("9:05") == "09:05"
    now = datetime(2026, 11, 23, 12, 0)
    assert describe_due("2026-11-24", now) == "due tomorrow"
    assert describe_due("2026-11-21", now) == "overdue by 2 days"


def test_taskbook_crud_and_overdue(mem):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    past = (date.today() - timedelta(days=3)).isoformat()
    a = mem.tasks.add("DBMS assignment", "deadline", tomorrow)
    b = mem.tasks.add("Return library book", "reminder", past)
    assert a["when"].startswith("due tomorrow") and b["overdue"]
    assert [t["id"] for t in mem.tasks.list(within_days=7)] == [b["id"], a["id"]]
    mem.tasks.complete(a["id"])
    assert [t["id"] for t in mem.tasks.list()] == [b["id"]]
    assert mem.tasks.update(b["id"], title="Return 2 library books")["title"].startswith("Return 2")
    assert mem.tasks.delete(b["id"]) and mem.tasks.list() == []


def test_timetable(mem):
    c = mem.tasks.add_class("DBMS", "Monday", "9am", "10:00", "Room 204")
    assert (c["day"], c["start"], c["end"]) == ("Monday", "09:00", "10:00")
    with pytest.raises(TaskError):
        mem.tasks.add_class("OS", "Tue", "11:00", "10:00")
    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7)
    assert [x["course"] for x in mem.tasks.classes_on(next_monday)] == ["DBMS"]


# ---------------------------------------------------------- knowledge vault
async def test_pdf_ingest_and_hybrid_search(mem, tmp_path):
    pdf = make_pdf(tmp_path / "dbms_notes.pdf", [
        "Normalization removes redundancy by splitting tables into normal forms.",
        "Paging divides physical memory into fixed size frames for processes.",
        "The third normal form 3NF removes transitive dependencies between columns."])
    events = []
    doc = await mem.knowledge.ingest(pdf, on_progress=events.append)
    assert doc["status"] == "ready" and doc["pages"] == 3 and doc["chunks"] == 3
    assert [e["status"] for e in events if "status" in e][:2] == ["reading", "embedding"]
    assert events[-1]["progress"] == 1.0

    hits = await mem.knowledge.search("what is the third normal form 3NF?")
    assert hits[0].page == 3 and hits[0].cite() == "dbms_notes.pdf p.3"
    hits = await mem.knowledge.search("memory frames paging")
    assert hits[0].page == 2

    again, is_new = mem.knowledge.register(pdf)
    assert not is_new and again["id"] == doc["id"]  # duplicate upload detected


async def test_markdown_ingest_and_document_filter(mem, tmp_path):
    md = tmp_path / "os.md"
    md.write_text("# Scheduling\n\nRound robin gives each process a time quantum.\n")
    await mem.knowledge.ingest(md)
    pdf = make_pdf(tmp_path / "dbms.pdf", ["Round robin is not a database concept at all."])
    await mem.knowledge.ingest(pdf)
    hits = await mem.knowledge.search("round robin", document="os.md")
    assert {h.filename for h in hits} == {"os.md"}


async def test_scanned_pdf_reports_error(mem, tmp_path):
    pdf = make_pdf(tmp_path / "scan.pdf", [""])
    doc = await mem.knowledge.ingest(pdf)
    assert doc["status"] == "error" and "scanned" in doc["error"]


async def test_keyword_only_indexing_without_embedding_model(settings, tmp_path):
    s = settings.model_copy(update={"data_dir": tmp_path / "d"})
    system = MemorySystem(s, FakeLLM(embeddings=False), db_path=":memory:")
    md = tmp_path / "bio.md"
    md.write_text("Mitochondria is the powerhouse of the cell.")
    doc = await system.knowledge.ingest(md)
    assert doc["status"] == "ready" and "keyword search only" in doc["error"]
    hits = await system.knowledge.search("powerhouse mitochondria")
    assert hits and hits[0].filename == "bio.md"
    system.close()


# ------------------------------------------------------------ memory context
async def test_build_context_includes_everything_relevant(mem, tmp_path):
    await mem.facts.add("The user is in the first year of BCA.", "profile")
    mem.tasks.add("OS lab record", "deadline", (date.today() + timedelta(days=2)).isoformat())
    mem.tasks.add_class("Statistics", date.today().strftime("%A"), "10:00", "11:00")
    md = tmp_path / "syllabus.md"
    md.write_text("Unit 1: probability.")
    await mem.knowledge.ingest(md)
    context = await mem.build_context("help me plan my week")
    assert "first year of BCA" in context
    assert "OS lab record" in context and "Statistics" in context
    assert "syllabus.md" in context and "search_knowledge_base" in context


async def test_auto_fact_extraction(mem):
    llm = FakeLLM([say('Sure: [{"fact": "The user is preparing for GATE 2027.", "category": "goal"},'
                       ' {"fact": "Paris is in France.", "category": "general"}]')])
    saved = await mem.extract_facts(llm, "fake", "I'm preparing for GATE 2027", "Great goal!")
    assert [f["text"] for f in saved] == ["The user is preparing for GATE 2027."]
    # Nothing personal said → no extra model call at all
    assert await mem.extract_facts(FakeLLM([]), "fake", "What is 2+2?", "4") == []


async def test_agent_emits_memory_event_after_done(settings, mem):
    llm = FakeLLM([say("Nice! Good luck with your exams."),
                   say('[{"fact": "The user has exams in December.", "category": "academic"}]')])

    async def learn(goal, answer, model):
        return await mem.extract_facts(llm, model, goal, answer)

    agent = AetherAgent(settings, llm=llm, memory_provider=mem.build_context, after_turn=learn)
    events = await run(agent, "My exams are in December")
    seq = types(events)
    assert seq.index(EventType.DONE) < seq.index(EventType.MEMORY)
    assert mem.facts.all()[0]["text"] == "The user has exams in December."


# --------------------------------------------------------------------- tools
async def test_planner_tools_through_the_agent(settings, mem):
    due = (date.today() + timedelta(days=5)).isoformat()
    llm = FakeLLM([call("add_task", title="ML quiz", kind="exam", due=due),
                   call("manage_tasks", action="list"),
                   say("Added your ML quiz."),
                   call("manage_tasks", action="delete", task_id=1),
                   say("Deleted.")])
    agent = AetherAgent(settings, llm=llm)
    events = await run(agent, "add ML quiz")
    outputs = [e.data["output"] for e in events if e.type is EventType.TOOL_RESULT]
    assert "Added" in outputs[0] and "ML quiz" in outputs[1]
    events = await run(agent, "delete it", approve=False)
    assert EventType.APPROVAL_REQUIRED in types(events)
    assert len(mem.tasks.list()) == 1  # denied, so still there


def test_tool_risk_levels_for_phase2(mem):
    tasks = REGISTRY.get("manage_tasks")
    assert tasks.risk_for({"action": "list"}) is Risk.SAFE
    assert REGISTRY.get("add_task").parameters["required"] == ["title", "due"]
    assert REGISTRY.get("add_class").parameters["required"] == ["course", "day", "start", "end"]
    assert tasks.risk_for({"action": "delete"}) is Risk.REQUIRES_APPROVAL
    assert REGISTRY.get("forget").risk is Risk.REQUIRES_APPROVAL
    assert REGISTRY.get("search_knowledge_base").risk is Risk.SAFE


async def test_knowledge_and_memory_tools(mem, tmp_path):
    kb_tool = REGISTRY.get("search_knowledge_base")
    empty = await REGISTRY.execute(kb_tool, {"query": "x"}, timeout_s=5, max_output_chars=4000)
    assert "empty" in empty.output
    await mem.knowledge.ingest(make_pdf(tmp_path / "cn.pdf", ["TCP is a reliable connection oriented protocol."]))
    res = await REGISTRY.execute(kb_tool, {"query": "is TCP reliable?"}, timeout_s=5,
                                 max_output_chars=4000)
    assert res.ok and "[cn.pdf p.1]" in res.output

    rem = await REGISTRY.execute(REGISTRY.get("remember"),
                                 {"fact": "The user likes dark mode.", "category": "preference"},
                                 timeout_s=5, max_output_chars=1000)
    assert "Remembered" in rem.output
    rec = await REGISTRY.execute(REGISTRY.get("recall_memory"), {"query": "everything"},
                                 timeout_s=5, max_output_chars=1000)
    assert "dark mode" in rec.output


async def test_precheck_blocks_bad_task_ids(settings, mem):
    llm = FakeLLM([call("manage_tasks", action="complete", task_id=42), say("No such task.")])
    events = await run(AetherAgent(settings, llm=llm), "complete 42")
    result = next(e for e in events if e.type is EventType.TOOL_RESULT).data
    assert not result["ok"] and "doesn't exist" in result["output"]


def test_embedder_prefixes_for_nomic():
    e = Embedder(FakeLLM(), "nomic-embed-text")
    assert e._doc("x") == "search_document: x" and e._query("x") == "search_query: x"
    assert Embedder(FakeLLM(), "mxbai-embed-large")._doc("x") == "x"


async def test_auto_learning_skips_facts_already_known(mem):
    await mem.facts.add("The user is Piyush, a first-year BCA AI & Data Science student.", "profile")
    llm = FakeLLM([say('[{"fact": "The user studies BCA AI & Data Science.", "category": "academic"}]')])
    saved = await mem.extract_facts(llm, "fake", "I'm a BCA AI & Data Science student", "Nice!")
    assert saved == [] and len(mem.facts.all()) == 1


async def test_auto_learning_keeps_genuinely_new_facts(mem):
    await mem.facts.add("The user likes coffee in the morning.", "preference")
    llm = FakeLLM([say('[{"fact": "The user plays chess every Sunday.", "category": "general"}]')])
    saved = await mem.extract_facts(llm, "fake", "I play chess every Sunday", "Fun!")
    assert [f["text"] for f in saved] == ["The user plays chess every Sunday."]


@pytest.mark.parametrize("said, expected", [
    ("tomorrow", "2026-11-24"), ("tomorrow at 5pm", "2026-11-24T17:00"),
    ("day after tomorrow", "2026-11-25"), ("in 3 days", "2026-11-26"), ("in two weeks", "2026-12-07"),
    ("friday", "2026-11-27"), ("this Friday", "2026-11-27"), ("next Friday", "2026-12-04"),
    ("next friday 17:30", "2026-12-04T17:30"), ("monday", "2026-11-30"), ("next monday", "2026-11-30"),
    ("Dec 4", "2026-12-04"), ("4th December", "2026-12-04"), ("January 15", "2027-01-15"),
    ("by 4 dec 2026 at 9am", "2026-12-04T09:00"), ("today 23:59", "2026-11-23T23:59"),
    ("2026-12-04", "2026-12-04"),
])
def test_natural_language_due_dates(said, expected):
    monday = datetime(2026, 11, 23, 10, 0)  # a Monday
    assert parse_due(said, monday) == expected


def test_unparseable_due_date_explains():
    with pytest.raises(TaskError, match="next Friday"):
        parse_due("whenever I feel like it")


async def test_search_collapses_duplicate_passages(mem, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("Normalization scales values between zero and one.")
    b = tmp_path / "b.md"
    b.write_text("Normalization scales values between zero and one.\n")  # different bytes, same text
    c = tmp_path / "c.md"
    c.write_text("Standardization gives mean zero and unit variance.")
    for f in (a, b, c):
        await mem.knowledge.ingest(f)
    hits = await mem.knowledge.search("normalization scales values", k=2)
    assert len({h.text.strip() for h in hits}) == 2
