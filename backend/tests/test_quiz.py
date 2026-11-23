"""Interactive quizzes: normalisation of model output, storage, weak-topic tracking."""
from __future__ import annotations

import json

import pytest

from aether.engine import AetherAgent
from aether.events import EventType
from aether.memory.quizzes import QuizError, normalize_questions
from aether.tools import REGISTRY

from .conftest import FakeLLM, call, say
from .test_agent_loop import run


def test_normalize_handles_messy_model_output():
    qs = normalize_questions([
        {"question": "What does 3NF remove?", "options": ["A) Partial deps", "B) Transitive deps", "C) Keys"],
         "answer": "B", "topic": "Normalization"},
        {"q": "Unit of paging?", "choices": ["Frame", "Segment"], "correct": 0, "topic": "Paging"},
        {"question": "Define RAG", "answer": "Retrieval-augmented generation", "topic": "AI"},
        {"question": "Broken", "options": ["x", "y"], "answer": "z"},  # answer not an option → dropped
        {"answer": "no question"},
    ])
    assert [q["type"] for q in qs] == ["mcq", "mcq", "flashcard"]
    assert qs[0]["answer"] == 1 and qs[1]["answer"] == 0
    with pytest.raises(QuizError):
        normalize_questions([{"question": "x", "options": ["a", "b"], "answer": "c"}])


async def test_weak_topics_become_memories(mem):
    quiz = mem.quizzes.create("DBMS", [
        {"question": "q1", "options": ["a", "b"], "answer": "a", "topic": "Normalization"},
        {"question": "q2", "options": ["a", "b"], "answer": "b", "topic": "Normalization"},
        {"question": "q3", "options": ["a", "b"], "answer": "a", "topic": "Joins"},
    ])
    await mem.quizzes.record(quiz["id"], 2, True)
    first = await mem.quizzes.record(quiz["id"], 0, False)
    assert not first["weak"]  # one attempt isn't enough to call it weak
    second = await mem.quizzes.record(quiz["id"], 1, False)
    assert second["weak"] and second["wrong"] == 2
    assert [w["topic"] for w in mem.quizzes.weak_topics()] == ["Normalization"]
    assert any("Normalization difficult" in f["text"] for f in mem.facts.all())
    assert "Weak quiz topics" in await mem.build_context("quiz me")


async def test_make_quiz_tool_through_the_agent(settings, mem):
    questions = [{"question": "Is TCP reliable?", "options": ["Yes", "No"], "answer": "Yes", "topic": "TCP"}]
    llm = FakeLLM([call("make_quiz", title="Networks", questions=json.dumps(questions)),  # list sent as a string
                   say("Your quiz is ready, have a go!")])
    events = await run(AetherAgent(settings, llm=llm), "quiz me on networks")
    result = next(e for e in events if e.type is EventType.TOOL_RESULT).data
    assert result["ok"] and "Quiz #1 ready" in result["output"]
    assert mem.quizzes.get(1)["questions"][0]["answer"] == 0


def test_make_quiz_schema_guides_the_model():
    items = REGISTRY.get("make_quiz").parameters["properties"]["questions"]["items"]
    assert items["required"] == ["question", "answer", "topic"]


async def test_quiz_request_answered_in_text_is_sent_back(settings, mem):
    questions = [{"question": "Q?", "options": ["A", "B"], "answer": "A", "topic": "T"}]
    llm = FakeLLM([say("1. What is X? Answer: Y"),  # plain-text quiz → nudged
                   call("make_quiz", title="Quiz", questions=questions),
                   say("Your quiz is ready.")])
    events = await run(AetherAgent(settings, llm=llm), "Quiz me on my notes")
    assert any(e.type is EventType.THOUGHT and "make_quiz" in e.data["text"] for e in events)
    assert "Call make_quiz now" in llm.requests[1]["messages"][-1]["content"]
    assert any(e.type is EventType.TOOL_RESULT and e.data["ok"] for e in events)


async def test_generic_quiz_query_covers_the_whole_document(mem, tmp_path):
    from .conftest import make_pdf
    await mem.knowledge.ingest(make_pdf(tmp_path / "os.pdf", [f"Topic {i}: page {i} content about systems." for i in range(1, 9)]))
    hits = await mem.knowledge.search("quiz me on my notes", k=4)
    assert len(hits) == 4 and len({h.page for h in hits}) == 4  # spread across pages
