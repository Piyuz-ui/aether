"""The Knowledge Vault: ingest PDFs / Markdown / text and search them (RAG).

Search is hybrid. Each query runs two retrievers:
  • semantic: cosine similarity between embeddings (finds paraphrases)
  • keyword:  SQLite FTS5 BM25 (finds exact terms, course codes, formulas)
and merges the two rankings with Reciprocal Rank Fusion, so a chunk that ranks well in
either list rises to the top.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embedder import Embedder
from .store import Store, to_blob

SUPPORTED = {".pdf", ".md", ".markdown", ".txt"}
CHUNK_CHARS = 1000
OVERLAP_CHARS = 180
RRF_K = 60
SEMANTIC_WEIGHT = 1.0   # meaning-based ranking …
KEYWORD_WEIGHT = 0.7    # … counts a bit more than exact-word (BM25) ranking

ProgressFn = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class Hit:
    chunk_id: int
    doc_id: int
    filename: str
    page: int
    text: str
    score: float
    semantic: float | None = None

    def cite(self) -> str:
        return f"{self.filename} p.{self.page}"


class KnowledgeError(ValueError):
    pass


def read_pages(path: Path) -> list[str]:
    """Text per page (PDF) or one 'page' for a text file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                reader.decrypt("")
            return [(p.extract_text() or "") for p in reader.pages]
        except Exception as e:
            raise KnowledgeError(f"Couldn't read the PDF: {type(e).__name__}: {e}") from e
    return [path.read_text(encoding="utf-8", errors="replace")]


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split on paragraph, then sentence, boundaries into ~size-char chunks that overlap."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= size:
            pieces.append(p)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", p)
        buf = ""
        for s in sentences:
            while len(s) > size:  # one enormous "sentence" (tables, code): hard-split it
                pieces.append(s[:size])
                s = s[size - overlap:]
            if len(buf) + len(s) + 1 > size and buf:
                pieces.append(buf)
                buf = ""
            buf = f"{buf} {s}".strip()
        if buf:
            pieces.append(buf)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            current = f"{tail}\n\n{piece}"
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _fts_query(query: str) -> str:
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", query) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:24])


class KnowledgeBase:
    def __init__(self, store: Store, embedder: Embedder, files_dir: Path):
        self.store = store
        self.embedder = embedder
        self.files_dir = files_dir
        files_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- listing
    def documents(self) -> list[dict[str, Any]]:
        return self.store.query("SELECT id, filename, pages, chunks, status, progress, error, "
                                "created FROM documents ORDER BY created DESC")

    def document(self, doc_id: int) -> dict[str, Any] | None:
        return self.store.one("SELECT * FROM documents WHERE id = ?", (doc_id,))

    def find_document(self, name: str) -> dict[str, Any] | None:
        """Exact file name, else a unique case-insensitive partial match."""
        docs = self.documents()
        for d in docs:
            if d["filename"] == name:
                return d
        low = name.lower().strip()
        matches = [d for d in docs if low and low in d["filename"].lower()]
        return matches[0] if len(matches) == 1 else None

    def delete(self, doc_id: int) -> bool:
        doc = self.document(doc_id)
        if not doc:
            return False
        self.store.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        path = Path(doc["path"])
        if path.is_relative_to(self.files_dir) and path.exists():
            path.unlink()
        return True

    # ------------------------------------------------------------ ingestion
    def register(self, source: Path, *, filename: str | None = None) -> tuple[dict[str, Any], bool]:
        """Copy a file into the vault and create its row. Returns (document, is_new)."""
        name = Path(filename or source.name).name
        if Path(name).suffix.lower() not in SUPPORTED:
            raise KnowledgeError(f"'{name}' isn't supported. Use PDF, Markdown or TXT.")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        existing = self.store.one("SELECT * FROM documents WHERE sha256 = ?", (digest,))
        if existing:
            return existing, False
        target = self.files_dir / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            target = self.files_dir / f"{target.stem}-{digest[:8]}{target.suffix}"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        cur = self.store.execute(
            "INSERT INTO documents (filename, path, sha256, status, created) VALUES (?, ?, ?, "
            "'queued', ?)", (target.name, str(target), digest, time.time()))
        return self.document(cur.lastrowid) or {}, True

    async def index(self, doc_id: int, on_progress: ProgressFn | None = None) -> dict[str, Any]:
        """Read → chunk → embed → store. Progress and status are kept in the documents row."""
        doc = self.document(doc_id)
        if not doc:
            raise KnowledgeError(f"No document #{doc_id}.")

        async def update(**fields: Any) -> None:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.store.execute(f"UPDATE documents SET {sets} WHERE id = ?", (*fields.values(), doc_id))
            if on_progress:
                res = on_progress({"doc_id": doc_id, "filename": doc["filename"], **fields})
                if asyncio.iscoroutine(res):
                    await res

        try:
            await update(status="reading", progress=0.05, error=None)
            pages = await asyncio.to_thread(read_pages, Path(doc["path"]))
            if sum(len(p.strip()) for p in pages) < 20:
                raise KnowledgeError("No readable text found. It may be a scanned image "
                                     "(OCR isn't supported yet).")
            rows: list[tuple[int, int, str]] = []
            for page_no, page_text in enumerate(pages, 1):
                for i, chunk in enumerate(chunk_text(page_text)):
                    rows.append((page_no, i, chunk))
            await update(status="embedding", progress=0.15, pages=len(pages))

            self.store.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            embedded = 0
            keyword_only = False
            batch = 16
            for start in range(0, len(rows), batch):
                part = rows[start:start + batch]
                vecs = await self.embedder.embed_documents([f"{doc['filename']}: {t}" for _, _, t in part])
                if vecs is None:
                    keyword_only = True
                self.store.executemany(
                    "INSERT INTO chunks (doc_id, page, idx, text, embedding) VALUES (?, ?, ?, ?, ?)",
                    [(doc_id, p, i, t, to_blob(vecs[j]) if vecs else None)
                     for j, (p, i, t) in enumerate(part)])
                embedded += len(part)
                await update(progress=round(0.15 + 0.85 * embedded / len(rows), 3))
            note = None
            if keyword_only and self.embedder.last_error:
                note = (f"Indexed for keyword search only: {self.embedder.last_error.message} "
                        f"{self.embedder.last_error.hint}").strip()
            await update(status="ready", progress=1.0, chunks=len(rows), error=note)
        except KnowledgeError as e:
            await update(status="error", error=str(e))
        except Exception as e:
            await update(status="error", error=f"{type(e).__name__}: {e}")
        return self.document(doc_id) or {}

    async def ingest(self, source: Path, *, filename: str | None = None,
                     on_progress: ProgressFn | None = None) -> dict[str, Any]:
        doc, is_new = self.register(source, filename=filename)
        if not is_new and doc["status"] == "ready":
            return doc
        return await self.index(doc["id"], on_progress)

    # ---------------------------------------------------------------- search
    async def search(self, query: str, k: int = 5, document: str = "") -> list[Hit]:
        where, params = "", ()
        if document:
            doc = self.find_document(document)
            if not doc:
                raise KnowledgeError(f"No single document matches '{document}'.")
            where, params = "doc_id = ?", (doc["id"],)

        ranked: dict[int, float] = {}
        semantic: dict[int, float] = {}
        qvec = await self.embedder.embed_query(query)
        if qvec is not None:
            for rank, (cid, sim) in enumerate(self.store.nearest("chunks", qvec, 20, where, params)):
                semantic[cid] = sim
                ranked[cid] = ranked.get(cid, 0) + SEMANTIC_WEIGHT / (RRF_K + rank + 1)
        fts = _fts_query(query)
        if fts:
            doc_filter = f"AND c.{where}" if where else ""
            try:
                rows = self.store.query(
                    f"SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                    f"WHERE chunks_fts MATCH ? {doc_filter} ORDER BY bm25(chunks_fts) LIMIT 20",
                    (fts, *params))
            except Exception:
                rows = []
            for rank, r in enumerate(rows):
                ranked[r["id"]] = ranked.get(r["id"], 0) + KEYWORD_WEIGHT / (RRF_K + rank + 1)
        if not ranked:
            return []
        ordered = sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)[:k * 3]
        ids = [cid for cid, _ in ordered]
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in self.store.query(
            f"SELECT c.id, c.doc_id, c.page, c.text, d.filename FROM chunks c "
            f"JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({marks})", ids)}
        hits: list[Hit] = []
        seen_text: set[str] = set()
        for cid, score in ordered:
            row = rows.get(cid)
            if row is None:
                continue
            # The same passage from a re-uploaded copy shouldn't use up a result slot.
            key = " ".join(row["text"].split())[:400].lower()
            if key in seen_text:
                continue
            seen_text.add(key)
            hits.append(Hit(chunk_id=cid, doc_id=row["doc_id"], filename=row["filename"],
                            page=row["page"], text=row["text"], score=round(score, 5),
                            semantic=round(semantic[cid], 4) if cid in semantic else None))
            if len(hits) == k:
                break
        return hits
