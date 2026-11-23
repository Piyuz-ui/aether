"""Tests run without Ollama: a fake LLM scripts the model's replies."""
import os
import tempfile

# Keep test data out of the real data/, documents/ and workspace/ folders.
_tmp = tempfile.mkdtemp()
for var in ("AGENT_DATA_DIR", "AGENT_DOCS_DIR", "AGENT_WORKSPACE_DIR"):
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
