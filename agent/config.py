"""Central settings. Everything can be overridden with environment variables or a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Tiny .env loader so we don't need an extra dependency."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DATA_DIR = Path(os.getenv("AGENT_DATA_DIR", ROOT / "data")).expanduser()
DOCS_DIR = Path(os.getenv("AGENT_DOCS_DIR", ROOT / "documents")).expanduser()
WORKSPACE_DIR = Path(os.getenv("AGENT_WORKSPACE_DIR", ROOT / "workspace")).expanduser()

for _d in (DATA_DIR, DOCS_DIR, WORKSPACE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    agent_name: str = os.getenv("AGENT_NAME", "Atlas")
    user_name: str = os.getenv("AGENT_USER_NAME", "")
    model: str = os.getenv("AGENT_MODEL", "qwen3:14b")
    embed_model: str = os.getenv("AGENT_EMBED_MODEL", "nomic-embed-text")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    context_length: int = int(os.getenv("AGENT_CONTEXT_LENGTH", "16384"))
    temperature: float = float(os.getenv("AGENT_TEMPERATURE", "0.3"))
    max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "10"))
    history_turns: int = int(os.getenv("AGENT_HISTORY_TURNS", "12"))
    think: bool = os.getenv("AGENT_THINK", "false").lower() == "true"
