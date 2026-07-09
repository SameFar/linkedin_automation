"""Settings load from `.env`, fall back to documented defaults, and hide secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from linkedos.core.config import Settings, get_settings

SECRET = "sk-ant-not-a-real-key"


def test_defaults_apply_when_env_is_empty(isolated_env: Path) -> None:
    settings = get_settings()

    assert settings.db_path == Path("data/linkedos.db")
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.log_level == "INFO"
    assert settings.monthly_budget_usd == 30.0
    assert settings.anthropic_api_key == SecretStr("")


def test_values_load_from_dotenv(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        f"ANTHROPIC_API_KEY={SECRET}\n"
        "LINKEDIN_CLIENT_ID=client-123\n"
        "LINKEDIN_CLIENT_SECRET=shhh\n"
        "LINKEDIN_REDIRECT_URI=http://localhost:9999/cb\n"
        "OLLAMA_BASE_URL=http://localhost:12345\n"
        "DB_PATH=custom/spot.db\n"
        "LOG_LEVEL=DEBUG\n"
        "MONTHLY_BUDGET_USD=12.5\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.anthropic_api_key.get_secret_value() == SECRET
    assert settings.linkedin_client_id == "client-123"
    assert settings.linkedin_client_secret.get_secret_value() == "shhh"
    assert settings.linkedin_redirect_uri == "http://localhost:9999/cb"
    assert settings.ollama_base_url == "http://localhost:12345"
    assert settings.db_path == Path("custom/spot.db")
    assert settings.log_level == "DEBUG"
    assert settings.monthly_budget_usd == 12.5


def test_real_environment_beats_dotenv(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated_env / ".env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    get_settings.cache_clear()

    assert get_settings().log_level == "ERROR"


def test_secrets_never_render(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(f"ANTHROPIC_API_KEY={SECRET}\n", encoding="utf-8")
    get_settings.cache_clear()

    settings = get_settings()

    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert SECRET not in settings.model_dump_json()


def test_settings_are_frozen(isolated_env: Path) -> None:
    settings = get_settings()

    with pytest.raises(ValueError, match="frozen"):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_get_settings_is_cached(isolated_env: Path) -> None:
    assert get_settings() is get_settings()


def test_derived_paths_hang_off_db_path(isolated_env: Path) -> None:
    settings = Settings(db_path=Path("/var/lib/linkedos/linkedos.db"))

    assert settings.data_dir == Path("/var/lib/linkedos")
    assert settings.log_dir == Path("/var/lib/linkedos/logs")
