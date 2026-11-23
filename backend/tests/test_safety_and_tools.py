"""Parser, calculator, workspace jail, registry validation and approval broker."""
from __future__ import annotations

import asyncio
import os

import pytest

from aether.engine.safety import ApprovalBroker
from aether.llm.parsing import (ToolCallParseError, extract_tool_calls, looks_like_failed_tool_call,
                                parse_arguments)
from aether.tools import REGISTRY, Risk, ToolError
from aether.tools.calculator import calculate
from aether.tools.notes import write_note
from aether.workspace import WorkspaceError

KNOWN = REGISTRY.names


# --------------------------------------------------------------- tool-call parsing
@pytest.mark.parametrize("text", [
    'Let me check.\n```json\n{"name": "web_search", "arguments": {"query": "rust 2.0",}}\n```',
    "<tool_call>{'name': 'web_search', 'arguments': {'query': 'rust 2.0'}}</tool_call>",
    '{"function": {"name": "web_search", "parameters": {"query": "rust 2.0"}}}',
    '{"tool": "web_search", "args": {"query": "rust 2.0"}}',
    '{"tool_calls": [{"name": "web_search", "arguments": "{\\"query\\": \\"rust 2.0\\"}"}]}',
])
def test_extracts_tool_calls_written_as_text(text):
    calls, remaining = extract_tool_calls(text, KNOWN)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "web_search"
    assert parse_arguments(calls[0]["function"]["arguments"]) == {"query": "rust 2.0"}
    assert "web_search" not in remaining


def test_ignores_json_that_is_not_a_known_tool():
    calls, remaining = extract_tool_calls('Example: {"name": "rm_everything", "arguments": {}}', KNOWN)
    assert calls == [] and "rm_everything" in remaining


def test_detects_unrecoverable_tool_call():
    assert looks_like_failed_tool_call('{"name": "web_search", "arguments": {query: oops', KNOWN)
    assert not looks_like_failed_tool_call("The capital of France is Paris.", KNOWN)


def test_parse_arguments_repairs_and_rejects():
    assert parse_arguments('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}
    assert parse_arguments("{'a': True}") == {"a": True}
    with pytest.raises(ToolCallParseError):
        parse_arguments("not json at all")


# ------------------------------------------------------------------- calculator
@pytest.mark.parametrize("expr, expected", [
    ("2 + 2 * 3", "8"), ("10 / 4", "2.5"), ("sum(x**2 for x in range(1, 11))", "385"),
    ("data = [2, 4, 4, 4, 5, 5, 7, 9]; pstdev(data)", "2"), ("factorial(10)", "3628800"),
    ("round(sqrt(2), 4)", "1.4142"), ("[x for x in range(10) if x % 3 == 0]", "[0, 3, 6, 9]"),
    ("max([3, 9, 2]) - min([3, 9, 2])", "7"),
])
def test_calculator_math(expr, expected):
    assert calculate(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('ls')", "().__class__.__bases__", "open('/etc/passwd')",
    "9 ** 9 ** 9", "[0] * 10 ** 9", "pow(9, 10 ** 9)", "factorial(10 ** 7)",
    "sum(a * b for a in range(100000) for b in range(100000))", "import os", "lambda: 1",
    "exec('1')", "1 / 0",
])
def test_calculator_blocks_unsafe_or_huge(expr):
    with pytest.raises(ToolError):
        calculate(expr)


# ------------------------------------------------------------------ workspace jail
def test_workspace_blocks_escapes(fresh_workspace, tmp_path):
    ws = fresh_workspace
    for bad in ["../secret.txt", "notes/../../x", "/etc/passwd", str(tmp_path / "outside.txt"), ""]:
        with pytest.raises(WorkspaceError):
            ws.resolve(bad)
    assert ws.resolve("notes/a.md") == ws.root / "notes" / "a.md"


def test_workspace_blocks_symlink_escape(fresh_workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, fresh_workspace.root / "link")
    with pytest.raises(WorkspaceError):
        fresh_workspace.resolve("link/file.txt")


# ------------------------------------------------------------------------ registry
def test_schema_generation_and_enums():
    schema = REGISTRY.get("write_note").schema()["function"]["parameters"]
    assert schema["required"] == ["title", "content"]
    assert schema["properties"]["mode"]["enum"] == ["create", "append", "overwrite"]
    assert REGISTRY.get("web_search").parameters["properties"]["max_results"]["type"] == "integer"


def test_validate_coerces_and_explains():
    spec = REGISTRY.get("web_search")
    clean, errors = REGISTRY.validate(spec, {"query": "x", "max_results": "3", "junk": 1})
    assert clean == {"query": "x", "max_results": 3} and errors == []
    _, errors = REGISTRY.validate(spec, {"max_results": "lots"})
    assert any("integer" in e for e in errors) and any("query" in e for e in errors)
    _, errors = REGISTRY.validate(REGISTRY.get("write_note"),
                                  {"title": "a", "content": "b", "mode": "explode"})
    assert any("must be one of" in e for e in errors)


def test_risk_levels():
    assert REGISTRY.get("delete_file").risk is Risk.REQUIRES_APPROVAL
    assert REGISTRY.get("run_shell_command").risk is Risk.REQUIRES_APPROVAL
    assert REGISTRY.get("python_calculator").risk is Risk.SAFE
    note = REGISTRY.get("write_note")
    assert note.risk_for({"title": "fresh", "content": "x", "mode": "overwrite"}) is Risk.SAFE
    write_note("fresh", "hello")
    assert note.risk_for({"title": "fresh", "content": "x", "mode": "overwrite"}) is Risk.REQUIRES_APPROVAL
    assert note.risk_for({"title": "fresh", "content": "x", "mode": "append"}) is Risk.SAFE


def test_shell_risk_summary_flags_dangerous_commands():
    summary = REGISTRY.get("run_shell_command").summarize_risk({"command": "sudo rm -rf /tmp/x"})
    assert "administrator" in summary and "force-deletes" in summary


async def test_tool_errors_and_timeouts_never_raise():
    spec = REGISTRY.get("python_calculator")
    result = await REGISTRY.execute(spec, {"expression": "1/0"}, timeout_s=5, max_output_chars=100)
    assert not result.ok and "Division by zero" in result.output


# ------------------------------------------------------------------ approval broker
async def test_broker_resolves_and_times_out():
    broker = ApprovalBroker(timeout_s=0.05)
    req = broker.open(run_id="r", tool="delete_file", args={}, summary="")
    assert broker.resolve(req.id, True)
    assert (await broker.wait(req.id)).approved
    assert not broker.resolve(req.id, True)  # already answered

    late = broker.open(run_id="r", tool="delete_file", args={}, summary="")
    decision = await broker.wait(late.id)
    assert not decision.approved and "No answer" in decision.reason


async def test_broker_cancel_run_denies_pending():
    broker = ApprovalBroker()
    req = broker.open(run_id="run1", tool="delete_file", args={}, summary="")
    waiter = asyncio.create_task(broker.wait(req.id))
    await asyncio.sleep(0)
    broker.cancel_run("run1")
    assert not (await waiter).approved
