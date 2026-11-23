"""Explicit long-term memory tools ("remember that I…", "what do you know about me?")."""
from __future__ import annotations

from typing import Any, Literal

from .context import memory
from .registry import Risk, ToolError, tool


@tool(description=(
    "Save ONE lasting fact about the user to long-term memory (who they are, their course, "
    "goals, preferences). NOT for deadlines, exams or tasks: use manage_tasks for those."),
    params={"fact": "One sentence starting with 'The user', e.g. 'The user prefers short answers.'",
            "category": "What kind of fact it is"},
    category="memory", icon="brain")
async def remember(fact: str,
                   category: Literal["profile", "preference", "goal", "academic", "general"] = "general"
                   ) -> str:
    saved, created = await memory().facts.add(fact, category, source="user")
    verb = "Remembered" if created else "Updated an existing memory"
    return f"{verb} (#{saved['id']}): {saved['text']}"


@tool(description="Search long-term memory for what you know about the user.",
      params={"query": "What to look up, e.g. 'study goals'. Use 'everything' to list all."},
      category="memory", icon="brain")
async def recall_memory(query: str) -> str:
    facts = memory().facts
    hits = facts.all() if query.strip().lower() in {"everything", "all", "*"} else \
        await facts.search(query, k=10, min_similarity=0.3)
    if not hits:
        return "Nothing relevant in long-term memory."
    return "\n".join(f"#{f['id']} [{f['category']}] {f['text']}" for f in hits[:30])


def _forget_summary(args: dict[str, Any]) -> str:
    f = memory().facts.get(int(args.get("fact_id") or 0))
    return f"Permanently forget: \"{f['text'] if f else args.get('fact_id')}\"."


def _forget_precheck(args: dict[str, Any]) -> None:
    fid = int(args.get("fact_id") or 0)
    if not memory().facts.get(fid):
        raise ToolError(f"Memory #{fid} doesn't exist. Use recall_memory to find the right number.")


@tool(description="Delete one fact from long-term memory (asks the user first).",
      params={"fact_id": "The memory number from recall_memory"},
      risk=Risk.REQUIRES_APPROVAL, category="memory", icon="brain",
      risk_summary=_forget_summary, precheck=_forget_precheck)
def forget(fact_id: int) -> str:
    return f"Forgot memory #{fact_id}." if memory().facts.delete(fact_id) else f"No memory #{fact_id}."
