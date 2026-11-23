"""Terminal chat interface."""
from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from agent import Agent, Settings
from agent.config import DOCS_DIR
from agent.metrics import fmt_seconds
from agent.storage import get_store

console = Console()

HELP = """**Commands**
- `/help` — this list
- `/attach <file> [question]` — attach a PDF/TXT/MD and ask about it (you can drag the file in)
- `/tasks` — show open tasks and deadlines
- `/stats` — tokens, context use and timing for this session
- `/memory` — show everything I remember about you
- `/tools` — list my tools
- `/model <name>` — switch Ollama model (`/model` alone lists installed ones)
- `/reset` — forget this conversation (long-term memory stays)
- `/exit` — quit"""


def _short(args: dict, limit: int = 120) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


class CLI:
    def __init__(self) -> None:
        self.status = None
        self.pending: list[Path] = []  # files attached for the next message
        self.agent = Agent(Settings(), confirm=self.confirm, on_event=self.on_event)

    # The spinner has to pause while we ask a question, or the prompt gets garbled.
    def confirm(self, name: str, args: dict) -> bool:
        if self.status:
            self.status.stop()
        console.print(Panel(json.dumps(args, indent=2, ensure_ascii=False)[:2000],
                            title=f"⚠️  {self.agent.settings.agent_name} wants to run "
                                  f"[bold]{name}[/]", border_style="yellow"))
        ok = Confirm.ask("Allow?", default=False)
        if self.status:
            self.status.start()
        return ok

    def on_event(self, kind: str, payload: dict) -> None:
        if kind == "tool_call":
            console.print(f"  [dim]🔧 {payload['name']} {_short(payload['args'])}[/]")

    def command(self, line: str) -> bool:
        """Handle a /command. Returns False when the user wants to quit."""
        cmd, _, arg = line[1:].partition(" ")
        if cmd in {"exit", "quit", "q"}:
            return False
        if cmd == "help":
            console.print(Markdown(HELP))
        elif cmd == "stats":
            self.show_stats()
        elif cmd == "attach":
            return self.attach(arg)
        elif cmd == "reset":
            self.agent.reset()
            console.print("[green]Conversation cleared.[/]")
        elif cmd == "tasks":
            from agent.tools.personal import list_tasks
            console.print(list_tasks())
        elif cmd == "memory":
            mems = self.agent.memory.all()
            console.print("\n".join(f"#{m['id']} {m['text']}" for m in mems) or "Nothing yet.")
        elif cmd == "tools":
            from agent.tools import REGISTRY
            for t in REGISTRY.values():
                flag = " [yellow](asks first)[/]" if t.dangerous else ""
                console.print(f"[cyan]{t.category:>9}[/]  {t.name}{flag}")
        elif cmd == "model":
            if arg:
                self.agent.model = arg.strip()
                console.print(f"Model → [bold]{self.agent.model}[/]")
            else:
                for m in self.agent.llm.list_models():
                    mark = "→" if m == self.agent.model else " "
                    console.print(f" {mark} {m}")
        else:
            console.print(f"Unknown command /{cmd}. Try /help.")
        return True

    def attach(self, arg: str) -> bool:
        try:
            parts = shlex.split(arg)  # handles quoted paths and drag-and-drop escapes
        except ValueError:
            parts = [arg]
        paths, words = [], []
        for part in parts:
            p = Path(part).expanduser()
            (paths if p.is_file() else words).append(p if p.is_file() else part)
        bad = [p for p in paths if p.suffix.lower() not in {".pdf", ".txt", ".md"}]
        if not paths or bad:
            console.print("[red]Give me a PDF, TXT or MD file path,[/] e.g. "
                          "/attach ~/Downloads/notes.pdf what is paging?")
            return True
        for p in paths:
            target = DOCS_DIR / p.name
            if p.resolve() != target.resolve():
                shutil.copy2(p, target)
            self.pending.append(target)
        console.print(f"[green]Attached:[/] {', '.join(p.name for p in paths)}")
        if words:
            self.ask(" ".join(words))
        else:
            console.print("Now ask your question about it.")
        return True

    def ask(self, line: str) -> None:
        attachments, self.pending = self.pending, []
        with console.status("thinking…") as status:
            self.status = status
            try:
                reply = self.agent.chat(line, attachments=attachments or None)
            except KeyboardInterrupt:
                reply = "(interrupted)"
            self.status = None
        console.print(f"\n[bold blue]{self.agent.settings.agent_name} ›[/]")
        console.print(Markdown(reply))
        last = self.agent.last_stats
        if last and last.calls:
            style = "yellow" if last.context_pct > 85 else "dim"
            console.print(f"[{style}]⏱ {last.summary()}[/]")
        for n in self.agent.last_notes:
            if f"Saved as note #{n['id']}" not in reply:
                console.print(Panel(Markdown(n["body"]), title=f"📝 {n['title']}",
                                    border_style="green"))

    def show_stats(self) -> None:
        s = self.agent.monitor.session()
        if not s["turns"]:
            console.print("No replies yet.")
            return
        table = Table(title="This session", show_header=False, box=None)
        table.add_row("Replies", str(s["turns"]))
        table.add_row("Tokens", f"{s['prompt_tokens']:,} in · {s['output_tokens']:,} out · "
                                f"{s['total_tokens']:,} total")
        table.add_row("Time", f"avg {fmt_seconds(s['avg_s'])} · total {fmt_seconds(s['total_s'])}")
        table.add_row("Peak context", f"{s['peak_context_pct']:.0f}% of "
                                      f"{self.agent.settings.context_length:,} tokens")
        table.add_row("Tool calls", str(s["tool_calls"]))
        console.print(table)
        last = self.agent.last_stats
        if last and last.calls:
            console.print(f"[dim]Last reply: {last.summary()}[/]")
            for i, c in enumerate(last.calls, 1):
                console.print(f"[dim]  call {i}: {c.prompt_tokens:,} in "
                              f"({c.cached_tokens:,} cached) · {c.output_tokens:,} out · "
                              f"read {fmt_seconds(c.prompt_s)} · write {fmt_seconds(c.gen_s)}"
                              + (f" · load {fmt_seconds(c.load_s)}" if c.load_s > 1 else "") + "[/]")

    def greet(self) -> None:
        s = self.agent.settings
        up = self.agent.llm.is_up()
        console.print(Panel.fit(
            f"[bold]{s.agent_name}[/] · model [cyan]{s.model}[/] · "
            f"Ollama {'[green]online[/]' if up else '[red]not running[/]'}\n"
            "Type a message, or /help for commands.", border_style="blue"))
        due = [t for t in get_store().list_tasks() if t["due"]][:3]
        if due:
            console.print("[bold]Coming up:[/] " +
                          " · ".join(f"{t['title']} ({t['due']})" for t in due))

    def run(self) -> None:
        self.greet()
        while True:
            try:
                line = console.input("\n[bold green]you ›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
                continue
            self.ask(line)
        console.print("\nBye! 👋")


def main() -> None:
    CLI().run()


if __name__ == "__main__":
    main()
