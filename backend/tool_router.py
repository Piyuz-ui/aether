"""Dynamic tool router: offer a small local model only the tools a request needs.

Tools are grouped into six packs:

    system_mac     your Mac: shell commands (and, from Wave 2, apps, calendar, clipboard…)
    knowledge_rag  your documents and files: ingest, search, read, write, quizzes
    code_data      Python sandbox, charts, datasets, calculator
    research_web   web search, pages, YouTube transcripts, arXiv & Wikipedia
    productivity   tasks, timetable, long-term memory
    mcp_bridge     tools from MCP servers

Every tool has tags and a risk level (SAFE · WRITE · HIGH_RISK). For each request the router
scores tools by tag hits, name hits and, when the local embedding model is running, meaning
(cosine similarity with nomic-embed-text). Pinned packs come first, switched-off packs never
appear, and the top 6–8 are offered, plus `list_available_capabilities` so the agent can pull in
a whole pack mid-task. Cloud models still see every enabled tool.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Level(StrEnum):
    SAFE = "SAFE"            # reads or computes
    WRITE = "WRITE"          # changes your data (notes, tasks, memory, the vault)
    HIGH_RISK = "HIGH_RISK"  # runs code or deletes; always asks first


@dataclass(frozen=True, slots=True)
class PackInfo:
    id: str
    label: str
    description: str


PACKS: dict[str, PackInfo] = {p.id: p for p in [
    PackInfo("system_mac", "Mac control", "Shell commands on this Mac (Wave 2 adds apps, Calendar, Reminders, clipboard, Spotlight)."),
    PackInfo("knowledge_rag", "Knowledge & files", "Ingest and search your documents, read and write notes and files, make quizzes."),
    PackInfo("code_data", "Code & data", "A Python sandbox with charts, dataset analysis and a calculator."),
    PackInfo("research_web", "Research & web", "Web search, reading pages, YouTube transcripts, arXiv papers and Wikipedia."),
    PackInfo("productivity", "Productivity", "Tasks, deadlines, your class timetable and long-term memory."),
    PackInfo("mcp_bridge", "MCP servers", "Tools from the MCP servers you've connected."),
]}

META_TOOL = "list_available_capabilities"


@dataclass(frozen=True, slots=True)
class ToolMeta:
    pack: str
    level: Level
    tags: tuple[str, ...]


def _m(pack: str, level: Level, tags: str) -> ToolMeta:
    return ToolMeta(pack, level, tuple(tags.split()))


CATALOG: dict[str, ToolMeta] = {
    # ---- system_mac
    "run_shell_command": _m("system_mac", Level.HIGH_RISK, "shell terminal command bash zsh install git ls run brew pip mac"),
    # ---- knowledge_rag
    "search_knowledge_base": _m("knowledge_rag", Level.SAFE, "document documents pdf slide slides lecture notes uploaded chapter course syllabus according my file docx"),
    "list_documents": _m("knowledge_rag", Level.SAFE, "document documents uploaded files vault library which"),
    "ingest_any_file": _m("knowledge_rag", Level.WRITE, "ingest index add vault upload docx word excel xlsx code image screenshot learn file"),
    "read_file": _m("knowledge_rag", Level.SAFE, "read open file note show content"),
    "list_files": _m("knowledge_rag", Level.SAFE, "files list folder workspace notes saved"),
    "write_note": _m("knowledge_rag", Level.WRITE, "note notes write save summary summarise summarize markdown file draft"),
    "delete_file": _m("knowledge_rag", Level.HIGH_RISK, "delete remove file erase"),
    "create_pdf": _m("knowledge_rag", Level.WRITE, "pdf export document print"),
    "make_quiz": _m("knowledge_rag", Level.WRITE, "quiz test me mcq flashcard flashcards practice questions revise"),
    # ---- code_data
    "python_sandbox": _m("code_data", Level.HIGH_RISK, "chart plot graph visualise visualize histogram bar line scatter pie python code numpy pandas matplotlib sympy simulate data analyse analyze solve equation symbolic"),
    "analyze_dataset": _m("code_data", Level.SAFE, "csv excel xlsx spreadsheet data dataset table column columns rows statistics stats analyse analyze filter group average"),
    "run_python": _m("code_data", Level.HIGH_RISK, "python script code run execute"),
    "python_calculator": _m("code_data", Level.SAFE, "calculate compute math sum percent percentage average convert how much times divide multiply"),
    # ---- research_web
    "web_search": _m("research_web", Level.SAFE, "search web internet google latest news current today price who what when online find"),
    "read_webpage": _m("research_web", Level.SAFE, "url link website page http https read article"),
    "youtube_transcript": _m("research_web", Level.SAFE, "youtube video transcript watch lecture talk youtu summarise summarize captions"),
    "arxiv_wiki_lookup": _m("research_web", Level.SAFE, "arxiv paper papers research academic wikipedia wiki encyclopedia study scientific definition"),
    # ---- productivity
    "add_task": _m("productivity", Level.WRITE, "task todo deadline due exam test assignment homework submit remind reminder tomorrow"),
    "manage_tasks": _m("productivity", Level.WRITE, "task tasks todo deadline due done complete list upcoming week exam assignment"),
    "add_class": _m("productivity", Level.WRITE, "class lecture timetable schedule monday tuesday wednesday thursday friday room lab"),
    "manage_timetable": _m("productivity", Level.WRITE, "timetable schedule class classes lecture week today tomorrow"),
    "recall_memory": _m("productivity", Level.SAFE, "remember recall know about me my name prefer preference profile who"),
    "remember": _m("productivity", Level.WRITE, "remember note that save fact my prefer preference call me always"),
    "forget": _m("productivity", Level.WRITE, "forget remove memory delete fact"),
    # ---- the meta-tool
    META_TOOL: _m("knowledge_rag", Level.SAFE, "capabilities tools packs what can you do help"),
}

COMPANIONS: dict[str, tuple[str, ...]] = {
    "analyze_dataset": ("python_sandbox",),
    "python_sandbox": ("analyze_dataset",),
    "web_search": ("read_webpage",),
    "read_webpage": ("web_search",),
    "list_documents": ("search_knowledge_base",),
    "ingest_any_file": ("search_knowledge_base",),
    "add_task": ("manage_tasks",),
    "manage_tasks": ("add_task",),
    "add_class": ("manage_timetable",),
    "manage_timetable": ("add_class",),
    "read_file": ("list_files",),
    "make_quiz": ("search_knowledge_base",),
}
DEFAULTS = ("search_knowledge_base", "recall_memory", "web_search", "python_calculator",
            "add_task", "write_note")

_STOP = set("a an the and or but to of in on at for with is are was be it this that i me my you your "
            "can could would please what how do does did will just about from by as".split())

# ---------------------------------------------------------------- pack state
# Which packs are switched off or pinned (Settings → Tool packs). The app loads it from the
# database at startup and saves every change; the CLI uses the defaults (all on, none pinned).
_state: dict[str, set[str]] = {"disabled": set(), "pinned": set()}


def set_state(disabled: set[str] | list[str] | None = None, pinned: set[str] | list[str] | None = None) -> None:
    if disabled is not None:
        _state["disabled"] = {p for p in disabled if p in PACKS}
    if pinned is not None:
        _state["pinned"] = {p for p in pinned if p in PACKS}


def state() -> dict[str, list[str]]:
    return {"disabled": sorted(_state["disabled"]), "pinned": sorted(_state["pinned"])}


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
    pack = "mcp_bridge" if getattr(spec, "category", "") == "mcp" else "knowledge_rag"
    tags = tuple(dict.fromkeys(_words(name.replace("__", " ").replace("_", " ")
                                      + " " + (getattr(spec, "description", "") or "")[:200])))
    # MCP tools are external: anything that isn't marked read-only counts as a write
    level = Level.HIGH_RISK if risky and pack != "mcp_bridge" else Level.WRITE if risky else Level.SAFE
    return ToolMeta(pack, level, tags)


def enabled(name: str, spec: Any = None) -> bool:
    return name == META_TOOL or meta_for(name, spec).pack not in _state["disabled"]


def pack_tools(pack: str, specs: list[Any]) -> list[str]:
    return [s.name for s in specs if s.name != META_TOOL and meta_for(s.name, s).pack == pack and enabled(s.name, s)]


# ---------------------------------------------------------- meaning (embeddings)
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]] | None]]
QueryFn = Callable[[str], Awaitable[list[float] | None]]


class ToolIndex:
    """Embeddings of every tool's name, tags and description (cached), so a request can be
    matched by meaning, e.g. "what's this video about" → youtube_transcript, with no shared word."""

    def __init__(self, embed_documents: EmbedFn, embed_query: QueryFn):
        self.embed_documents, self.embed_query = embed_documents, embed_query
        self._vecs: dict[str, list[float]] = {}

    @staticmethod
    def _text(spec: Any) -> str:
        meta = meta_for(spec.name, spec)
        return f"{spec.name.replace('_', ' ')}: {(spec.description or '')[:300]} ({' '.join(meta.tags)})"

    async def similarities(self, prompt: str, specs: list[Any]) -> dict[str, float]:
        """Tool → cosine similarity with the prompt. {} if the embedding model isn't available."""
        keyed = {s.name: (s, hashlib.sha1(self._text(s).encode()).hexdigest()[:12]) for s in specs}
        missing = [s for name, (s, h) in keyed.items() if f"{name}:{h}" not in self._vecs]
        if missing:
            vecs = await self.embed_documents([self._text(s) for s in missing])
            if not vecs:
                return {}
            for s, v in zip(missing, vecs):
                self._vecs[f"{s.name}:{keyed[s.name][1]}"] = v
        q = await self.embed_query(prompt)
        if not q:
            return {}
        qn = math.sqrt(sum(x * x for x in q)) or 1.0
        out = {}
        for name, (_, h) in keyed.items():
            v = self._vecs[f"{name}:{h}"]
            out[name] = sum(a * b for a, b in zip(q, v)) / (qn * (math.sqrt(sum(x * x for x in v)) or 1.0))
        return out


def _semantic_bonus(sims: dict[str, float]) -> dict[str, float]:
    """Similarities → a bonus for tools that stand out from the rest (z-score above the mean),
    so the scale of the embedding model doesn't matter."""
    if len(sims) < 3:
        return {}
    vals = list(sims.values())
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1.0
    return {n: 2.0 * max(0.0, (v - mean) / std) for n, v in sims.items()}


# ------------------------------------------------------------------- routing
def route(prompt: str, specs: list[Any], *, max_tools: int = 8, min_tools: int = 6,
          recent: set[str] | None = None, has_images: bool = False,
          similarity: dict[str, float] | None = None) -> list[str]:
    """Names of the tools to offer for `prompt`, best first, then the meta-tool.
    `specs`: the registry's ToolSpecs. `recent`: tools used earlier in this chat (follow-ups like
    "now plot it"). `similarity`: tool → cosine similarity from ToolIndex (optional)."""
    by_name = {s.name: s for s in specs if s.name != META_TOOL and enabled(s.name, s)}
    meta = [META_TOOL] if any(s.name == META_TOOL for s in specs) else []
    if len(by_name) <= max_tools:
        return [*by_name, *meta]
    words = _words(prompt)
    wordset = set(words)
    low = prompt.lower()
    bonus = _semantic_bonus(similarity or {})
    pinned_packs = _state["pinned"]
    scores: dict[str, float] = {}
    for name, spec in by_name.items():
        m = meta_for(name, spec)
        tag_hits = sum(1 for t in m.tags if t in wordset or (" " in t and t in low))
        name_hits = sum(1 for p in name.lower().split("_") if len(p) > 2 and p in wordset)
        desc = set(_words(getattr(spec, "description", "")[:300]))
        overlap = len(wordset & desc)
        score = 3.0 * tag_hits + 2.0 * name_hits + 0.4 * overlap
        if not tag_hits and not name_hits:
            score = min(score, 1.0)  # description overlap alone is weak evidence
        score += bonus.get(name, 0.0)
        if recent and name in recent:
            score += 2.5
        if m.pack in pinned_packs:
            score += 4.0
        if has_images and name in ("add_class", "manage_timetable", "add_task"):
            score += 3.0  # photos are mostly timetables, notices and assignment sheets
        if re.search(r"\.(csv|tsv|xlsx)\b", low) and name in ("analyze_dataset", "python_sandbox"):
            score += 6.0
        if re.search(r"\.(docx|pptx|pdf|png|jpe?g)\b", low) and name == "ingest_any_file":
            score += 3.0
        if re.search(r"https?://", low) and name == "read_webpage":
            score += 6.0
        if re.search(r"youtu\.?be", low) and name == "youtube_transcript":
            score += 8.0
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
    return [*chosen[:max_tools], *meta]


def describe(specs: list[Any]) -> list[dict[str, Any]]:
    """Every tool with its pack, level and tags (for /api/tools and debugging)."""
    return [{"name": s.name, "pack": (m := meta_for(s.name, s)).pack, "level": m.level.value,
             "tags": list(m.tags[:12])} for s in specs]


def packs_summary(specs: list[Any]) -> list[dict[str, Any]]:
    """Each pack with its tools and whether it's on or pinned (Settings → Tool packs)."""
    out = []
    for pid, info in PACKS.items():
        tools = [{"name": s.name, "level": meta_for(s.name, s).level.value}
                 for s in specs if s.name != META_TOOL and meta_for(s.name, s).pack == pid]
        out.append({"id": pid, "label": info.label, "description": info.description, "tools": tools,
                    "count": len(tools), "enabled": pid not in _state["disabled"], "pinned": pid in _state["pinned"]})
    return out
