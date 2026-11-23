"""Settings, loaded from AETHER_* environment variables or backend/.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=BACKEND_DIR / ".env",
                                      extra="ignore")

    # --- model -------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    fallback_models: list[str] = Field(default_factory=lambda: [
        "qwen3:14b", "qwen3:8b", "llama3.1:8b", "qwen2.5:14b", "qwen2.5-coder:14b",
        "mistral-nemo", "llama3.2"])
    embed_model: str = "nomic-embed-text"
    context_length: int = 16384
    temperature: float = 0.2
    think: bool = False              # let reasoning models think out loud (shown live)
    request_timeout_s: float = 300.0

    # --- agent loop ----------------------------------------------------------
    max_steps: int = 8
    max_format_retries: int = 2      # re-asks when a model emits a broken tool call
    max_tool_output_chars: int = 8000
    tool_timeout_s: float = 60.0
    approval_timeout_s: float = 600.0
    history_chars: int = 24000       # ≈ 6k tokens of past conversation

    # --- places --------------------------------------------------------------
    workspace_dir: Path = BACKEND_DIR / "workspace"
    data_dir: Path = BACKEND_DIR / "data"

    # --- persona -------------------------------------------------------------
    agent_name: str = "AETHER"
    user_name: str = ""

    def ensure_dirs(self) -> None:
        for d in (self.workspace_dir, self.workspace_dir / "notes", self.data_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
