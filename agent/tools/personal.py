"""Personal assistant tools: time, tasks/deadlines/reminders, notes, long-term memory."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..config import NOTES_DIR
from ..storage import get_store
from .base import CONTEXT, tool


def _fmt_task(t: dict) -> str:
    status = "✅" if t["done"] else "⬜"
    due = f" — due {t['due']}" if t["due"] else ""
    return f"{status} #{t['id']} [{t['kind']}] {t['title']}{due}"


@tool("Get the current local date, time and weekday.", category="personal")
def get_datetime() -> str:
    return datetime.now().strftime("%A, %d %B %Y, %H:%M")


@tool("Add a task, assignment deadline, exam or reminder to the user's list.",
      params={"title": "What needs to be done",
              "due": "Due date/time as 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'. Empty if none.",
              "kind": "One of: task, deadline, exam, reminder"},
      category="personal")
def add_task(title: str, due: str = "", kind: str = "task") -> str:
    if due:
        try:
            datetime.fromisoformat(due)
        except ValueError:
            return f"Error: due '{due}' isn't in YYYY-MM-DD or YYYY-MM-DD HH:MM format."
    task_id = get_store().add_task(title, due, kind.lower())
    return f"Added #{task_id}: {title}" + (f" (due {due})" if due else "")


@tool("List the user's open tasks, deadlines, exams and reminders, soonest first.",
      params={"include_done": "Also show completed items"}, category="personal")
def list_tasks(include_done: bool = False) -> str:
    tasks = get_store().list_tasks(include_done)
    return "\n".join(_fmt_task(t) for t in tasks) if tasks else "No tasks. 🎉"


@tool("Mark a task as done.", params={"task_id": "The task number, e.g. 3"},
      category="personal")
def complete_task(task_id: int) -> str:
    return f"Marked #{task_id} done." if get_store().set_task_done(task_id) \
        else f"No task #{task_id}."


@tool("Permanently delete a task.", params={"task_id": "The task number"},
      category="personal", dangerous=True)
def delete_task(task_id: int) -> str:
    return f"Deleted #{task_id}." if get_store().delete_task(task_id) else f"No task #{task_id}."


def note_path(note_id: int, title: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "note"
    return NOTES_DIR / f"{note_id:03d}-{slug}.md"


def export_note(note: dict) -> Path:
    """Write a note to notes/ as Markdown so the user can open it in any app."""
    path = note_path(note["id"], note["title"])
    path.write_text(f"# {note['title']}\n\n_{note['created']}_\n\n{note['body']}\n")
    return path


@tool("Save a note (study notes, lecture notes, ideas, summaries). It is stored in the app "
      "and as a Markdown file the user can open.",
      params={"title": "Short title", "body": "The full note content in Markdown"},
      category="personal")
def save_note(title: str, body: str) -> str:
    store = get_store()
    note_id = store.add_note(title, body)
    note = store.query("SELECT * FROM notes WHERE id = ?", (note_id,))[0]
    return f"Saved note #{note_id} '{title}' to {export_note(note)}"


@tool("Search the user's saved notes AND their study documents. "
      "Leave query empty to list recent saved notes.",
      params={"query": "Words to look for"}, category="personal")
def search_notes(query: str = "") -> str:
    from .study import search_documents  # users say "my notes" for both kinds

    store = get_store()
    notes = store.search_notes(query) if query else store.recent_notes()
    parts = []
    if notes:
        parts.append("Saved notes:\n" + "\n\n".join(
            f"#{n['id']} {n['title']} ({n['created']})\n{n['body']}" for n in notes))
    if query:
        docs = search_documents(query)
        if not docs.startswith("No matches"):
            parts.append("From study documents:\n" + docs)
    return "\n\n".join(parts) or "Nothing found in saved notes or documents."


@tool("Store a lasting fact or preference about the user in long-term memory "
      "(e.g. their course, goals, habits, likes). Use whenever they share one.",
      params={"fact": "The fact, written as a full sentence about the user"},
      category="memory")
def remember(fact: str) -> str:
    memory_id = CONTEXT.memory.remember(fact)
    return f"Remembered (#{memory_id}): {fact}"


@tool("Search long-term memory for facts about the user.",
      params={"query": "What to look up"}, category="memory")
def recall(query: str) -> str:
    hits = CONTEXT.memory.recall(query, k=8)
    return "\n".join(f"#{m['id']} {m['text']}" for m in hits) if hits else "Nothing relevant remembered."


@tool("Delete a fact from long-term memory.", params={"memory_id": "The memory number"},
      category="memory", dangerous=True)
def forget(memory_id: int) -> str:
    return f"Forgot #{memory_id}." if CONTEXT.memory.forget(memory_id) else f"No memory #{memory_id}."
