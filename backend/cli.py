"""AETHER in the terminal.

    python cli.py                      interactive chat
    python cli.py "what is 17% of 2400?"  answer one goal and exit
    python cli.py --model qwen3:14b    choose a model
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from aether import __version__
from aether.config import get_settings
from aether.engine import AetherAgent, Session
from aether.llm.ollama import OllamaClient
from aether.memory import MemorySystem
from aether.memory.knowledge import KnowledgeError
from aether.tools.context import set_memory
from aether.events import AgentEvent, AgentState, EventType
from aether.llm.ollama import OllamaError
from aether.metrics import fmt_seconds
from aether.tools import REGISTRY, Risk

THEME = Theme({
    "glacier": "#7DD3FC", "sky": "#38BDF8", "mist": "#94B8D7", "periwinkle": "#A5B4FC",
    "seafoam": "#5EEAD4", "frost": "#F0F9FF", "warn": "#FCD34D", "danger": "#FDA4AF",
    # Rich can't combine a theme name with bold/italic in one string ("bold warn"),
    # so every combination gets its own theme entry.
    "glacier.bold": "bold #7DD3FC", "sky.bold": "bold #38BDF8", "warn.bold": "bold #FCD34D",
    "danger.bold": "bold #FDA4AF", "mist.italic": "italic #94B8D7",
    "periwinkle.italic": "italic #A5B4FC", "warn.italic": "italic #FCD34D",
})
console = Console(theme=THEME, highlight=False)

STATE_LABEL = {
    AgentState.THINKING: "thinking", AgentState.USING_TOOL: "using a tool",
    AgentState.AWAITING_APPROVAL: "waiting for your approval", AgentState.IDLE: "idle",
}

HELP = """\
**Commands**

| | |
|---|---|
| `/ingest <file…>` | add PDFs / Markdown / text to the knowledge vault (drag files in) |
| `/docs` | list the knowledge vault |
| `/memory` | what AETHER remembers about you · `/forget <id>` deletes one |
| `/tasks` · `/timetable` | your deadlines and weekly classes |
| `/sessions` · `/resume <n>` | past conversations, and continue one |
| `/tools` | list tools and which ones need approval |
| `/model [name]` | show installed models, or switch to one |
| `/stats` | tokens, context and timing for this session |
| `/verbose` | show full tool output on/off |
| `/reset` | start a new conversation |
| `/exit` | quit |

Try: *"I'm a BCA AI & Data Science student, remember that"* ·
*"My DBMS exam is next Friday"* · *"/ingest ~/Downloads/notes.pdf"* then *"what does my PDF say about
normalization?"* · *"Research what's new in Python and save a note"* · *"Delete that note"* (safety gate)
"""


def _short_args(args: dict[str, Any], limit: int = 110) -> str:
    text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return text if len(text) <= limit else text[:limit] + "…"


def _preview(output: str, lines: int = 3, width: int = 160) -> str:
    rows = [r for r in output.strip().splitlines() if r.strip()][:lines]
    rows = [r if len(r) <= width else r[:width] + "…" for r in rows]
    more = len(output.strip().splitlines()) - len(rows)
    return "\n".join(rows) + (f"\n… {more} more line(s)" if more > 0 else "")


class StreamView:
    """The live, transient area showing the agent's state and streaming text."""

    def __init__(self) -> None:
        self.live: Live | None = None
        self.state = AgentState.THINKING
        self.tool = ""
        self.content = ""
        self.thinking = ""

    def _render(self) -> Group:
        label = STATE_LABEL.get(self.state, "working")
        if self.state is AgentState.USING_TOOL and self.tool:
            label = f"running {self.tool}"
        parts: list[Any] = [Spinner("dots12", text=Text(f" {label}…", style="glacier"),
                                    style="sky")]
        if self.thinking:
            tail = self.thinking[-600:]
            parts.append(Text(tail, style="mist.italic"))
        if self.content:
            parts.append(Markdown(self.content))
        return Group(*parts)

    def start(self) -> None:
        if self.live is None:
            self.live = Live(self._render(), console=console, refresh_per_second=15,
                             transient=True)
            self.live.start()

    def update(self) -> None:
        self.start()
        assert self.live is not None
        self.live.update(self._render())

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None

    def reset_text(self) -> None:
        self.content = ""
        self.thinking = ""


class CLI:
    def __init__(self, model: str | None = None, *, llm: Any = None,
                 db_path: str | None = None) -> None:
        self.settings = get_settings()
        self.llm = llm or OllamaClient(self.settings.ollama_url,
                                       timeout_s=self.settings.request_timeout_s)
        self.memory = MemorySystem(self.settings, self.llm, db_path=db_path)
        set_memory(self.memory)

        async def learn(goal: str, answer: str, model_name: str) -> list[dict[str, Any]]:
            if not self.settings.auto_memory:
                return []
            return await self.memory.extract_facts(self.llm, model_name, goal, answer)

        self.agent = AetherAgent(self.settings, llm=self.llm,
                                 memory_provider=self.memory.build_context, after_turn=learn)
        if model:
            self.agent.set_model(model)
        self.session: Session = self.memory.session()
        self.view = StreamView()
        self.verbose = False
        self.session_stats: list[dict[str, Any]] = []

    # ------------------------------------------------------------- rendering
    def handle(self, e: AgentEvent) -> None:
        d = e.data
        v = self.view
        match e.type:
            case EventType.STATUS:
                v.state = AgentState(d["state"])
                v.tool = d.get("tool", "")
                if v.state is not AgentState.IDLE:
                    v.update()
            case EventType.TOKEN:
                if d.get("channel") == "thinking":
                    v.thinking += d["text"]
                else:
                    v.content += d["text"]
                v.update()
            case EventType.THOUGHT:
                v.stop()
                style = "warn" if d.get("kind") == "retry" else "periwinkle"
                console.print(Text("  ◌ ", style=style) + Text(d["text"].strip(), style=f"{style}.italic"))
                v.reset_text()
            case EventType.TOOL_START:
                v.stop()
                risky = d.get("risk") == Risk.REQUIRES_APPROVAL.value
                badge = Text(" needs approval ", style="black on #FCD34D") if risky else Text("")
                console.print(Text("  ⚙ ", style="sky") + Text(d["name"], style="glacier.bold") +
                              Text(f"  {_short_args(d.get('args', {}))} ", style="mist") + badge)
            case EventType.APPROVAL_REQUIRED:
                v.stop()
                self._ask_approval(d)
            case EventType.APPROVAL_RESOLVED:
                if d["approved"]:
                    console.print(Text("    ✓ approved", style="seafoam"))
                else:
                    console.print(Text(f"    ✗ denied {d.get('reason') or ''}".rstrip(), style="danger"))
            case EventType.TOOL_RESULT:
                v.stop()
                icon, style = ("✓", "seafoam") if d["ok"] else ("✗", "danger")
                body = d["output"] if self.verbose else _preview(d["output"])
                head = Text(f"    {icon} {d['duration_s']:.2f}s ", style=style)
                console.print(head + Text(body.replace("\n", "\n      "), style="mist"))
            case EventType.DONE:
                v.stop()
                stats = d.get("stats", {})
                self.session_stats.append(stats)
                console.print()
                console.print(Text(f"◉ {self.settings.agent_name}", style="glacier.bold"))
                console.print(Markdown(d["answer"]))
                if stats:
                    console.print(Text(f"  ⏱ {stats.get('summary', '')}", style="mist"))
                v.reset_text()
            case EventType.MEMORY:
                for fact in d.get("saved", []):
                    console.print(Text("  🧠 remembered: ", style="seafoam") +
                                  Text(fact["text"], style="mist"))
            case EventType.ERROR:
                v.stop()
                msg = Text(d["message"], style="danger.bold")
                if d.get("hint"):
                    msg.append(f"\n→ {d['hint']}", style="frost")
                console.print(Panel(msg, border_style="danger", title="Something went wrong"))
                v.reset_text()

    def _ask_approval(self, d: dict[str, Any]) -> None:
        args_json = json.dumps(d.get("args", {}), indent=2, ensure_ascii=False)
        body = Group(
            Text(d.get("summary", ""), style="frost"),
            Text(""),
            Syntax(args_json[:3000], "json", theme="ansi_dark", word_wrap=True),
        )
        console.print(Panel(body, border_style="warn", padding=(1, 2),
                            title=Text(f"⚠  {self.settings.agent_name} wants to run "
                                       f"{d['name']}", style="warn.bold")))
        try:
            ok = Confirm.ask("[warn]Approve this action?[/]", default=False, console=console)
        except (EOFError, KeyboardInterrupt):
            ok = False
        self.agent.broker.resolve(d["approval_id"], ok, "" if ok else "Denied in the terminal.")

    # ------------------------------------------------------------------ run
    async def run_goal(self, goal: str) -> None:
        self.view.state = AgentState.THINKING
        self.view.update()
        try:
            async with contextlib.aclosing(self.agent.run(goal, self.session)) as events:
                async for event in events:
                    self.handle(event)
        finally:
            self.view.stop()

    async def banner(self) -> bool:
        title = Text("◉ ", style="sky") + Text("A E T H E R", style="glacier.bold")
        title.append(f"   v{__version__} · local autonomous agent", style="mist")
        try:
            model = await self.agent.ensure_model()
        except OllamaError as e:
            console.print(Panel(Group(title, Text(""), Text(e.message, style="danger"),
                                      Text(f"→ {e.hint}", style="frost")),
                                border_style="danger"))
            return False
        info = Text(f"brain ", style="mist") + Text(model, style="periwinkle")
        info.append(f" · {len(REGISTRY.all())} tools", style="mist")
        facts, docs = len(self.memory.facts.all()), len(self.memory.knowledge.documents())
        tasks = len(self.memory.tasks.list())
        info.append(f" · {facts} memories · {docs} documents · {tasks} open tasks", style="mist")
        lines: list[Any] = [title, info]
        if self.agent.model_note:
            lines.append(Text(f"note: {self.agent.model_note}", style="warn"))
        lines.append(Text("Type a goal, or /help.", style="mist"))
        console.print(Panel(Group(*lines), border_style="sky", padding=(0, 2)))
        return True

    # -------------------------------------------------------------- commands
    async def command(self, line: str) -> bool:
        cmd, _, arg = line[1:].strip().partition(" ")
        arg = arg.strip()
        if cmd in {"exit", "quit", "q"}:
            return False
        if cmd == "help":
            console.print(Markdown(HELP))
        elif cmd == "tools":
            table = Table(border_style="sky", header_style="glacier")
            table.add_column("tool")
            table.add_column("category", style="mist")
            table.add_column("safety")
            for t in sorted(REGISTRY.all(), key=lambda t: (t.category, t.name)):
                safety = ("[warn]needs approval[/]" if t.risk is Risk.REQUIRES_APPROVAL else
                          "[warn]sometimes[/]" if t.risk_check else "[seafoam]safe[/]")
                table.add_row(t.name, t.category, safety)
            console.print(table)
        elif cmd == "model":
            if arg:
                self.agent.set_model(arg)
                console.print(Text(f"Brain → {arg}", style="periwinkle"))
            else:
                try:
                    models = await self.agent.llm.list_models()  # type: ignore[attr-defined]
                except OllamaError as e:
                    console.print(Text(f"{e.message} → {e.hint}", style="danger"))
                    return True
                for m in models:
                    mark = "◉" if m == self.agent.model else "○"
                    console.print(Text(f" {mark} {m}", style="glacier" if mark == "◉" else "mist"))
        elif cmd == "stats":
            self._print_stats()
        elif cmd == "verbose":
            self.verbose = not self.verbose
            console.print(Text(f"Full tool output {'on' if self.verbose else 'off'}.", style="mist"))
        elif cmd == "reset":
            self.session = self.memory.session()
            self.session_stats.clear()
            console.print(Text("New conversation started. Long-term memory is kept.", style="seafoam"))
        elif cmd == "ingest":
            await self._ingest(arg)
        elif cmd == "docs":
            docs = self.memory.knowledge.documents()
            if not docs:
                console.print(Text("The knowledge vault is empty. Add files with /ingest <file>.",
                                   style="mist"))
            for d in docs:
                state = (f"[seafoam]ready[/] · {d['chunks']} passages" if d["status"] == "ready"
                         else f"[warn]{d['status']}[/]")
                console.print(f"  #{d['id']} [glacier]{d['filename']}[/] · {d['pages']} pages · {state}")
                if d["error"]:
                    console.print(Text(f"      {d['error']}", style="mist"))
        elif cmd == "memory":
            facts = self.memory.facts.all()
            if not facts:
                console.print(Text("Nothing remembered yet. Tell me about yourself!", style="mist"))
            for f in facts:
                tag = "auto" if f["source"] == "auto" else "you"
                console.print(f"  [glacier]#{f['id']}[/] [mist]{f['category']} · {tag}[/]  {f['text']}")
        elif cmd == "forget":
            if arg.isdigit() and self.memory.facts.delete(int(arg)):
                console.print(Text(f"Forgot memory #{arg}.", style="seafoam"))
            else:
                console.print(Text("Usage: /forget <id> (see /memory).", style="warn"))
        elif cmd == "tasks":
            tasks = self.memory.tasks.list()
            if not tasks:
                console.print(Text("No open tasks. 🎉", style="seafoam"))
            for t in tasks:
                style = "danger" if t["overdue"] else "glacier"
                console.print(f"  [{style}]#{t['id']}[/] [mist]{t['kind']}[/] {t['title']} "
                              f"[{style}]· {t['when']}[/]")
        elif cmd == "timetable":
            classes = self.memory.tasks.classes()
            if not classes:
                console.print(Text("No classes yet. Try: \"I have DBMS every Monday 9 to 10 in "
                                   "room 204\"", style="mist"))
            for c in classes:
                where = f" · {c['location']}" if c["location"] else ""
                console.print(f"  [glacier]#{c['id']}[/] {c['day'][:3]} {c['start']}–{c['end']} "
                              f"{c['course']}[mist]{where}[/]")
        elif cmd == "sessions":
            for i, ses in enumerate(self.memory.store.list_sessions(15), 1):
                mark = "◉" if ses["id"] == self.session.id else "○"
                console.print(f"  {mark} [glacier]{i:>2}[/] {ses['title']} [mist]· "
                              f"{ses['turns']} turn(s)[/]")
        elif cmd == "resume":
            sessions = self.memory.store.list_sessions(15)
            if arg.isdigit() and 1 <= int(arg) <= len(sessions):
                chosen = sessions[int(arg) - 1]
                self.session = self.memory.session(chosen["id"])
                console.print(Text(f"Continuing \"{chosen['title']}\" "
                                   f"({len(self.session.turns)} turns).", style="seafoam"))
            else:
                console.print(Text("Usage: /resume <n> (see /sessions).", style="warn"))
        else:
            console.print(Text(f"Unknown command /{cmd}. Try /help.", style="warn"))
        return True

    async def _ingest(self, arg: str) -> None:
        import shlex
        try:
            paths = [Path(p).expanduser() for p in shlex.split(arg)]
        except ValueError:
            paths = [Path(arg).expanduser()]
        if not paths:
            console.print(Text("Usage: /ingest <file> [more files…]  (PDF, MD or TXT)", style="warn"))
            return
        for path in paths:
            if not path.is_file():
                console.print(Text(f"  ✗ {path} isn't a file.", style="danger"))
                continue
            with console.status(Text(f" indexing {path.name}…", style="glacier"),
                                spinner="dots12", spinner_style="sky") as status:
                def progress(p: dict[str, Any]) -> None:
                    pct = int(100 * float(p.get("progress", 0)))
                    status.update(Text(f" {p.get('status', 'indexing')} {path.name}… {pct}%",
                                       style="glacier"))
                try:
                    doc = await self.memory.knowledge.ingest(path, on_progress=progress)
                except KnowledgeError as e:
                    console.print(Text(f"  ✗ {path.name}: {e}", style="danger"))
                    continue
            if doc.get("status") == "ready":
                console.print(Text(f"  ✓ {doc['filename']}: {doc['pages']} pages, "
                                   f"{doc['chunks']} passages indexed", style="seafoam"))
                if doc.get("error"):
                    console.print(Text(f"    {doc['error']}", style="warn"))
            else:
                console.print(Text(f"  ✗ {doc.get('filename', path.name)}: {doc.get('error')}",
                                   style="danger"))

    def _print_stats(self) -> None:
        if not self.session_stats:
            console.print(Text("No replies yet.", style="mist"))
            return
        s = self.session_stats
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[mist]replies[/]", str(len(s)))
        table.add_row("[mist]tokens[/]", f"{sum(x['prompt_tokens'] for x in s):,} in · "
                                         f"{sum(x['output_tokens'] for x in s):,} out")
        total = sum(x["wall_s"] for x in s)
        table.add_row("[mist]time[/]", f"{fmt_seconds(total)} total · "
                                       f"{fmt_seconds(total / len(s))} average")
        table.add_row("[mist]peak context[/]", f"{max(x['context_pct'] for x in s):.0f}% of "
                                               f"{self.settings.context_length:,}")
        table.add_row("[mist]tool calls[/]", str(sum(len(x["tools"]) for x in s)))
        console.print(Panel(table, title="session", border_style="sky", expand=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="AETHER, a local autonomous agent")
    parser.add_argument("goal", nargs="*", help="answer this one goal and exit")
    parser.add_argument("--model", help="Ollama model to use (default from settings)")
    args = parser.parse_args()

    cli = CLI(model=args.model)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def run(coro: Any) -> None:
        task = loop.create_task(coro)
        try:
            loop.run_until_complete(task)
        except KeyboardInterrupt:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt):
                loop.run_until_complete(task)
            cli.view.stop()
            console.print(Text("  ⏹ stopped", style="warn"))

    try:
        loop.run_until_complete(cli.banner())
        if args.goal:
            run(cli.run_goal(" ".join(args.goal)))
            return
        while True:
            try:
                line = console.input("\n[sky.bold]›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith("/"):
                if not loop.run_until_complete(cli.command(line)):
                    break
                continue
            run(cli.run_goal(line))
        console.print(Text("\nSee you soon. ◉", style="glacier"))
    finally:
        with contextlib.suppress(Exception):
            loop.run_until_complete(cli.agent.llm.aclose())  # type: ignore[attr-defined]
        cli.memory.close()
        loop.close()


if __name__ == "__main__":
    sys.exit(main())
