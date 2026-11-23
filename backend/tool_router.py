"""Dynamic tool router: offer a small local model only the tools a request needs.

Every tool belongs to a pack (core · research · code_data · mcp), carries keyword tags and a
risk level (SAFE · WRITE · HIGH_RISK). For each request the router scores tools by tag hits,
name hits and description overlap, adds a few companions (e.g. analyze_csv → python_sandbox) and
returns the top 6–8. 4B–9B models pick far better from 7 tools than from 20+.

Cloud models still see every tool: they handle long tool lists well.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Level(StrEnum):
    SAFE = "SAFE"          # reads or computes
    WRITE = "WRITE"        # changes your data (notes, tasks, memory, files it creates)
    HIGH_RISK = "HIGH_RISK"  # runs code or deletes; always asks first


@dataclass(frozen=True, slots=True)
class ToolMeta:
    pack: str
    level: Level
    tags: tuple[str, ...]


def _m(pack: str, level: Level, tags: str) -> ToolMeta:
    return ToolMeta(pack, level, tuple(tags.split()))


CATALOG: dict[str, ToolMeta] = {
    # ---- core: your stuff
    "recall_memory": _m("core", Level.SAFE, "remember recall know about me my name prefer preference profile who"),
    "remember": _m("core", Level.WRITE, "remember note that save fact my prefer preference call me always"),
    "forget": _m("core", Level.WRITE, "forget remove memory delete fact"),
    "add_task": _m("core", Level.WRITE, "task todo deadline due exam test assignment homework submit remind reminder tomorrow"),
    "manage_tasks": _m("core", Level.WRITE, "task tasks todo deadline due done complete list upcoming week exam assignment"),
    "add_class": _m("core", Level.WRITE, "class lecture timetable schedule monday tuesday wednesday thursday friday room lab"),
    "manage_timetable": _m("core", Level.WRITE, "timetable schedule class classes lecture week today tomorrow"),
    "write_note": _m("core", Level.WRITE, "note notes write save summary summarise summarize markdown file draft"),
    "read_file": _m("core", Level.SAFE, "read open file note show content"),
    "list_files": _m("core", Level.SAFE, "files list folder workspace notes saved"),
    "delete_file": _m("core", Level.HIGH_RISK, "delete remove file erase"),
    "create_pdf": _m("core", Level.WRITE, "pdf export document print"),
    "make_quiz": _m("core", Level.WRITE, "quiz test me mcq flashcard flashcards practice questions revise"),
    "python_calculator": _m("core", Level.SAFE, "calculate compute math sum percent percentage average convert how much times divide multiply"),
    # ---- research: documents and the web
    "search_knowledge_base": _m("research", Level.SAFE, "document documents pdf slide slides lecture notes uploaded chapter course syllabus according my file"),
    "list_documents": _m("research", Level.SAFE, "document documents uploaded files vault library which"),
    "web_search": _m("research", Level.SAFE, "search web internet google latest news current today price who what when online find"),
    "read_webpage": _m("research", Level.SAFE, "url link website page http https read article"),
    # ---- code_data: run code, analyse data, draw charts
    "python_sandbox": _m("code_data", Level.HIGH_RISK, "chart plot graph visualise visualize histogram bar line scatter pie python code numpy pandas matplotlib simulate data analyse analyze"),
    "analyze_csv": _m("code_data", Level.SAFE, "csv excel xlsx spreadsheet data dataset table column columns rows statistics stats analyse analyze"),
    "run_python": _m("code_data", Level.HIGH_RISK, "python script code run execute"),
    "run_shell_command": _m("code_data", Level.HIGH_RISK, "shell terminal command bash zsh install git ls run"),
}

# Tools that usually go together: picking one brings the other along.
COMPANIONS: dict[str, tuple[str, ...]] = {
    "analyze_csv": ("python_sandbox",),
    "python_sandbox": ("analyze_csv",),
    "web_search": ("read_webpage",),
    "read_webpage": ("web_search",),
    "list_documents": ("search_knowledge_base",),
    "add_task": ("manage_tasks",),
    "manage_tasks": ("add_task",),
    "add_class": ("manage_timetable",),
    "manage_timetable": ("add_class",),
    "read_file": ("list_files",),
    "make_quiz": ("search_knowledge_base",),
}
# Offered when nothing in the request points anywhere in particular (small talk, vague asks).
DEFAULTS = ("search_knowledge_base", "recall_memory", "web_search", "python_calculator",
            "add_task", "write_note")

_STOP = set("a an the and or but to of in on at for with is are was be it this that i me my you your "
            "can could would please what how do does did will just about from by as".split())


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOP or len(w) < 2:
            continue
        out.append(w)
        for suf in ("ing", "ed", "es", "s"):  # cheap stemming: "plots" → "plot", "charts" → "chart"
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                out.append(w[: -len(suf)])
                break
    return out


def meta_for(name: str, spec: Any = None) -> ToolMeta:
    """Catalogue entry, or one derived from the tool itself (MCP and future tools)."""
    if name in CATALOG:
        return CATALOG[name]
    risky = spec is not None and getattr(getattr(spec, "risk", None), "value", "") == "requires_approval"
    pack = "mcp" if getattr(spec, "category", "") == "mcp" else "core"
    tags = tuple(dict.fromkeys(_words(name.replace("__", " ").replace("_", " ")
                                      + " " + (getattr(spec, "description", "") or "")[:200])))
    return ToolMeta(pack, Level.HIGH_RISK if risky else Level.SAFE, tags)


def route(prompt: str, specs: list[Any], *, max_tools: int = 8, min_tools: int = 6,
          recent: set[str] | None = None, has_images: bool = False) -> list[str]:
    """Names of the tools to offer for `prompt`, best first. `specs`: the registry's ToolSpecs.
    `recent`: tools used earlier in this chat (follow-ups like "now plot it" need them)."""
    by_name = {s.name: s for s in specs}
    if len(by_name) <= max_tools:
        return list(by_name)
    words = _words(prompt)
    wordset = set(words)
    low = prompt.lower()
    scores: dict[str, float] = {}
    for name, spec in by_name.items():
        meta = meta_for(name, spec)
        tag_hits = sum(1 for t in meta.tags if t in wordset or (" " in t and t in low))
        name_hits = sum(1 for p in name.lower().split("_") if len(p) > 2 and p in wordset)
        desc = set(_words(getattr(spec, "description", "")[:300]))
        overlap = len(wordset & desc)
        score = 3.0 * tag_hits + 2.0 * name_hits + 0.4 * overlap
        if not tag_hits and not name_hits:
            score = min(score, 1.0)  # description overlap alone is weak evidence
        if recent and name in recent:
            score += 2.5
        if has_images and name in ("add_class", "manage_timetable", "add_task"):
            score += 3.0  # photos are mostly timetables, notices and assignment sheets
        if re.search(r"\.(csv|tsv|xlsx)\b", low) and name in ("analyze_csv", "python_sandbox"):
            score += 6.0
        if re.search(r"https?://", low) and name == "read_webpage":
            score += 6.0
        if score > 0:
            scores[name] = score

    # Only real matches make the cut; the defaults fill any remaining room.
    ranked = sorted((n for n in scores if scores[n] >= 2.0), key=lambda n: (-scores[n], n))
    chosen: list[str] = []
    for name in ranked:
        if len(chosen) >= max_tools:
            break
        if name not in chosen:
            chosen.append(name)
        for mate in COMPANIONS.get(name, ()):
            if mate in by_name and mate not in chosen and len(chosen) < max_tools and scores.get(name, 0) >= 3:
                chosen.append(mate)
    for name in DEFAULTS:  # top up with sensible all-rounders
        if len(chosen) >= min_tools:
            break
        if name in by_name and name not in chosen:
            chosen.append(name)
    return chosen[:max_tools]


def describe(specs: list[Any]) -> list[dict[str, Any]]:
    """Every tool with its pack, level and tags (for /api/tools and debugging)."""
    return [{"name": s.name, "pack": (m := meta_for(s.name, s)).pack, "level": m.level.value,
             "tags": list(m.tags[:12])} for s in specs]
