"""The agent loop: think → call tools → read results → repeat → answer."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable

from .config import DOCS_DIR, ROOT, WORKSPACE_DIR, Settings
from .llm import OllamaClient, OllamaError, strip_thinking
from .memory import Memory
from .tools import CONTEXT, REGISTRY, coerce_args

MAX_TOOL_OUTPUT = 12000

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
- For a quiz: read the material first, ask ONE question at a time, wait for the answer, then give feedback.
- Convert relative dates ("next Friday") to YYYY-MM-DD by looking them up in the calendar above.
- "My notes", "my PDF", "my slides" usually mean files in the documents folder: use search_documents /
  read_document. search_notes only covers short notes saved with save_note.
- If a tool says the user declined, accept it and suggest an alternative.
- Never use run_command to start yourself (main.py, your CLI/web/voice modes) or any long-running
  server; it would block for 120 s. Instead tell the user the exact command to run in a new terminal
  tab. Your own web UI starts with: cd "{project}" && source .venv/bin/activate && python main.py web
- Be concise and friendly. Use Markdown for lists and code.
{memories}"""


def _calendar(days: int = 21) -> str:
    """LLMs are bad at date arithmetic, so give them a lookup table."""
    today = date.today()
    return ", ".join(f"{(today + timedelta(d)).strftime('%a')} {(today + timedelta(d)).isoformat()}"
                     + (" (today)" if d == 0 else "") for d in range(days))


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

    # ---------------------------------------------------------------- helpers
    @property
    def model(self) -> str:
        return self.llm.model

    @model.setter
    def model(self, name: str) -> None:
        self.llm.model = name

    def reset(self) -> None:
        self.turns.clear()

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
            docs=DOCS_DIR, workspace=WORKSPACE_DIR, project=ROOT, memories=memories)}

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
    def chat(self, user_text: str) -> str:
        turn: list[dict] = [{"role": "user", "content": user_text}]
        history = [m for past in self.turns[-self.settings.history_turns:] for m in past]
        messages = [self._system_message(user_text), *history, *turn]
        tools = self.tool_schemas()

        reply = ""
        for _ in range(self.settings.max_steps):
            try:
                msg = self.llm.chat(messages, tools)
            except OllamaError as e:
                reply = f"⚠️ {e}"
                break
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
                result = self._run_tool(name, args)
                self.on_event("tool_result", {"name": name, "result": result})
                tool_msg = {"role": "tool", "content": result, "tool_name": name}
                messages.append(tool_msg)
                turn.append(tool_msg)
        else:
            reply = "I ran out of steps before finishing. Tell me to continue, or break the task up."

        turn.append({"role": "assistant", "content": reply})
        self.turns.append(turn)
        return reply
