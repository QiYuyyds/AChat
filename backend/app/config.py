"""Application configuration using pydantic-settings."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub"

    # API Keys
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    ark_api_key: str | None = None
    tavily_api_key: str | None = None

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # CORS
    cors_origins: str = "http://localhost:3000"

    # Workspace
    workspace_root: str = "../.agenthub-data/workspaces"

    # AChat data dir (deployments live under <data_dir>/deployments). Mirrors
    # the TS AGENTHUB_DATA_DIR; defaults to the same dir the SQLite DB sits in.
    data_dir: str = "../.agenthub-data"

    # ─── Milvus ───
    milvus_host: str = ""
    milvus_port: int = 19530

    # ─── Elasticsearch ───
    es_addresses: str = ""  # comma-separated

    # ─── Neo4j ───
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    enable_graph: bool = False
    kg_max_hops: int = 2
    kg_weight: float = 0.3

    # ─── Kafka (optional) ───
    kafka_brokers: str = ""

    # ─── Embedding ───
    embedding_api_key: str | None = None
    embedding_api_url: str | None = None
    embedding_model: str | None = None

    # ─── LLM (for RAG rewrite/rerank/answer/KG extraction) ───
    llm_api_key: str | None = None
    llm_api_url: str | None = None
    llm_model: str | None = None

    # ─── RAG ───
    rag_chunk_size: int = 200
    rag_chunk_overlap: int = 50
    rag_top_k: int = 3
    rag_rrf_constant_k: int = 60
    rag_semantic_weight: float = 0.7
    rag_milvus_dim: int = 1024
    rag_rewrite_enabled: bool = True
    rag_rewrite_num_queries: int = 3
    rag_rerank_enabled: bool = True
    rag_rerank_preview_len: int = 200

    # ─── Memory ───
    memory_short_term_max_turns: int = 10
    memory_long_term_top_k: int = 3
    memory_consolidation_similarity: float = 0.80
    memory_consolidation_dedup: float = 0.95
    memory_consolidation_ttl_days: int = 30
    memory_consolidation_decay_rate: float = 0.995
    memory_consolidation_min_importance: float = 0.3
    memory_consolidation_trigger: int = 5

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
        """Get AChat data dir as a resolved Path object."""
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
        ("TAVILY_API_KEY", s.tavily_api_key),
    ):
        if value and not os.environ.get(name):
            os.environ[name] = value
