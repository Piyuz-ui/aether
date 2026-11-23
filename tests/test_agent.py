"""Tests run without Ollama: a fake LLM scripts the model's replies."""
import os
import tempfile

# Keep test data out of the real data/, documents/ and workspace/ folders.
_tmp = tempfile.mkdtemp()
for var in ("AGENT_DATA_DIR", "AGENT_DOCS_DIR", "AGENT_WORKSPACE_DIR", "AGENT_NOTES_DIR"):
    os.environ[var] = os.path.join(_tmp, var.lower())

from agent import Agent, Settings  # noqa: E402
from agent.llm import OllamaError  # noqa: E402
from agent.tools import REGISTRY  # noqa: E402


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.model = "fake"
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append(list(messages))
        return self.replies.pop(0)

    def embed(self, texts, model):
        raise OllamaError("no embeddings in tests")


def call(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def say(text):
    return {"role": "assistant", "content": text}


def make_agent(replies, confirm=None):
    return Agent(Settings(), llm=FakeLLM(replies), confirm=confirm)


def test_tool_schemas_are_generated():
    schema = REGISTRY["add_task"].schema()["function"]
    assert schema["parameters"]["required"] == ["title"]
    assert schema["parameters"]["properties"]["due"]["type"] == "string"
    assert REGISTRY["read_file"].parameters["properties"]["start_line"]["type"] == "integer"


def test_plain_reply():
    agent = make_agent([say("Hello!")])
    assert agent.chat("hi") == "Hello!"


def test_tool_loop_adds_and_lists_tasks():
    agent = make_agent([
        call("add_task", title="DBMS assignment", due="2026-12-01", kind="deadline"),
        call("list_tasks"),
        say("Added it."),
    ])
    assert agent.chat("remind me about DBMS assignment on 1 Dec") == "Added it."
    tool_msgs = [m for m in agent.turns[-1] if m["role"] == "tool"]
    assert "Added #" in tool_msgs[0]["content"]
    assert "DBMS assignment" in tool_msgs[1]["content"]


def test_string_arguments_are_coerced():
    agent = make_agent([call("complete_task", task_id="999"), say("ok")])
    agent.chat("done")
    assert "No task #999" in agent.turns[-1][2]["content"]


def test_dangerous_tool_needs_confirmation():
    agent = make_agent([call("write_file", path="x.txt", content="hi"), say("ok")],
                       confirm=lambda name, args: False)
    agent.chat("write a file")
    assert "declined" in agent.turns[-1][2]["content"]


def test_memory_keyword_recall():
    agent = make_agent([call("remember", fact="The user studies BCA AI & Data Science."),
                        say("Noted.")])
    agent.chat("I study BCA AI and Data Science")
    hits = agent.memory.recall("what does the user study?")
    assert hits and "BCA" in hits[0]["text"]


def test_unknown_tool_does_not_crash():
    agent = make_agent([call("fly_to_moon"), say("Can't do that.")])
    assert agent.chat("fly") == "Can't do that."
    assert "no tool named" in agent.turns[-1][2]["content"]


def test_history_is_carried_between_turns():
    llm = FakeLLM([say("one"), say("two")])
    agent = Agent(Settings(), llm=llm)
    agent.chat("first")
    agent.chat("second")
    contents = [m["content"] for m in llm.seen[-1]]
    assert "first" in contents and "one" in contents


def test_save_note_writes_markdown_file_and_is_reported():
    agent = make_agent([call("save_note", title="Paging", body="Paging splits memory."),
                        say("Saved.")])
    agent.chat("make notes on paging")
    assert [n["title"] for n in agent.last_notes] == ["Paging"]
    from agent.config import NOTES_DIR
    files = list(NOTES_DIR.glob("*-paging.md"))
    assert files and "Paging splits memory." in files[0].read_text()


def test_notes_written_in_reply_are_saved_automatically():
    notes = "# Deadlock\n\n" + "Four conditions: mutual exclusion, hold and wait... " * 10
    agent = make_agent([say(notes + "\nThe notes have been saved in the following location: /fake")])
    reply = agent.chat("make notes of my OS pdf")
    assert agent.last_notes and agent.last_notes[0]["title"] == "Deadlock"
    assert "/fake" not in reply and "Saved as note #" in reply


def test_attached_document_text_goes_into_the_message():
    from agent.config import DOCS_DIR
    doc = DOCS_DIR / "paging.md"
    doc.write_text("Paging divides memory into fixed-size frames of 4 KB.")
    llm = FakeLLM([say("4 KB (p. 1)"), say("follow-up")])
    agent = Agent(Settings(), llm=llm)
    agent.chat("what is the frame size?", attachments=[doc])
    user_msg = llm.seen[0][-1]["content"]
    assert "fixed-size frames of 4 KB" in user_msg and "[Attached document: paging.md" in user_msg
    assert "FOCUS" in llm.seen[0][0]["content"] and "paging.md" in llm.seen[0][0]["content"]
    agent.chat("and why?")  # follow-ups keep the focus and still see the document
    assert "paging.md" in llm.seen[1][0]["content"]
    assert any("fixed-size frames" in m["content"] for m in llm.seen[1])
    agent.reset()
    assert agent.active_docs == []


def test_long_attachment_is_left_to_search_tools():
    from agent.config import DOCS_DIR
    doc = DOCS_DIR / "big.txt"
    doc.write_text("lorem ipsum " * 5000)
    llm = FakeLLM([say("ok")])
    Agent(Settings(), llm=llm).chat("summarise", attachments=[doc])
    user_msg = llm.seen[0][-1]["content"]
    assert "too long" in user_msg and "lorem ipsum lorem" not in user_msg


def test_turn_stats_are_recorded():
    from agent.metrics import CallStats, Monitor
    llm = FakeLLM([call("get_datetime"), say("It's Monday.")])
    stats = iter([CallStats(prompt_tokens=1000, output_tokens=20, total_s=1.0, gen_s=0.5),
                  CallStats(prompt_tokens=1100, cached_tokens=1000, output_tokens=40,
                            total_s=2.0, gen_s=1.5)])
    real_chat = llm.chat

    def chat(messages, tools=None):
        llm.last_stats = next(stats)
        return real_chat(messages, tools)

    llm.chat = chat
    agent = Agent(Settings(), llm=llm)
    agent.monitor = Monitor(log_path=agent.monitor.log_path.with_name("test_metrics.jsonl"))
    agent.chat("what day is it?")
    s = agent.last_stats
    assert (s.prompt_tokens, s.output_tokens, s.context_used) == (2100, 60, 1140)
    assert s.tokens_per_s == 30.0 and [n for n, _ in s.tools] == ["get_datetime"]
    assert "2,100 in / 60 out tokens" in s.summary()
    assert agent.monitor.session()["total_tokens"] == 2160
    assert agent.monitor.history()[-1]["context_used"] == 1140
