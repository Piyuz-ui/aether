"""Chat titles: a short name for what a conversation is about, written by the model.

A chat is first titled with the start of your first message, then named by the model after the
first reply and again after the third (when the topic is clearer). A title you type yourself is
never overwritten.
"""
from __future__ import annotations

import re
from typing import Any

PROMPT = ("Write a short title (2 to 5 words) for this conversation, like a chat app would, e.g. "
          "'Fixing a Python import error', 'Goa trip budget', 'Essay on climate policy'. Use the user's language. "
          "No quotes, no emoji, no ending punctuation. Reply with the title only.")


def clean_title(raw: str) -> str | None:
    """The model's reply → a tidy title, or None if it isn't usable."""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(title|chat title)\s*[:\-]\s*", "", line, flags=re.I)
    line = line.strip(" \"'`*#_“”‘’.!?:;")
    line = re.sub(r"\s+", " ", line)
    if not line or len(line) < 3:
        return None
    if len(line.split(" ")) > 7:
        return None  # a sentence, not a title; a clipped one reads worse than keeping the old
    return line[:60].rstrip(" ,;:-")


def _excerpt(turns: list[list[dict[str, Any]]], limit: int = 2400) -> str:
    parts: list[str] = []
    for turn in turns[:3]:
        for m in turn:
            if m.get("role") == "user":
                parts.append(f"User: {m.get('content', '')[:600]}")
            elif m.get("role") == "assistant" and not m.get("tool_calls"):
                parts.append(f"Assistant: {m.get('content', '')[:500]}")
    return "\n".join(parts)[:limit]


async def generate_title(llm: Any, model: str, turns: list[list[dict[str, Any]]]) -> str | None:
    """Ask the model for a title. Returns None if it didn't give a usable one."""
    # The instruction comes after the conversation: small models then title it instead of
    # summarising it.
    messages = [{"role": "system", "content": "You name chat conversations."},
                {"role": "user", "content": f"<conversation>\n{_excerpt(turns)}\n</conversation>\n\n{PROMPT}"}]
    out: list[str] = []
    async for d in llm.chat_stream(model=model, messages=messages, tools=None,
                                   options={"temperature": 0.1, "num_predict": 32}, think=False):
        if d.content:
            out.append(d.content)
        if sum(len(x) for x in out) > 300:  # a title never needs this much
            break
    return clean_title("".join(out))
