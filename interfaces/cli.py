"""Terminal chat interface."""
from __future__ import annotations

import json

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from agent import Agent, Settings
from agent.storage import get_store

console = Console()

HELP = """**Commands**
- `/help` — this list
- `/tasks` — show open tasks and deadlines
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
            with console.status("thinking…") as status:
                self.status = status
                try:
                    reply = self.agent.chat(line)
                except KeyboardInterrupt:
                    reply = "(interrupted)"
                self.status = None
            console.print(f"\n[bold blue]{self.agent.settings.agent_name} ›[/]")
            console.print(Markdown(reply))
        console.print("\nBye! 👋")


def main() -> None:
    CLI().run()


if __name__ == "__main__":
    main()
