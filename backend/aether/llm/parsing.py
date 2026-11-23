"""Robust tool-call handling for small local models.

7B models don't always use the native tool-calling channel. They may write the call as
text: in a ```json fence, inside <tool_call> tags, as bare JSON, with single quotes, or
with trailing commas. This module recovers those calls instead of failing.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"```(?:json|tool_call|tool|javascript|js)?\s*(.*?)```",
                      re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<(tool_call|function_call|tool)>\s*(.*?)\s*</\1>",
                    re.DOTALL | re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class ToolCallParseError(ValueError):
    pass


def strip_thinking(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Turn whatever the model sent as arguments into a dict, repairing common damage."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ToolCallParseError(f"Arguments must be an object, got {type(raw).__name__}.")
    obj = loads_lenient(raw)
    if not isinstance(obj, dict):
        raise ToolCallParseError("Arguments must be a JSON object.")
    return obj


def loads_lenient(text: str) -> Any:
    """json.loads, then progressively more forgiving repairs."""
    text = text.strip()
    fence = FENCE_RE.fullmatch(text)
    if fence:
        text = fence.group(1).strip()
    attempts = [text, TRAILING_COMMA_RE.sub(r"\1", text)]
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
    # Python-style dicts: single quotes, True/False/None.
    try:
        return ast.literal_eval(TRAILING_COMMA_RE.sub(r"\1", text))
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        pass
    raise ToolCallParseError("Couldn't parse the tool arguments as JSON.")


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} span in text, respecting strings and escapes."""
    spans, depth, start, in_str, quote, escape = [], 0, -1, False, "", False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            if depth > 0:
                in_str, quote = True, ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append(text[start:i + 1])
    return spans


def _normalize(obj: Any, known: set[str]) -> list[dict[str, Any]]:
    """Accept the many shapes models use for a tool call."""
    if isinstance(obj, list):
        return [c for item in obj for c in _normalize(item, known)]
    if not isinstance(obj, dict):
        return []
    if "tool_calls" in obj:
        return _normalize(obj["tool_calls"], known)
    if isinstance(obj.get("function"), dict):
        return _normalize(obj["function"], known)
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
    if not isinstance(name, str) or name not in known:
        return []
    args = next((obj[k] for k in ("arguments", "parameters", "args", "input") if k in obj), {})
    return [{"function": {"name": name, "arguments": args}}]


def extract_tool_calls(text: str, known_tools: set[str]) -> tuple[list[dict[str, Any]], str]:
    """Find tool calls written as text. Returns (calls, the text with those calls removed)."""
    if not text or not any(name in text for name in known_tools):
        return [], text
    calls: list[dict[str, Any]] = []
    remaining = text
    candidates: list[str] = [m.group(2) for m in TAG_RE.finditer(text)]
    candidates += [m.group(1) for m in FENCE_RE.finditer(text)]
    candidates += _balanced_objects(text)
    seen: set[str] = set()
    for cand in candidates:
        key = cand.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            obj = loads_lenient(key)
        except ToolCallParseError:
            continue
        found = _normalize(obj, known_tools)
        if found:
            calls.extend(found)
            remaining = remaining.replace(cand, "")
    if calls:
        remaining = TAG_RE.sub("", remaining)
        remaining = re.sub(r"```\w*\s*```", "", remaining)
        # Drop duplicates (the same call can appear both fenced and bare).
        unique, keys = [], set()
        for c in calls:
            k = json.dumps(c, sort_keys=True, default=str)
            if k not in keys:
                keys.add(k)
                unique.append(c)
        calls = unique
    return calls, remaining.strip()


def looks_like_failed_tool_call(text: str, known_tools: set[str]) -> bool:
    """True when the model clearly tried to call a tool but the JSON was unrecoverable."""
    if not text:
        return False
    mentions_tool = any(re.search(rf'["\']?{re.escape(n)}["\']?\s*[,(:]', text) or
                        f'"{n}"' in text for n in known_tools)
    return mentions_tool and ("{" in text or "<tool_call>" in text.lower())
