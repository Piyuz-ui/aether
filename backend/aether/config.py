"""Settings, loaded from AETHER_* environment variables or backend/.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"


def _key(env: str, *more: str) -> SecretStr:
    """An optional API key, read from its usual environment variable name."""
    return Field(default=SecretStr(""),
                 validation_alias=AliasChoices(f"AETHER_{env}", env, *more))  # type: ignore[return-value]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=ENV_FILE,
                                      extra="ignore", populate_by_name=True)

    # --- model -------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    fallback_models: list[str] = Field(default_factory=lambda: [
        "qwen3:14b", "qwen3:8b", "llama3.1:8b", "qwen2.5:14b", "qwen2.5-coder:14b",
        "mistral-nemo", "llama3.2"])
    embed_model: str = "nomic-embed-text"
    vision_model: str = ""           # preferred model for images; "" = any installed vision model
    context_length: int = 16384
    temperature: float = 0.2
    # Hard cap per model call. Small models occasionally loop forever, so this turns a
    # 5-minute hang into a short, recoverable "reply was cut off".
    max_output_tokens: int = 2048
    # After a tool call arrives, wait this long for more parallel calls, then stop the stream.
    # Small models often keep generating hidden junk after a valid call.
    tool_call_grace_s: float = 2.5
    think: bool = False              # let reasoning models think out loud (shown live)
    request_timeout_s: float = 300.0

    # --- optional cloud brains (off unless a key is set; see .env.example) ---------
    gemini_api_key: SecretStr = _key("GEMINI_API_KEY", "GOOGLE_API_KEY")
    groq_api_key: SecretStr = _key("GROQ_API_KEY")
    openrouter_api_key: SecretStr = _key("OPENROUTER_API_KEY")
    anthropic_api_key: SecretStr = _key("ANTHROPIC_API_KEY")
    cloud_max_output_tokens: int = 8192     # Gemini / Groq / OpenRouter, per call
    claude_max_output_tokens: int = 16000   # Claude, per call (includes its thinking)

    def cloud_keys(self) -> dict[str, str]:
        return {"gemini": self.gemini_api_key.get_secret_value(),
                "groq": self.groq_api_key.get_secret_value(),
                "openrouter": self.openrouter_api_key.get_secret_value(),
                "claude": self.anthropic_api_key.get_secret_value()}

    # --- agent loop ----------------------------------------------------------
    max_steps: int = 8
    max_format_retries: int = 2      # re-asks when a model emits a broken tool call
    max_nudges: int = 2              # re-asks when it ends with an unfixed failure / empty promise
    max_tool_output_chars: int = 8000
    tool_timeout_s: float = 60.0
    approval_timeout_s: float = 600.0
    history_chars: int = 24000       # ≈ 6k tokens of past conversation
    auto_memory: bool = True         # learn lasting facts about the user after each answer

    # --- api -----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000", "http://127.0.0.1:3000"])
    max_upload_mb: int = 50

    # --- places --------------------------------------------------------------
    workspace_dir: Path = BACKEND_DIR / "workspace"
    mcp_config: Path = BACKEND_DIR / "mcp_servers.json"   # external MCP tool servers (optional)
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
