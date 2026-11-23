"""The agent loop: think → call tools → read results → repeat → answer."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import DOCS_DIR, ROOT, WORKSPACE_DIR, Settings
from .llm import OllamaClient, OllamaError, strip_thinking
from .memory import Memory
from .metrics import Monitor, TurnStats
from .tools import CONTEXT, REGISTRY, coerce_args
from .storage import get_store
from .tools.personal import export_note
from .tools.study import _documents, get_pages

MAX_TOOL_OUTPUT = 12000
HISTORY_CHARS = 30000     # ≈ 8k tokens of past conversation, leaves room in a 16k context
INLINE_DOC_CHARS = 14000  # attached documents up to this size go straight into the prompt
NOTE_ID_RE = re.compile(r"Saved note #(\d+)")
NOTES_REQUEST_RE = re.compile(r"\bnotes?\b", re.IGNORECASE)
# A trailing "the notes have been saved in …" line the model may invent.
SAVED_CLAIM_RE = re.compile(r"^.*\b(saved|stored)\b.*(location|folder|file|path|at|in)\b.*$",
                            re.IGNORECASE | re.MULTILINE)

# confirm(tool_name, args) -> bool    | asked before any "dangerous" tool runs
ConfirmFn = Callable[[str, dict], bool]
# on_event(kind, payload)             | kind: "tool_call" | "tool_result"
EventFn = Callable[[str, dict], None]

SYSTEM_PROMPT = """You are {name}, a personal AI agent running fully offline on the user's own computer.{user_line}
Now: {now}.
Calendar for resolving relative dates (use it, don't calculate): {calendar}

You can help with four kinds of work, using your tools:
1. Study — read and search the user's documents ({docs}); explain, summarise, and quiz them.
2. Personal — tasks, assignment deadlines, exams, reminders, notes, and long-term memory.
3. Research — search the web and read pages (needs internet).
4. Coding — explore, read, write and run code. Relative paths are inside {workspace}.

How to work:
- Use tools whenever they help. Never invent tool results, file contents, or facts you could look up.
- For multi-step jobs, take one step at a time and check each tool result before the next step.
- When the user shares a lasting fact or preference about themselves, save it with `remember`.
- When answering from documents, cite the file and page. When answering from the web, cite URLs.
- To make notes from a document: read ALL of it first (every page), then write proper study notes
  in Markdown: headings, key definitions, formulas, short examples, and a "Likely exam questions"
  section at the end. Put the COMPLETE notes in the body of ONE save_note call. Once it succeeds,
  reply in one or two short lines: the app shows the saved note to the user automatically,
  so don't repeat the notes in your reply.
- Never claim you saved, wrote or ran anything unless a tool result confirmed it.
- Write formulas in plain text or `code`, e.g. `x_new = (x - min) / (max - min)`, not LaTeX.
- For a quiz: read the material first, ask ONE question at a time, wait for the answer, then give feedback.
- Convert relative dates ("next Friday") to YYYY-MM-DD by looking them up in the calendar above.
- "My notes", "my PDF", "my slides" usually mean files in the documents folder: use search_documents /
  read_document. search_notes only covers short notes saved with save_note.
- If a tool says the user declined, accept it and suggest an alternative.
- Never use run_command to start yourself (main.py, your CLI/web/voice modes) or any long-running
  server; it would block for 120 s. Instead tell the user the exact command to run in a new terminal
  tab. Your own web UI starts with: cd "{project}" && source .venv/bin/activate && python main.py web
- Be concise and friendly. Use Markdown for lists and code.

Documents the user has uploaded (newest first; "the document/PDF I uploaded" means the first one):
{documents}
{focus}{memories}"""

FOCUS_PROMPT = """
FOCUS: the user is asking about the document(s) they attached in this chat: {names}.
- Answer from these documents, not from general knowledge, and cite page numbers (e.g. "p. 2").
- If the full text is already in the conversation, use it. If not, call read_document or
  search_documents with name="<file name>" before answering. Never answer about a document
  you haven't read.
- If the document doesn't contain the answer, say so plainly. Then you may add general knowledge,
  clearly labelled "Not from your document:".
"""


def _calendar(days: int = 21) -> str:
    """LLMs are bad at date arithmetic, so give them a lookup table."""
    today = date.today()
    return ", ".join(f"{(today + timedelta(d)).strftime('%a')} {(today + timedelta(d)).isoformat()}"
                     + (" (today)" if d == 0 else "") for d in range(days))


def _document_list(limit: int = 15) -> str:
    docs = sorted(_documents(), key=lambda p: p.stat().st_mtime, reverse=True)
    if not docs:
        return "(none yet)"
    lines = [f"- {p.name} (added {datetime.fromtimestamp(p.stat().st_mtime):%d %b %H:%M})"
             for p in docs[:limit]]
    if len(docs) > limit:
        lines.append(f"- …and {len(docs) - limit} more (use list_documents)")
    return "\n".join(lines)


def attachment_block(paths: list[Path]) -> str:
    """Text appended to the user's message for files they attached. Short documents go in
    whole, so the model reads the actual content. Long ones are left to the search tools."""
    blocks, budget = [], INLINE_DOC_CHARS
    for path in paths:
        pages = get_pages(path)
        text = "\n".join(f"--- page {i} ---\n{t.strip()}" for i, t in enumerate(pages, 1))
        n = len(pages)
        if sum(len(t.strip()) for t in pages) < 20:
            blocks.append(f"[Attached: {path.name}, {n} pages, but no readable text was found. "
                          "It is probably a scanned image. Tell the user.]")
        elif len(text) <= budget:
            budget -= len(text)
            blocks.append(f"[Attached document: {path.name}, {n} pages. Full text:]\n{text}\n"
                          f"[End of {path.name}]")
        else:
            blocks.append(f"[Attached document: {path.name}, {n} pages. It is too long to include "
                          f"here, so use search_documents(query, name=\"{path.name}\") and "
                          "read_document to find the relevant pages before answering.]")
    return "\n\n".join(blocks)


class Agent:
    def __init__(self, settings: Settings | None = None, *,
                 confirm: ConfirmFn | None = None, on_event: EventFn | None = None,
                 llm: OllamaClient | None = None):
        self.settings = settings or Settings()
        self.llm = llm or OllamaClient(
            self.settings.ollama_url, self.settings.model,
            context_length=self.settings.context_length,
            temperature=self.settings.temperature, think=self.settings.think)
        self.memory = Memory(self.settings, self.llm)
        CONTEXT.memory, CONTEXT.settings = self.memory, self.settings
        self.confirm = confirm or (lambda name, args: False)  # safe default: deny
        self.on_event = on_event or (lambda kind, payload: None)
        self.turns: list[list[dict]] = []  # each turn = user msg + tool msgs + reply
        self.last_notes: list[dict] = []   # notes saved during the latest turn
        self.active_docs: list[str] = []   # documents attached in this chat (answers focus on them)
        self.monitor = Monitor()
        self.last_stats: TurnStats | None = None  # tokens / context / timing of the latest turn

    # ---------------------------------------------------------------- helpers
    @property
    def model(self) -> str:
        return self.llm.model

    @model.setter
    def model(self, name: str) -> None:
        self.llm.model = name

    def reset(self) -> None:
        self.turns.clear()
        self.active_docs = []

    def tool_schemas(self) -> list[dict]:
        return [t.schema() for t in REGISTRY.values()]

    def _system_message(self, user_text: str) -> dict:
        relevant = self.memory.recall(user_text, k=6)
        memories = ""
        if relevant:
            memories = "\nThings you remember about the user:\n" + \
                       "\n".join(f"- {m['text']}" for m in relevant)
        s = self.settings
        return {"role": "system", "content": SYSTEM_PROMPT.format(
            name=s.agent_name,
            user_line=f" You work for {s.user_name}." if s.user_name else "",
            now=datetime.now().strftime("%A %d %B %Y, %H:%M"),
            calendar=_calendar(),
            documents=_document_list(),
            focus=FOCUS_PROMPT.format(names=", ".join(self.active_docs)) if self.active_docs else "",
            docs=DOCS_DIR, workspace=WORKSPACE_DIR, project=ROOT, memories=memories)}

    def _recent_history(self) -> list[dict]:
        """Latest turns that fit the history budget, so big documents can't overflow context."""
        kept, used = [], 0
        for past in reversed(self.turns[-self.settings.history_turns:]):
            size = sum(len(m.get("content") or "") for m in past)
            if kept and used + size > HISTORY_CHARS:
                break
            kept.append(past)
            used += size
        return [m for past in reversed(kept) for m in past]

    def _run_tool(self, name: str, raw_args) -> str:
        t = REGISTRY.get(name)
        if t is None:
            return f"Error: no tool named '{name}'. Available: {', '.join(REGISTRY)}"
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                return "Error: tool arguments were not valid JSON."
        args = coerce_args(t, raw_args or {})
        missing = [p for p in t.parameters["required"] if p not in args]
        if missing:
            return f"Error: missing required argument(s): {', '.join(missing)}"
        if t.dangerous and not self.confirm(name, args):
            return "The user declined to run this action."
        try:
            result = str(t.func(**args))
        except Exception as e:  # a tool failing should never crash the agent
            result = f"Error while running {name}: {type(e).__name__}: {e}"
        if len(result) > MAX_TOOL_OUTPUT:
            result = result[:MAX_TOOL_OUTPUT] + "\n[output truncated]"
        return result

    # ------------------------------------------------------------- main loop
    def chat(self, user_text: str, attachments: list[Path] | None = None) -> str:
        """Answer one user message. attachments: document files the user attached to it."""
        content = user_text
        if attachments:
            self.active_docs = [p.name for p in attachments]
            content = f"{user_text}\n\n{attachment_block(attachments)}"
        turn: list[dict] = [{"role": "user", "content": content}]
        history = self._recent_history()
        messages = [self._system_message(user_text), *history, *turn]
        tools = self.tool_schemas()
        stats = TurnStats(model=self.llm.model, context_limit=self.settings.context_length)
        started = time.perf_counter()

        reply = ""
        for _ in range(self.settings.max_steps):
            try:
                msg = self.llm.chat(messages, tools)
            except OllamaError as e:
                reply = f"⚠️ {e}"
                break
            if call_stats := getattr(self.llm, "last_stats", None):
                stats.calls.append(call_stats)
            content = strip_thinking(msg.get("content", ""))
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = content or "(no reply)"
                break

            assistant_msg = {"role": "assistant", "content": content, "tool_calls": calls}
            messages.append(assistant_msg)
            turn.append(assistant_msg)
            for call in calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments", {})
                self.on_event("tool_call", {"name": name, "args": args})
                t0 = time.perf_counter()
                result = self._run_tool(name, args)
                stats.tools.append((name, time.perf_counter() - t0))
                self.on_event("tool_result", {"name": name, "result": result})
                tool_msg = {"role": "tool", "content": result, "tool_name": name}
                messages.append(tool_msg)
                turn.append(tool_msg)
        else:
            reply = "I ran out of steps before finishing. Tell me to continue, or break the task up."

        reply = self._collect_notes(user_text, turn, reply)
        stats.wall_s = time.perf_counter() - started
        self.last_stats = stats
        if stats.calls:
            self.monitor.record(stats)
        turn.append({"role": "assistant", "content": reply})
        self.turns.append(turn)
        return reply

    def _collect_notes(self, user_text: str, turn: list[dict], reply: str) -> str:
        """Record notes saved this turn (interfaces display them). If the user asked for
        notes and the model wrote them in its reply but forgot to save, save them for it."""
        store = get_store()
        ids = [int(m.group(1)) for msg in turn if msg.get("tool_name") == "save_note"
               for m in [NOTE_ID_RE.search(msg["content"])] if m]
        if not ids and NOTES_REQUEST_RE.search(user_text) and len(reply) > 400:
            heading = re.search(r"^#+\s*(.+)$", reply, re.MULTILINE)
            title = (heading.group(1) if heading else user_text)[:80].strip()
            body = SAVED_CLAIM_RE.sub("", reply).strip()
            note_id = store.add_note(title, body)
            ids.append(note_id)
            path = export_note(store.query("SELECT * FROM notes WHERE id = ?", (note_id,))[0])
            reply = f"{body}\n\n_📝 Saved as note #{note_id} → `{path}`_"
        self.last_notes = [n for i in ids
                           for n in store.query("SELECT * FROM notes WHERE id = ?", (i,))]
        return reply
