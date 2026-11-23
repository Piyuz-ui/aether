"""Long-term semantic memory: short facts about the user, searchable by meaning.

New facts are de-duplicated: if one is very close in meaning to a stored fact
(cosine ≥ 0.9), the stored fact is updated instead of adding a near-copy.
"""
from __future__ import annotations

import re
import time
from typing import Any, Literal

from .embedder import Embedder
from .store import Store, to_blob

Category = Literal["profile", "preference", "goal", "academic", "general"]
CATEGORIES: tuple[str, ...] = ("profile", "preference", "goal", "academic", "general")
DUPLICATE_SIMILARITY = 0.9
RELEVANT_SIMILARITY = 0.45

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "in", "on", "for", "what", "do",
         "does", "user", "users", "my", "me", "i", "it", "that", "this", "with", "be", "was"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class FactMemory:
    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def all(self) -> list[dict[str, Any]]:
        return self.store.query("SELECT id, text, category, source, created, updated FROM facts "
                                "ORDER BY updated DESC")

    def get(self, fact_id: int) -> dict[str, Any] | None:
        return self.store.one("SELECT id, text, category, source, created, updated FROM facts "
                              "WHERE id = ?", (fact_id,))

    async def add(self, text: str, category: str = "general",
                  source: str = "user") -> tuple[dict[str, Any], bool]:
        """Store a fact. Returns (fact, created). created=False means it merged into a duplicate."""
        text = " ".join(text.split()).strip()
        if not text:
            raise ValueError("The fact is empty.")
        category = category if category in CATEGORIES else "general"
        vec = await self.embedder.embed_documents([text])
        emb = vec[0] if vec else None
        now = time.time()

        duplicate_id: int | None = None
        if emb is not None:
            near = self.store.nearest("facts", emb, 1)
            if near and near[0][1] >= DUPLICATE_SIMILARITY:
                duplicate_id = near[0][0]
        else:
            for f in self.all():
                if f["text"].lower() == text.lower():
                    duplicate_id = f["id"]
        if duplicate_id is not None:
            self.store.execute("UPDATE facts SET text = ?, category = ?, embedding = COALESCE(?, "
                               "embedding), updated = ? WHERE id = ?",
                               (text, category, to_blob(emb) if emb else None, now, duplicate_id))
            return self.get(duplicate_id) or {}, False
        cur = self.store.execute(
            "INSERT INTO facts (text, category, source, embedding, created, updated) VALUES "
            "(?, ?, ?, ?, ?, ?)", (text, category, source, to_blob(emb) if emb else None, now, now))
        return self.get(cur.lastrowid) or {}, True

    async def update(self, fact_id: int, text: str | None = None,
                     category: str | None = None) -> dict[str, Any] | None:
        fact = self.get(fact_id)
        if not fact:
            return None
        new_text = " ".join((text or fact["text"]).split())
        new_cat = category if category in CATEGORIES else fact["category"]
        emb = None
        if text and text != fact["text"]:
            vec = await self.embedder.embed_documents([new_text])
            emb = vec[0] if vec else None
        self.store.execute("UPDATE facts SET text = ?, category = ?, embedding = COALESCE(?, "
                           "embedding), updated = ? WHERE id = ?",
                           (new_text, new_cat, to_blob(emb) if emb else None, time.time(), fact_id))
        return self.get(fact_id)

    def delete(self, fact_id: int) -> bool:
        return self.store.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    async def search(self, query: str, k: int = 6, min_similarity: float = RELEVANT_SIMILARITY
                     ) -> list[dict[str, Any]]:
        qvec = await self.embedder.embed_query(query)
        if qvec is not None:
            hits = [(fid, sim) for fid, sim in self.store.nearest("facts", qvec, k)
                    if sim >= min_similarity]
            return [{**f, "similarity": round(sim, 3)} for fid, sim in hits
                    if (f := self.get(fid))]
        q = _tokens(query)
        scored = [(len(q & _tokens(f["text"])) / (len(q) or 1), f) for f in self.all()]
        return [{**f, "similarity": round(s, 3)}
                for s, f in sorted(scored, key=lambda x: x[0], reverse=True)[:k] if s > 0]
