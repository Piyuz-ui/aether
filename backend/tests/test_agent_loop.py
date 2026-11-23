"""The ReAct loop end to end, with a scripted model."""
from __future__ import annotations

from aether.engine import AetherAgent, Session
from aether.events import EventType
from aether.llm.ollama import OllamaError
from aether.tools.context import workspace

from .conftest import FakeLLM, call, say


async def run(agent: AetherAgent, goal: str, session: Session | None = None,
              approve: bool | None = None):
    """Collect events; answer approval requests with `approve`."""
    events = []
    async for e in agent.run(goal, session or Session()):
        events.append(e)
        if e.type is EventType.APPROVAL_REQUIRED and approve is not None:
            agent.broker.resolve(e.data["approval_id"], approve)
    return events


def types(events):
    return [e.type for e in events]


def done(events):
    return next(e for e in events if e.type is EventType.DONE).data


async def test_plain_answer_streams_tokens(settings):
    agent = AetherAgent(settings, llm=FakeLLM([say("Hello there, I'm AETHER.")]))
    events = await run(agent, "hi")
    tokens = "".join(e.data["text"] for e in events if e.type is EventType.TOKEN)
    assert tokens == "Hello there, I'm AETHER."
    assert done(events)["answer"] == "Hello there, I'm AETHER."
    assert done(events)["stats"]["prompt_tokens"] == 100


async def test_tool_loop_uses_result(settings):
    llm = FakeLLM([call("python_calculator", expression="17 * 23"), say("It's 391.")])
    events = await run(AetherAgent(settings, llm=llm), "17 times 23?")
    result = next(e for e in events if e.type is EventType.TOOL_RESULT)
    assert result.data["ok"] and "391" in result.data["output"]
    # The tool result was fed back to the model on the next step.
    assert any(m["role"] == "tool" and "391" in m["content"] for m in llm.requests[1]["messages"])
    assert types(events).index(EventType.TOOL_START) < types(events).index(EventType.TOOL_RESULT)


async def test_recovers_tool_call_written_as_text(settings):
    llm = FakeLLM([say('I will compute it.\n```json\n{"name": "python_calculator", '
                       '"arguments": {"expression": "2**10",}}\n```'), say("1024.")])
    events = await run(AetherAgent(settings, llm=llm), "2^10?")
    assert any(e.type is EventType.TOOL_RESULT and "1024" in e.data["output"] for e in events)
    assert any(e.type is EventType.THOUGHT and e.data["text"] == "I will compute it." for e in events)


async def test_retries_malformed_tool_call(settings):
    llm = FakeLLM([say('{"name": "python_calculator", "arguments": {expression: 2+'),
                   call("python_calculator", expression="2+2"), say("4")])
    events = await run(AetherAgent(settings, llm=llm), "2+2")
    assert any(e.type is EventType.THOUGHT and e.data.get("kind") == "retry" for e in events)
    assert done(events)["answer"] == "4"
    assert "malformed" in llm.requests[1]["messages"][-1]["content"]


async def test_bad_arguments_and_unknown_tools_are_fed_back(settings):
    llm = FakeLLM([call("python_calculator"), call("teleport", to="mars"),
                   call("python_calculator", expression="1+1"), say("2")])
    events = await run(AetherAgent(settings, llm=llm), "1+1")
    results = [e.data for e in events if e.type is EventType.TOOL_RESULT]
    assert "Missing required argument 'expression'" in results[0]["output"]
    assert "no tool named 'teleport'" in results[1]["output"]
    assert results[2]["ok"]


async def test_approval_approved_runs_the_tool(settings):
    target = workspace().resolve("notes/old.md")
    target.write_text("bye")
    llm = FakeLLM([call("delete_file", path="notes/old.md"), say("Deleted.")])
    events = await run(AetherAgent(settings, llm=llm), "delete notes/old.md", approve=True)
    seq = types(events)
    assert seq.index(EventType.APPROVAL_REQUIRED) < seq.index(EventType.APPROVAL_RESOLVED) \
        < seq.index(EventType.TOOL_RESULT)
    req = next(e for e in events if e.type is EventType.APPROVAL_REQUIRED).data
    assert "can't be undone" in req["summary"] and req["args"] == {"path": "notes/old.md"}
    assert not target.exists()


async def test_approval_denied_does_not_run(settings):
    target = workspace().resolve("notes/keep.md")
    target.write_text("important")
    llm = FakeLLM([call("delete_file", path="notes/keep.md"), say("Okay, I left it alone.")])
    events = await run(AetherAgent(settings, llm=llm), "delete notes/keep.md", approve=False)
    result = next(e for e in events if e.type is EventType.TOOL_RESULT).data
    assert result["denied"] and not result["ok"]
    assert target.exists()
    assert "DENIED" in llm.requests[1]["messages"][-1]["content"]


async def test_overwrite_needs_approval_but_create_does_not(settings):
    llm = FakeLLM([call("write_note", title="Plan", content="v1"), say("Saved."),
                   call("write_note", title="Plan", content="v2", mode="overwrite"), say("Updated.")])
    agent = AetherAgent(settings, llm=llm)
    first = await run(agent, "save plan")
    assert EventType.APPROVAL_REQUIRED not in types(first)
    second = await run(agent, "overwrite plan", approve=True)
    assert EventType.APPROVAL_REQUIRED in types(second)
    assert "v2" in workspace().resolve("notes/plan.md").read_text()


async def test_path_traversal_is_refused_by_tools(settings):
    llm = FakeLLM([call("read_file", path="../../etc/passwd"), say("I can't read that.")])
    events = await run(AetherAgent(settings, llm=llm), "read /etc/passwd")
    result = next(e for e in events if e.type is EventType.TOOL_RESULT).data
    assert not result["ok"] and "outside the workspace" in result["output"]


async def test_step_limit_forces_a_final_answer(settings):
    settings_small = settings.model_copy(update={"max_steps": 2})
    llm = FakeLLM([call("python_calculator", expression="1"), call("python_calculator", expression="2"),
                   say("Best answer so far.")])
    events = await run(AetherAgent(settings_small, llm=llm), "loop forever")
    assert done(events)["answer"] == "Best answer so far."
    assert llm.requests[-1]["tools"] is None  # the final call had tools switched off


async def test_ollama_errors_become_error_events(settings):
    llm = FakeLLM([OllamaError("Model 'x' isn't installed.", hint="Run: ollama pull x")])
    events = await run(AetherAgent(settings, llm=llm), "hi")
    err = next(e for e in events if e.type is EventType.ERROR).data
    assert err["hint"] == "Run: ollama pull x"
    assert EventType.DONE not in types(events)


async def test_history_carries_between_goals(settings):
    llm = FakeLLM([say("Nice to meet you, Piyush."), say("Your name is Piyush.")])
    agent, session = AetherAgent(settings, llm=llm), Session()
    await run(agent, "I'm Piyush", session)
    await run(agent, "what's my name?", session)
    contents = [m["content"] for m in llm.requests[1]["messages"]]
    assert "I'm Piyush" in contents and "Nice to meet you, Piyush." in contents


async def test_memory_provider_is_injected_into_system_prompt(settings):
    async def memory(goal: str) -> str:
        return "- The user studies BCA AI & Data Science."

    llm = FakeLLM([say("ok")])
    await run(AetherAgent(settings, llm=llm, memory_provider=memory), "what do I study?")
    assert "BCA AI & Data Science" in llm.requests[0]["messages"][0]["content"]


async def test_precheck_rejects_missing_file_before_asking(settings):
    workspace().resolve("notes/python-release.md").write_text("x")
    llm = FakeLLM([call("delete_file", path="notes/Python release.md"), say("It isn't there.")])
    events = await run(AetherAgent(settings, llm=llm), "delete it", approve=True)
    assert EventType.APPROVAL_REQUIRED not in types(events)
    result = next(e for e in events if e.type is EventType.TOOL_RESULT).data
    assert "doesn't exist" in result["output"] and "python-release.md" in result["output"]


async def test_unfixed_tool_failure_is_sent_back_once(settings, mem):
    llm = FakeLLM([call("add_task", title="DBMS exam", due="whenever", kind="exam"),
                   say("Your exam has been tracked!"),  # false claim → nudged
                   call("add_task", title="DBMS exam", due="tomorrow", kind="exam"),
                   say("Added your DBMS exam for tomorrow.")])
    events = await run(AetherAgent(settings, llm=llm), "track my DBMS exam tomorrow")
    assert any(e.type is EventType.THOUGHT and e.data.get("kind") == "retry" for e in events)
    assert "failed" in llm.requests[2]["messages"][-1]["content"]
    assert done(events)["answer"] == "Added your DBMS exam for tomorrow."
    assert mem.tasks.list()[0]["title"] == "DBMS exam"


async def test_announced_but_uncalled_tool_is_nudged(settings, mem):
    llm = FakeLLM([say("Let me check. I'll use manage_timetable to list your classes."),
                   call("manage_timetable", action="list"), say("You have no classes yet.")])
    events = await run(AetherAgent(settings, llm=llm), "what classes do I have?")
    assert "didn't call it" in llm.requests[1]["messages"][-1]["content"]
    assert done(events)["answer"] == "You have no classes yet."


async def test_denied_tools_are_not_nudged(settings):
    workspace().resolve("notes/k.md").write_text("k")
    llm = FakeLLM([call("delete_file", path="notes/k.md"), say("Okay, I kept it.")])
    events = await run(AetherAgent(settings, llm=llm), "delete notes/k.md", approve=False)
    assert not any(e.data.get("kind") == "retry" for e in events if e.type is EventType.THOUGHT)
    assert done(events)["answer"] == "Okay, I kept it."


async def test_runaway_generation_is_cut_off_and_retried(settings):
    llm = FakeLLM([{"content": "I will I will I will I will", "done_reason": "length"},
                   say("Short answer: 4.")])
    events = await run(AetherAgent(settings, llm=llm), "2+2?")
    assert any(e.type is EventType.THOUGHT and "too long" in e.data["text"] for e in events)
    assert "cut off" in llm.requests[1]["messages"][-1]["content"]
    assert done(events)["answer"] == "Short answer: 4."
    assert llm.requests and True


async def test_output_cap_is_sent_to_ollama(settings):
    agent = AetherAgent(settings, llm=FakeLLM([say("ok")]))
    assert agent._options["num_predict"] == settings.max_output_tokens == 2048


async def test_stops_waiting_after_tool_call_when_model_keeps_going(settings):
    import time as _time
    fast = settings.model_copy(update={"tool_call_grace_s": 0.2})
    llm = FakeLLM([call("python_calculator", expression="6*7") | {"hang": True}, say("42")])
    started = _time.perf_counter()
    events = await run(AetherAgent(fast, llm=llm), "6*7?")
    assert _time.perf_counter() - started < 2
    assert done(events)["answer"] == "42"
