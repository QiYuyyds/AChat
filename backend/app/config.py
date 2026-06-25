"""Application configuration using pydantic-settings."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///../.agenthub-data/agenthub.db"

    # API Keys
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    ark_api_key: str | None = None

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # CORS
    cors_origins: str = "http://localhost:3000"

    # Workspace
    workspace_root: str = "../.agenthub-data/workspaces"

    # AgentHub data dir (deployments live under <data_dir>/deployments). Mirrors
    # the TS AGENTHUB_DATA_DIR; defaults to the same dir the SQLite DB sits in.
    data_dir: str = "../.agenthub-data"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def workspace_path(self) -> Path:
        """Get workspace root as Path object."""
        return Path(self.workspace_root).resolve()

    @property
    def data_path(self) -> Path:
        """Get AgentHub data dir as a resolved Path object."""
        return Path(self.data_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def apply_env_overrides() -> None:
    """Bridge API keys from backend/.env into os.environ.

    pydantic-settings parses .env into Settings fields, but the adapter key
    resolution (settings_service / agent_runner) reads os.environ directly. Mirror
    Next.js's .env-into-process.env behaviour so keys placed in backend/.env are
    honoured as the env-fallback layer. Never overwrites a real shell env var.
    """
    s = get_settings()
    for name, value in (
        ("ANTHROPIC_API_KEY", s.anthropic_api_key),
        ("OPENAI_API_KEY", s.openai_api_key),
        ("DEEPSEEK_API_KEY", s.deepseek_api_key),
        ("ARK_API_KEY", s.ark_api_key),
    ):
        if value and not os.environ.get(name):
            os.environ[name] = value
