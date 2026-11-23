"""Notes and file reading, jailed to ./workspace.

write_note is SAFE when it creates or appends, and REQUIRES_APPROVAL when it would
overwrite an existing note. The risk is decided per call from the arguments.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from .context import workspace
from .registry import Risk, ToolError, tool

MAX_READ_CHARS = 12000
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".csv", ".yaml", ".yml", ".toml", ".html",
                 ".css", ".js", ".ts", ".tsx", ".sql", ".sh", ".log", ".ini", ".cfg", ""}


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
    return slug or f"note-{datetime.now():%Y%m%d-%H%M%S}"


def note_path_for(title: str):
    return workspace().resolve(f"notes/{slugify(title)}.md")


def _write_note_risk(args: dict[str, Any]) -> Risk:
    if args.get("mode") == "overwrite":
        try:
            if note_path_for(str(args.get("title", ""))).exists():
                return Risk.REQUIRES_APPROVAL
        except Exception:
            return Risk.REQUIRES_APPROVAL
    return Risk.SAFE


def _write_note_summary(args: dict[str, Any]) -> str:
    path = note_path_for(str(args.get("title", "")))
    size = path.stat().st_size if path.exists() else 0
    return (f"Overwrite the existing note '{workspace().relative(path)}' ({size:,} bytes). "
            "Its current content will be replaced and can't be recovered.")


@tool(description=(
    "Create, append to, or overwrite a Markdown note in the workspace (notes/<title>.md). "
    "Use for study notes, summaries, plans and research write-ups."),
    params={"title": "Note title; it also becomes the file name",
            "content": "The full Markdown content to write",
            "mode": "'create' a new note (fails if it exists), 'append' to the end, "
                    "or 'overwrite' an existing one (asks the user first)"},
    category="notes", icon="notebook-pen",
    risk_check=_write_note_risk, risk_summary=_write_note_summary)
def write_note(title: str, content: str, mode: Literal["create", "append", "overwrite"] = "create") -> str:
    path = note_path_for(title)
    rel = workspace().relative(path)
    exists = path.exists()
    if mode == "create" and exists:
        raise ToolError(f"A note called '{rel}' already exists. Use mode 'append' to add to it, "
                        "or 'overwrite' to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and exists:
        current = path.read_text(encoding="utf-8")
        separator = "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
        path.write_text(current + separator + content.rstrip() + "\n", encoding="utf-8")
        return f"Appended {len(content):,} characters to {rel}."
    body = content if content.lstrip().startswith("#") else f"# {title}\n\n{content}"
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    verb = "Overwrote" if exists else "Created"
    return f"{verb} {rel} ({len(body):,} characters)."


@tool(description="Read a text file or note from the workspace, with line numbers.",
      params={"path": "Path inside the workspace, e.g. 'notes/dbms.md'",
              "start_line": "First line to read (1-based)",
              "max_lines": "How many lines to read"},
      category="notes", icon="file-text")
def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
    target = workspace().resolve(path, must_exist=True)
    if target.is_dir():
        raise ToolError(f"'{path}' is a folder. Use list_files to see what's inside.")
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise ToolError(f"'{path}' isn't a text file I can read.")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    chunk = lines[start - 1:start - 1 + max(1, max_lines)]
    body = "\n".join(f"{i:>5} │ {line}" for i, line in enumerate(chunk, start))
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n…[truncated, read a later start_line for more]"
    end = start + len(chunk) - 1
    return f"{workspace().relative(target)} (lines {start}–{end} of {len(lines)})\n{body}"


@tool(description="List files and folders in the workspace (or a sub-folder of it).",
      params={"path": "Folder inside the workspace; '.' for the top level"},
      category="notes", icon="folder-open")
def list_files(path: str = ".") -> str:
    folder = workspace().resolve(path, must_exist=True)
    if not folder.is_dir():
        raise ToolError(f"'{path}' is a file, not a folder.")
    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [e for e in entries if not e.name.startswith(".")]
    if not entries:
        return f"{workspace().relative(folder)}/ is empty."
    lines = [f"{workspace().relative(folder)}/"]
    for e in entries[:300]:
        if e.is_dir():
            lines.append(f"  📁 {e.name}/")
        else:
            modified = datetime.fromtimestamp(e.stat().st_mtime).strftime("%d %b %H:%M")
            lines.append(f"  📄 {e.name}  ({e.stat().st_size:,} B, {modified})")
    return "\n".join(lines)
