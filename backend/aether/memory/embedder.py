"""Local embeddings through Ollama (default: nomic-embed-text).

If the embedding model isn't installed, everything still works: search falls back to
keywords, and `last_error` carries the fix ("ollama pull nomic-embed-text") for the UI.
"""
from __future__ import annotations

from typing import Protocol

from ..llm.ollama import OllamaError


class EmbeddingBackend(Protocol):
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...


class Embedder:
    def __init__(self, backend: EmbeddingBackend, model: str, batch_size: int = 16):
        self.backend = backend
        self.model = model
        self.batch_size = batch_size
        self.available: bool | None = None  # unknown until the first call
        self.last_error: OllamaError | None = None
        # nomic-embed-text is trained with task prefixes; using them noticeably improves recall.
        self._nomic = "nomic" in model.lower()

    def _doc(self, text: str) -> str:
        return f"search_document: {text}" if self._nomic else text

    def _query(self, text: str) -> str:
        return f"search_query: {text}" if self._nomic else text

    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if self.available is False:
            return None
        try:
            out: list[list[float]] = []
            for i in range(0, len(texts), self.batch_size):
                out.extend(await self.backend.embed(texts[i:i + self.batch_size], self.model))
            self.available, self.last_error = True, None
            return out
        except OllamaError as e:
            self.available, self.last_error = False, e
            return None

    async def embed_documents(self, texts: list[str]) -> list[list[float]] | None:
        return await self._embed([self._doc(t) for t in texts])

    async def embed_query(self, text: str) -> list[float] | None:
        vecs = await self._embed([self._query(text)])
        return vecs[0] if vecs else None

    def reset(self) -> None:
        """Try the model again (e.g. after the user pulled it)."""
        self.available, self.last_error = None, None
