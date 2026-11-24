"""AETHER's dual memory system.

Short-term:  PersistentSession, the conversation history, saved per session in SQLite.
Long-term:   FactMemory (facts about the user, embedded), TaskBook (deadlines + timetable),
             KnowledgeBase (indexed documents, hybrid search).

MemorySystem bundles them, builds the memory context injected before each goal, and
auto-extracts new long-term facts after each answer.
"""
from __future__ import annotations

from pathlib import Path

import re
from datetime import date, timedelta
from typing import Any

from ..config import Settings
from ..llm.parsing import ToolCallParseError, loads_lenient, strip_thinking
from .embedder import Embedder, EmbeddingBackend
from .facts import CATEGORIES, FactMemory
from .knowledge import KnowledgeBase
from .quizzes import QuizBook
from .sessions import PersistentSession
from .store import Store
from .taskbook import TaskBook

__all__ = ["Embedder", "FactMemory", "KnowledgeBase", "MemorySystem", "PersistentSession",
           "Store", "TaskBook"]

FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|i'll|i'd|my|me|mine|myself)\b", re.IGNORECASE)

EXTRACT_PROMPT = """Extract lasting facts about the USER from the exchange below: who they are, what \
they study, their courses, goals, preferences, habits and routines.

Rules:
- Only facts that will still be true next month. Skip one-off requests, questions, dates, deadlines, \
exams and tasks (those are stored elsewhere), and anything about the world or the assistant.
- Write each fact as one short sentence starting with "The user".
- Reply with ONLY a JSON array, e.g. [{{"fact": "The user studies BCA AI & Data Science.", \
"category": "academic"}}]. Categories: profile, preference, goal, academic, general.
- If there is nothing worth remembering, reply with [].

USER: {user}
ASSISTANT: {answer}"""


class MemorySystem:
    def __init__(self, settings: Settings, backend: EmbeddingBackend, *, db_path: str | None = None):
        self.settings = settings
        self.store = Store(db_path or settings.data_dir / "aether.db")
        self.embedder = Embedder(backend, settings.embed_model)
        self.facts = FactMemory(self.store, self.embedder)
        self.tasks = TaskBook(self.store)
        self.knowledge = KnowledgeBase(self.store, self.embedder, settings.data_dir / "knowledge")
        self.knowledge.describe_image = self._describe_image
        self._vision_backend = backend
        self.quizzes = QuizBook(self.store, self.facts)
        from ..usage import UsageBook
        self.usage = UsageBook(self.store)

    async def _describe_image(self, path: Path) -> str | None:
        """Turn an image into searchable text with a local vision model: every piece of text in it
        (verbatim), then what it shows. None if no vision model is installed."""
        import base64
        backend = self._vision_backend
        finder = getattr(backend, "find_model_with", None)
        if finder is None or not hasattr(backend, "chat_stream"):
            return None
        model = await finder("vision", prefer=self.settings.vision_model)
        if not model:
            return None
        b64 = base64.b64encode(path.read_bytes()).decode()
        prompt = ("Transcribe ALL text visible in this image exactly (headings, labels, tables, handwriting), "
                  "then describe what it shows in a few sentences: the type of image, the key content, any "
                  "numbers, dates or names. Plain text, no commentary.")
        out: list[str] = []
        async for d in backend.chat_stream(model=model, messages=[{"role": "user", "content": prompt, "images": [b64]}],
                                           tools=None, options={"temperature": 0.1, "num_predict": 1500}, think=False):
            out.append(d.content)
        text = "".join(out).strip()
        return f"[Image read by {model}]\n{text}" if text else None

    def session(self, session_id: str | None = None) -> PersistentSession:
        return PersistentSession(self.store, session_id)

    def close(self) -> None:
        self.store.close()

    # --------------------------------------------------------------- recall
    async def build_context(self, goal: str) -> str:
        """Everything relevant the agent should know before working on `goal`."""
        parts: list[str] = []

        facts = await self.facts.search(goal, k=6)
        profile = [f for f in self.facts.all() if f["category"] == "profile"][:4]
        seen: set[int] = set()
        lines = []
        for f in [*profile, *facts]:
            if f["id"] not in seen:
                seen.add(f["id"])
                lines.append(f"- {f['text']}")
        if lines:
            parts.append("Facts about the user:\n" + "\n".join(lines))

        open_tasks = self.tasks.list()
        overdue = [t for t in open_tasks if t["overdue"]]
        soon = [t for t in self.tasks.list(within_days=7) if not t["overdue"]]
        if overdue:
            parts.append("Overdue:\n" + "\n".join(
                f"- #{t['id']} [{t['kind']}] {t['title']} ({t['when']})" for t in overdue[:6]))
        if soon:
            parts.append("Coming up in the next 7 days:\n" + "\n".join(
                f"- #{t['id']} [{t['kind']}] {t['title']} ({t['when']})" for t in soon[:8]))

        today = date.today()
        for label, day in (("Today", today), ("Tomorrow", today + timedelta(days=1))):
            classes = self.tasks.classes_on(day)
            if classes:
                parts.append(f"{label}'s classes ({day:%A}): " + "; ".join(
                    f"{c['start']}–{c['end']} {c['course']}" + (f" ({c['location']})" if c["location"] else "")
                    for c in classes))

        weak = self.quizzes.weak_topics()
        if weak:
            parts.append("Weak quiz topics (focus study help here): " + ", ".join(
                f"{w['topic']} ({round(100 * w['accuracy'])}%)" for w in weak[:6]))

        docs = [d for d in self.knowledge.documents() if d["status"] == "ready"]
        if docs:
            names = ", ".join(d["filename"] for d in docs[:12])
            more = f" and {len(docs) - 12} more" if len(docs) > 12 else ""
            parts.append(f"Knowledge vault ({len(docs)} documents): {names}{more}. "
                         "Use search_knowledge_base for questions about the user's documents or notes.")
        return "\n\n".join(parts)

    # ------------------------------------------------------------ auto-learn
    async def _already_known(self, text: str) -> bool:
        """Auto-learned facts are stricter than explicit ones: anything already mostly known
        (cosine ≥ 0.8, or the same words) is skipped rather than stored again."""
        hits = await self.facts.search(text, k=1, min_similarity=0.8)
        if hits and hits[0].get("similarity", 0) >= 0.8:
            return True
        words = set(re.findall(r"[a-z0-9]+", text.lower())) - {"the", "user", "is", "a", "an"}
        for f in self.facts.all():
            known = set(re.findall(r"[a-z0-9]+", f["text"].lower()))
            if words and len(words & known) / len(words) >= 0.75:
                return True
        return False

    async def extract_facts(self, llm: Any, model: str, user_text: str,
                            answer: str) -> list[dict[str, Any]]:
        """Ask the model for durable facts in this exchange and store the new ones."""
        if not FIRST_PERSON.search(user_text):
            return []  # nothing personal was said; skip the extra model call
        prompt = EXTRACT_PROMPT.format(user=user_text[:2000], answer=answer[:1500])
        parts: list[str] = []
        async for d in llm.chat_stream(model=model, messages=[{"role": "user", "content": prompt}],
                                       # Same num_ctx as chat: a different value makes Ollama
                                       # reload the whole model.
                                       tools=None, options={"temperature": 0.0, "num_predict": 300,
                                                            "num_ctx": self.settings.context_length},
                                       think=False):
            parts.append(d.content)
        raw = strip_thinking("".join(parts))
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        try:
            items = loads_lenient(match.group(0))
        except ToolCallParseError:
            return []
        saved: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            text = str(item.get("fact", "") if isinstance(item, dict) else item).strip()
            category = str(item.get("category", "general")) if isinstance(item, dict) else "general"
            if not (8 <= len(text) <= 220) or "user" not in text.lower():
                continue
            if await self._already_known(text):
                continue  # e.g. the agent already saved it with `remember` this turn
            fact, created = await self.facts.add(text, category if category in CATEGORIES else "general",
                                                 source="auto")
            if created:
                saved.append(fact)
            if len(saved) >= 3:
                break
        return saved
