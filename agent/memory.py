"""Long-term memory: facts about the user, searched by meaning (embeddings) when
an embedding model is installed, and by keyword overlap otherwise."""
from __future__ import annotations

import math
import re

from .config import Settings
from .llm import OllamaClient, OllamaError
from .storage import get_store

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "i", "my", "me", "in", "on",
         "for", "what", "do", "you", "it", "that", "this", "with", "be", "was", "am"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Memory:
    def __init__(self, settings: Settings, llm: OllamaClient):
        self.settings = settings
        self.llm = llm
        self.store = get_store()
        self._embeddings_ok: bool | None = None  # unknown until first try

    def _embed(self, text: str) -> list[float] | None:
        if self._embeddings_ok is False:
            return None
        try:
            vec = self.llm.embed([text], self.settings.embed_model)[0]
            self._embeddings_ok = True
            return vec
        except (OllamaError, Exception):
            self._embeddings_ok = False  # model not pulled — fall back to keywords
            return None

    def remember(self, text: str) -> int:
        return self.store.add_memory(text.strip(), self._embed(text))

    def forget(self, memory_id: int) -> bool:
        return self.store.delete_memory(memory_id)

    def all(self) -> list[dict]:
        return self.store.all_memories()

    def recall(self, query: str, k: int = 5) -> list[dict]:
        memories = self.store.all_memories()
        if not memories:
            return []
        qvec = self._embed(query)
        qtok = _tokens(query)
        scored = []
        for m in memories:
            if qvec and m["embedding"] and len(m["embedding"]) == len(qvec):
                score = _cosine(qvec, m["embedding"])
            else:
                mtok = _tokens(m["text"])
                score = len(qtok & mtok) / (len(qtok) or 1)
            scored.append((score, m))
        scored.sort(key=lambda s: s[0], reverse=True)
        threshold = 0.45 if qvec else 0.01
        return [m for s, m in scored[:k] if s >= threshold]
