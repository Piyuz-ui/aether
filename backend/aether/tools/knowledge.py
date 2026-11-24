"""RAG tools over the Knowledge Vault (uploaded PDFs and notes)."""
from __future__ import annotations

from .context import memory
from .registry import ToolError, tool


@tool(description=(
    "Search the user's uploaded documents (PDFs, PowerPoint slides, Markdown notes) by meaning "
    "and keywords. Use this FIRST for any question about their course material, syllabus, "
    "slides or notes. Answer from the returned passages and cite them exactly as shown, e.g. "
    "[notes.pdf p.3] or [lecture.pptx slide 5]."),
    params={"query": "What to look for. A short natural-language question works best",
            "document": "Only search this document (file name or a unique part of it). "
                        "Empty = all documents",
            "k": "How many passages to return (1–10)"},
    category="knowledge", icon="library", timeout_s=60)
async def search_knowledge_base(query: str, document: str = "", k: int = 5) -> str:
    from ..memory.knowledge import KnowledgeError
    kb = memory().knowledge
    if not kb.documents():
        return ("The knowledge vault is empty. Ask the user to upload PDFs or notes "
                "(web UI: Knowledge Vault; CLI: /ingest <file>).")
    try:
        # Small models often ask for too few passages; 5 is the floor for decent coverage.
        hits = await kb.search(query, k=max(5, min(k, 10)), document=document)
    except KnowledgeError as e:
        names = ", ".join(d["filename"] for d in kb.documents())
        raise ToolError(f"{e} Documents: {names}") from e
    if not hits:
        return f"Nothing in the knowledge vault matches '{query}'. Try other keywords."
    blocks = [f"[{h.cite()}] (relevance {h.score:.3f})\n{h.text}" for h in hits]
    note = ""
    if memory().embedder.available is False:
        note = "\n\n(Keyword search only: the embedding model isn't installed.)"
    return "\n\n---\n\n".join(blocks) + note


@tool(description="List the documents in the knowledge vault with their indexing status.",
      category="knowledge", icon="library")
def list_documents() -> str:
    docs = memory().knowledge.documents()
    if not docs:
        return "The knowledge vault is empty."
    lines = []
    for d in docs:
        status = d["status"] if d["status"] != "ready" else f"ready, {d['chunks']} passages"
        lines.append(f"- {d['filename']} ({d['pages']} pages, {status})")
    return "\n".join(lines)


@tool(description=(
    "Add a file from the workspace (e.g. something the user saved in uploads/) to the knowledge vault, "
    "so it can be searched and cited. Reads PDF, Word (.docx), PowerPoint (.pptx), Excel/CSV, Markdown, "
    "text, code files and notebooks, and images (screenshots, photos of notes: read by a local vision model)."),
    params={"path": "The file's path in the workspace, e.g. uploads/report.docx"},
    category="knowledge", icon="file-plus", timeout_s=240)
async def ingest_any_file(path: str) -> str:
    from ..memory.knowledge import KnowledgeError, citation_unit
    from .context import workspace
    target = workspace().resolve(path, must_exist=True)
    if not target.is_file():
        raise ToolError(f"'{path}' is a folder. Give a file, e.g. uploads/notes.docx.")
    kb = memory().knowledge
    try:
        doc, is_new = kb.register(target, filename=target.name)
        if is_new or doc["status"] != "ready":
            doc = await kb.index(doc["id"])
    except KnowledgeError as e:
        raise ToolError(str(e)) from e
    if doc.get("status") == "error":
        raise ToolError(doc.get("error") or "Indexing failed.")
    unit = citation_unit(doc["filename"])
    where = "" if unit == "image" else f" from {doc.get('pages') or 1} {'page' if unit == 'p.' else unit}(s)"
    return (f"{'Added' if is_new else 'Already in the vault:'} {doc['filename']}: {doc.get('chunks', 0)} passages"
            f"{where}. Search it with search_knowledge_base (document=\"{doc['filename']}\").")
