"""Application settings, loaded from the environment and `.env`.

Settings are immutable and read exactly once per process via `get_settings()`.
Secrets are typed as `SecretStr` so they cannot be printed or logged by accident;
call `.get_secret_value()` at the single point of use and never before.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from linkedos.core.errors import ConfigError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Everything the app needs to know that is not code.

    Field names map to upper-case environment variables: `anthropic_api_key` is
    read from `ANTHROPIC_API_KEY`. Values in the real environment win over `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Credentials. Empty by default so the skeleton boots with no `.env` at all;
    # each integration validates its own key at the point it is first used.
    anthropic_api_key: SecretStr = SecretStr("")
    linkedin_client_id: str = ""
    linkedin_client_secret: SecretStr = SecretStr("")
    linkedin_redirect_uri: str = "http://localhost:8501/oauth/callback"

    # Local services.
    ollama_base_url: str = "http://localhost:11434"

    # Storage. `db_path` is also the anchor for logs and backups.
    db_path: Path = Path("data/linkedos.db")

    # Operations.
    log_level: LogLevel = "INFO"
    monthly_budget_usd: float = 30.0

    @property
    def data_dir(self) -> Path:
        """Directory holding the database, logs, and backups."""
        return self.db_path.parent

    @property
    def log_dir(self) -> Path:
        """Directory holding rotating log files."""
        return self.data_dir / "logs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructing them on first call.

    Raises:
        ConfigError: if the environment cannot produce a valid `Settings`.
    """
    try:
        return Settings()
    except ValidationError as exc:
        # `exc` renders field names and constraints, never the offending secret.
        raise ConfigError(f"invalid configuration: {exc.error_count()} field(s) rejected") from exc
