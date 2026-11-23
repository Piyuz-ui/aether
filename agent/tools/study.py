"""Study tools: read and search the PDFs / notes the user drops into documents/."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..config import DOCS_DIR
from .base import tool

SUPPORTED = {".pdf", ".txt", ".md"}
MAX_CHARS = 12000


def _documents() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*") if p.suffix.lower() in SUPPORTED)


def _find(name: str) -> Path | None:
    docs = _documents()
    for p in docs:  # exact, then case-insensitive partial match
        if p.name == name:
            return p
    low = name.lower()
    matches = [p for p in docs if low in p.name.lower()]
    return matches[0] if len(matches) == 1 else None


@lru_cache(maxsize=32)
def _pages(path: Path, mtime: float) -> list[str]:
    """Return a document's text split into pages (one 'page' for plain text)."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return [(page.extract_text() or "") for page in PdfReader(path).pages]
    return [path.read_text(errors="ignore")]


def get_pages(path: Path) -> list[str]:
    return _pages(path, path.stat().st_mtime)


@tool("List the study documents (PDF, TXT, MD) available to read.", category="study")
def list_documents() -> str:
    docs = _documents()
    if not docs:
        return f"No documents yet. Put PDFs or notes in: {DOCS_DIR}"
    lines = []
    for p in docs:
        n = len(get_pages(p)) if p.suffix.lower() == ".pdf" else 1
        lines.append(f"- {p.relative_to(DOCS_DIR)} ({n} page{'s' if n != 1 else ''})")
    return "\n".join(lines)


@tool("Read the text of a study document, optionally a page range. Use this before "
      "summarising, explaining or making a quiz from a document.",
      params={"name": "File name (or a unique part of it) from list_documents",
              "start_page": "First page to read (1-based)",
              "end_page": "Last page to read; 0 means a few pages from start_page"},
      category="study")
def read_document(name: str, start_page: int = 1, end_page: int = 0) -> str:
    path = _find(name)
    if not path:
        return f"Couldn't find a single document matching '{name}'.\n{list_documents()}"
    pages = get_pages(path)
    start = max(1, start_page)
    end = min(len(pages), end_page if end_page >= start else start + 4)
    out, used = [], 0
    for i in range(start, end + 1):
        text = pages[i - 1].strip()
        chunk = f"--- page {i} ---\n{text}"
        if used + len(chunk) > MAX_CHARS:
            out.append(f"[stopped at page {i - 1} to save space — ask for page {i} onwards]")
            break
        out.append(chunk)
        used += len(chunk)
    return f"{path.name} — {len(pages)} pages total\n" + "\n".join(out)


@tool("Search across all study documents for a word or phrase. Returns matching "
      "snippets with file name and page number.",
      params={"query": "Words to look for"}, category="study")
def search_documents(query: str) -> str:
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
    if not terms:
        return "Give me a more specific query."
    hits = []
    for path in _documents():
        for page_no, text in enumerate(get_pages(path), start=1):
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                idx = min((low.find(t) for t in terms if t in low), default=0)
                snippet = " ".join(text[max(0, idx - 200): idx + 400].split())
                hits.append((score, f"{path.name} p.{page_no}: …{snippet}…"))
    if not hits:
        return f"No matches for '{query}'."
    hits.sort(key=lambda h: h[0], reverse=True)
    return "\n\n".join(h[1] for h in hits[:6])
