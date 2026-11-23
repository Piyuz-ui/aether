"""Images: routed to a vision-capable model, sent to Ollama, never stored in history."""
from __future__ import annotations

import base64

from aether.engine import AetherAgent, Session
from aether.events import EventType

from .conftest import FakeLLM, call, say
from .test_agent_loop import done, run

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


async def run_with_images(agent, goal, images, session=None):
    events = []
    async for e in agent.run(goal, session or Session(), images=images):
        events.append(e)
    return events


async def test_images_switch_to_a_vision_model_and_reach_it(settings):
    llm = FakeLLM([say("That's a timetable with 2 classes.")])
    events = await run_with_images(AetherAgent(settings, llm=llm), "what's this?", [PNG])
    thought = next(e for e in events if e.type is EventType.THOUGHT).data["text"]
    assert "other-model" in thought  # fake-model can't see, so it routed to the vision model
    assert done(events)["stats"]["model"] == "other-model"
    user = llm.requests[0]["messages"][-1]
    assert user["images"] == [PNG]


async def test_timetable_image_becomes_classes(settings, mem):
    llm = FakeLLM([
        {"content": "", "tool_calls": [
            {"function": {"name": "add_class", "arguments": {"course": "DBMS", "day": "Monday", "start": "9am", "end": "10am"}}},
            {"function": {"name": "add_class", "arguments": {"course": "OS", "day": "Tuesday", "start": "11:00", "end": "12:00"}}}]},
        say("Saved 2 classes from your timetable."),
    ])
    events = await run_with_images(AetherAgent(settings, llm=llm), "save my timetable", [PNG])
    assert sum(1 for e in events if e.type is EventType.TOOL_RESULT and e.data["ok"]) == 2
    assert [c["course"] for c in mem.tasks.classes()] == ["DBMS", "OS"]


async def test_images_are_not_kept_in_history(settings):
    llm = FakeLLM([say("A cat."), say("You asked about a picture.")])
    agent, session = AetherAgent(settings, llm=llm), Session()
    await run_with_images(agent, "what is this?", [PNG], session)
    assert session.turns[0][0]["meta"] == {"images": 1} and "images" not in session.turns[0][0]
    await run(agent, "what did I ask?", session)
    assert all("images" not in m and "meta" not in m for m in llm.requests[1]["messages"])


async def test_no_vision_model_gives_a_clear_fix(settings):
    llm = FakeLLM([])
    llm.caps = {"fake-model": {"completion", "tools"}}
    events = await run_with_images(AetherAgent(settings, llm=llm), "look", [PNG])
    err = next(e for e in events if e.type is EventType.ERROR).data
    assert "can look at images" in err["message"] and "ollama pull" in err["hint"]
