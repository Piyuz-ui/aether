"""MCP setup from the app: edit mcp_servers.json, import servers from Claude, and add AETHER to
Claude as an MCP server.

Secrets (env values, headers) never leave the backend in full: the UI gets them masked, and a
masked value saved back unchanged keeps the original.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

MASK = "••••••"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
BACKEND_DIR = Path(__file__).resolve().parents[2]
SERVER_SCRIPT = BACKEND_DIR / "mcp_server.py"
AETHER_KEY = "aether"


def claude_paths() -> dict[str, Path]:
    """Where Claude Desktop and Claude Code keep their MCP servers on this computer."""
    home = Path.home()
    if sys.platform == "darwin":
        desktop = home / "Library/Application Support/Claude/claude_desktop_config.json"
    elif sys.platform.startswith("win"):
        desktop = Path(os.environ.get("APPDATA", home)) / "Claude/claude_desktop_config.json"
    else:
        desktop = home / ".config/Claude/claude_desktop_config.json"
    return {"desktop": desktop, "code": home / ".claude.json"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict[str, Any], *, backup: bool = True) -> None:
    """Write JSON atomically, keeping one backup of what was there before."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".aether-backup"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------- mcp_servers.json
def masked(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    for key in ("env", "headers"):
        if isinstance(out.get(key), dict):
            out[key] = {k: (MASK if v else "") for k, v in out[key].items()}
    return out


class ServerConfig:
    def __init__(self, path: Path):
        self.path = path

    def _data(self) -> dict[str, Any]:
        data = _read_json(self.path)
        if not isinstance(data.get("mcpServers"), dict):
            data["mcpServers"] = data.pop("servers", {}) if isinstance(data.get("servers"), dict) else {}
        return data

    def servers(self) -> dict[str, dict[str, Any]]:
        return self._data()["mcpServers"]

    def save(self, name: str, cfg: dict[str, Any]) -> dict[str, Any]:
        if not NAME.match(name):
            raise ValueError("Server names use letters, digits, - and _ (up to 40).")
        data = self._data()
        old = data["mcpServers"].get(name, {})
        clean: dict[str, Any] = {}
        if cfg.get("url"):
            clean["url"] = str(cfg["url"]).strip()
            if not re.match(r"^https?://", clean["url"]):
                raise ValueError("A remote server's URL starts with https:// (or http://).")
        elif cfg.get("command"):
            clean["command"] = str(cfg["command"]).strip()
            clean["args"] = [str(a) for a in cfg.get("args") or []]
        else:
            raise ValueError("Give a command (a local server) or a URL (a remote one).")
        for key in ("env", "headers"):
            given = cfg.get(key) or {}
            if given:
                prev = old.get(key) or {}
                # a masked value means "unchanged": keep the stored secret
                clean[key] = {str(k): (prev.get(k, "") if v == MASK else str(v)) for k, v in given.items() if k}
        clean["approval"] = cfg.get("approval") if cfg.get("approval") in ("auto", "always", "never") else "auto"
        if cfg.get("include"):
            clean["include"] = [str(t) for t in cfg["include"]]
        if cfg.get("disabled"):
            clean["disabled"] = True
        if old.get("cwd") and "command" in clean:
            clean["cwd"] = old["cwd"]
        data["mcpServers"][name] = clean
        _write_json(self.path, data, backup=False)
        os.chmod(self.path, 0o600)  # may hold tokens
        return clean

    def remove(self, name: str) -> bool:
        data = self._data()
        if data["mcpServers"].pop(name, None) is None:
            return False
        _write_json(self.path, data, backup=False)
        return True


# --------------------------------------------------- servers configured in Claude
def _claude_entry(name: str, cfg: dict[str, Any], source: str) -> dict[str, Any]:
    kind = (cfg.get("type") or ("http" if cfg.get("url") else "stdio")).lower()
    supported = kind in ("stdio", "http", "streamable-http") and bool(cfg.get("command") or cfg.get("url"))
    return {"name": name, "source": source, "kind": "remote" if cfg.get("url") else "local",
            "command": cfg.get("url") or " ".join([cfg.get("command", ""), *map(str, cfg.get("args") or [])]).strip(),
            "supported": supported,
            "note": None if supported else ("Uses the old SSE transport, which AETHER doesn't support."
                                            if kind == "sse" else "Not a server type AETHER can run."),
            "config": cfg}


def find_claude_servers(paths: dict[str, Path] | None = None) -> list[dict[str, Any]]:
    """MCP servers set up in Claude Desktop and Claude Code (user and per-project)."""
    paths = paths or claude_paths()
    found: list[dict[str, Any]] = []
    for name, cfg in (_read_json(paths["desktop"]).get("mcpServers") or {}).items():
        if isinstance(cfg, dict) and name != AETHER_KEY:
            found.append(_claude_entry(name, cfg, "Claude Desktop"))
    code = _read_json(paths["code"])
    for name, cfg in (code.get("mcpServers") or {}).items():
        if isinstance(cfg, dict) and name != AETHER_KEY:
            found.append(_claude_entry(name, cfg, "Claude Code"))
    for project, pcfg in (code.get("projects") or {}).items():
        for name, cfg in ((pcfg or {}).get("mcpServers") or {}).items():
            if isinstance(cfg, dict) and name != AETHER_KEY:
                found.append(_claude_entry(name, cfg, f"Claude Code · {Path(project).name}"))
    return found


def import_claude_servers(config: ServerConfig, picks: list[dict[str, str]],
                          paths: dict[str, Path] | None = None) -> list[str]:
    """Copy the chosen Claude servers into mcp_servers.json. New ones ask before every call."""
    by_key = {(e["source"], e["name"]): e for e in find_claude_servers(paths)}
    added = []
    existing = config.servers()
    for p in picks:
        e = by_key.get((p.get("source", ""), p.get("name", "")))
        if not e or not e["supported"]:
            continue
        name = e["name"] if e["name"] not in existing else f"{e['name']}-claude"
        name = re.sub(r"[^A-Za-z0-9_-]", "-", name)[:40]
        cfg = e["config"]
        config.save(name, {"command": cfg.get("command"), "args": cfg.get("args"), "env": cfg.get("env"),
                           "url": cfg.get("url"), "headers": cfg.get("headers"), "approval": "always"})
        added.append(name)
    return added


# ------------------------------------------------------ AETHER inside Claude
def aether_server_entry() -> dict[str, Any]:
    """How Claude should start AETHER's MCP server: this backend's Python and mcp_server.py."""
    return {"command": sys.executable, "args": [str(SERVER_SCRIPT)]}


def claude_cli() -> str | None:
    for candidate in (shutil.which("claude"), str(Path.home() / ".claude/local/claude"),
                      str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def aether_in_claude(paths: dict[str, Path] | None = None) -> dict[str, Any]:
    paths = paths or claude_paths()
    entry = aether_server_entry()
    return {
        "desktop": {"connected": AETHER_KEY in (_read_json(paths["desktop"]).get("mcpServers") or {}),
                    "installed": paths["desktop"].parent.exists(), "path": str(paths["desktop"])},
        "code": {"connected": AETHER_KEY in (_read_json(paths["code"]).get("mcpServers") or {}),
                 "installed": claude_cli() is not None or paths["code"].exists()},
        "command": " ".join([f'"{entry["command"]}"', *(f'"{a}"' for a in entry["args"])]),
        "code_command": f'claude mcp add --scope user {AETHER_KEY} -- "{entry["command"]}" "{entry["args"][0]}"',
    }


def connect_claude(target: str, connect: bool, paths: dict[str, Path] | None = None,
                   runner: Any = subprocess.run) -> str:
    """Add AETHER to (or remove it from) Claude Desktop or Claude Code. Returns what to do next."""
    paths = paths or claude_paths()
    if target == "desktop":
        data = _read_json(paths["desktop"])
        servers = data.setdefault("mcpServers", {})
        if connect:
            servers[AETHER_KEY] = aether_server_entry()
        else:
            servers.pop(AETHER_KEY, None)
        _write_json(paths["desktop"], data)
        return "Quit and reopen Claude Desktop to " + ("load AETHER's tools." if connect else "unload them.")
    if target == "code":
        cli = claude_cli()
        if cli is None:
            raise FileNotFoundError("The `claude` command wasn't found. Run this in a terminal instead: "
                                    + aether_in_claude(paths)["code_command"])
        entry = aether_server_entry()
        if connect:
            runner([cli, "mcp", "remove", "--scope", "user", AETHER_KEY], capture_output=True, timeout=30)
            args = [cli, "mcp", "add", "--scope", "user", AETHER_KEY, "--", entry["command"], *entry["args"]]
        else:
            args = [cli, "mcp", "remove", "--scope", "user", AETHER_KEY]
        done = runner(args, capture_output=True, text=True, timeout=30)
        if getattr(done, "returncode", 0) != 0:
            raise RuntimeError((getattr(done, "stderr", "") or "claude mcp failed").strip()[:300])
        return "Start a new Claude Code session to " + ("use AETHER's tools." if connect else "drop them.")
    raise ValueError("target must be 'desktop' or 'code'")

