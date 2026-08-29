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

    # Database (remote PostgreSQL — always required)
    database_url: str = "postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub"

    # Database (local SQLite — dual-DB mode; None = single-PG server mode)
    database_local_url: str | None = None

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

    # ─── Neo4j ───
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    enable_graph: bool = False
    kg_max_hops: int = 2
    kg_weight: float = 0.0

    # ─── Kafka (optional) ───
    kafka_brokers: str = ""

    # ─── Redis (removed in dual-DB migration; kept for backward-compat no-op) ───
    redis_url: str = ""

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
    rag_keyword_weight: float = 0.3
    rag_milvus_dim: int = 1024
    rag_rerank_enabled: bool = True
    rag_rerank_preview_len: int = 200

    # ─── RAG: Image extraction ───
    rag_extract_images: bool = True
    rag_image_caption_enabled: bool = False

    # ─── RAG: Chunking presets ───
    rag_chunk_preset: str = "general"
    rag_chunk_parser_config: str = ""  # JSON string for per-parser overrides

    # ─── RAG: Concurrency control ───
    rag_embed_concurrency: int = 5
    rag_search_concurrency: int = 8
    rag_graph_concurrency: int = 5
    rag_graph_neo4j_concurrency: int = 8
    milvus_bm25_drop_ratio_search: float = 0.0

    # ─── RAG: Graph auto-build ───
    rag_graph_auto_build: bool = True
    rag_graph_max_extraction_attempts: int = 3
    rag_graph_retry_delays: str = "2.0,10.0"  # comma-separated seconds

    # ─── RAG Task Queue ───
    rag_task_worker_interval: int = 5
    rag_task_max_retries: int = 3
    rag_task_worker_enabled: bool = True

    # ─── OCR engines ───
    ocr_engine: str = "auto"  # 'auto' | 'none' | 'rapidocr' | 'mineru' | 'deepseek-ocr' | 'paddleocr' | ...
    ocr_rapid_ocr_path: str = ""
    ocr_mineru_url: str = ""
    ocr_mineru_official_key: str = ""
    ocr_deepseek_ocr_key: str = ""
    ocr_pp_structure_url: str = ""  # PaddleX service URL for PP-Structure-V3
    ocr_paddleocr_key: str = ""  # PaddleOCR cloud API token

    # ─── Eval LLM (independent from RAG LLM) ───
    eval_llm_api_key: str | None = None
    eval_llm_api_url: str | None = None
    eval_llm_model: str | None = None
    eval_dataset_llm_api_key: str | None = None
    eval_dataset_llm_api_url: str | None = None
    eval_dataset_llm_model: str | None = None

    # ─── Session Metadata (custom_adapter prompt injection) ───
    default_language: str = "zh-CN"
    default_timezone: str = "GMT+8"
    default_location: str = "auto"  # 'auto' → IP geolocation; or set to e.g. 'Chongqing'

    # ─── Memory (file-native) ───
    memory_workspace_dir: str = ""  # empty → defaults to <data_dir>/memory
    memory_auto_dream_threshold: int = 5
    memory_auto_dream_cron: str = "23:00"
    memory_auto_dream_max_units: int = 5
    memory_dream_topic_count: int = 3
    memory_dream_topic_diversity_days: int = 7
    memory_search_top_k: int = 10
    memory_bm25_weight: float = 0.3
    memory_vector_weight: float = 0.7
    # DEPRECATED: wikilink no longer participates in RRF ranking (post-processing only)
    memory_wikilink_weight: float = 0.3
    memory_rrf_k: int = 60
    memory_chunk_size: int = 512
    memory_chunk_min_size: int = 100

    @property
    def memory_workspace_path(self) -> Path:
        """Get memory workspace root as resolved Path."""
        p = self.memory_workspace_dir or str(self.data_path / "memory")
        return Path(p).resolve()

    # ─── Auth ───
    jwt_secret: str = ""
    jwt_access_token_expiry: int = 315360000  # seconds (10 years — effectively non-expiring)
    jwt_refresh_token_expiry: int = 315360000  # seconds (10 years — effectively non-expiring)
    allow_registration: bool = True
    vip_login_enabled: bool = False
    default_user_email: str = "admin@local"
    default_user_password: str = ""

    # ─── ReAct Loop ───
    # When True, SDK agents (Custom) use the AgentRunner ReAct loop (call_once).
    # Set to False to fall back to the legacy adapter.stream() path.
    use_react_loop: bool = True
    # Optional Custom tool-turn fuse (None/0 = off). When hit, uses soft→forced
    # wrap-up pipeline — not a product default max-steps cap.
    max_tool_turns: int | None = None
    # In-memory compaction pipeline. When True, ReAct loop uses escalating
    # mask→fold at 0.75/0.88 ratios. When False, falls back to legacy
    # single-point _mid_run_compact at 0.85.
    compact_pipeline_enabled: bool = True
    # Feature flag for unified cross-run compaction pipeline. When True
    # (default), build_history_for uses CompactMessage + run_compact_pipeline_unified.
    compact_use_unified_pipeline: bool = True

    # ─── Summary LLM (Session Memory extraction — cheap model) ───
    # Independent from the main conversation model. Falls back to
    # DEEPSEEK_API_KEY when summary_llm_api_key is empty.
    summary_llm_provider: str = "deepseek"
    summary_llm_model: str = "deepseek-chat"
    summary_llm_api_key: str | None = None
    summary_llm_base_url: str | None = None

    # ─── Verify Stage (P2 O6) ───
    enable_verify_stage: bool = True

    # ─── Load-Aware Routing (P2 O7) ───
    enable_load_aware_routing: bool = True
    max_concurrent_tasks_per_agent: int = 2

    # ─── Observability (OpenTelemetry + Arize Phoenix) ───
    trace_enabled: bool = True
    phoenix_endpoint: str = "http://localhost:4317"
    phoenix_ui_url: str = "http://localhost:6006"
    eval_rule_enabled: bool = True
    eval_judge_enabled: bool = False

    # ─── Aeval evaluation harness (eval_harness sub-app at /api/eval) ───
    # Disabled by default; when enabled without an injected runner, only the
    # storage-backed endpoints work and POST /runs returns 503.
    eval_harness_enabled: bool = False

    # ─── Aeval AChat integration (eval_integration, change ②) ───
    # Injected runner wiring: create_aeval_runner() reads these. Eval mode
    # requires an explicit target agent — no default (装配缺凭证时报明确缺失项).
    eval_agent_id: str = ""
    # AChat API base the runner calls back into; empty → http://127.0.0.1:<port>.
    eval_api_base: str = ""
    # Bearer JWT for the runner's HTTP calls. Empty → mint an in-process token
    # for the default user (default_user_email).
    eval_user_token: str = ""
    # Per-trial completion wait timeout (seconds).
    eval_run_timeout: float = 300.0
    # Aeval result storage path; empty → <data_dir>/aeval.db.
    eval_aeval_db_path: str = ""
    # Aeval judge LLM (LLM output-quality metrics; AEVAL_JUDGE_* takes
    # priority, eval_llm_* then the OpenAI key are fallbacks).
    aeval_judge_api_key: str | None = None
    aeval_judge_api_url: str | None = None
    aeval_judge_model: str | None = None

    # ─── Obsidian Sync ───
    obsidian_max_embed_depth: int = 2
    obsidian_default_ignore: list[str] = [".obsidian/", "Templates/"]

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
        ("SUMMARY_LLM_PROVIDER", s.summary_llm_provider),
        ("SUMMARY_LLM_MODEL", s.summary_llm_model),
        ("SUMMARY_LLM_API_KEY", s.summary_llm_api_key),
        ("SUMMARY_LLM_BASE_URL", s.summary_llm_base_url),
    ):
        if value and not os.environ.get(name):
            os.environ[name] = value


def ensure_jwt_secret() -> None:
    """Validate that JWT_SECRET is set and sufficiently long (>= 32 chars).

    Called at startup. In test mode (DEBUG=true with no secret) a dev secret is
    generated so tests can run without configuration.
    """
    import secrets as _secrets

    s = get_settings()
    if not s.jwt_secret:
        if s.debug:
            s.jwt_secret = _secrets.token_urlsafe(48)
            return
        raise RuntimeError(
            "JWT_SECRET is not set. Set it in backend/.env or as an environment "
            "variable (must be at least 32 characters)."
        )
    if len(s.jwt_secret) < 32:
        raise RuntimeError(
            f"JWT_SECRET is too short ({len(s.jwt_secret)} chars); must be at least 32 characters."
        )
