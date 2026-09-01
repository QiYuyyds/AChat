"""FastAPI application entry point."""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

# On Windows the default ProactorEventLoop is required for subprocess support.
# Some libraries / env configs may switch to SelectorEventLoop which does not
# implement _make_subprocess_transport on Windows and raises NotImplementedError.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import apply_env_overrides, ensure_jwt_secret, get_settings
from app.db.engine import close_db, init_db
from app.observability import init_observability, shutdown_observability

# ── Logging configuration (AGI-memory style) ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
# Suppress noisy third-party library logs
for _noisy in ("pymilvus", "kafka", "sqlalchemy", "neo4j.notifications", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Module-level service references (accessed by tool handlers)
_memory_service = None
_rag_service = None
_rag_eval_service = None
_infrastructure = None
_app_ref = None
_document_service = None
_obsidian_sync_service = None
_kg_wired = False
_task_scheduler = None
_rag_task_worker = None


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    global _memory_service, _rag_service, _rag_eval_service, _infrastructure, _app_ref, _document_service, _obsidian_sync_service, _kg_wired, _task_scheduler, _rag_task_worker
    _app_ref = app_instance

    # Startup
    apply_env_overrides()
    ensure_jwt_secret()
    import app.services.agent_runner  # noqa: F401

    await init_db()

    # ─── Seed guide agent (小A) ───
    await _seed_guide_agent()

    # ─── Migrate baked-in agent model config to model_profiles ───
    await _migrate_agent_model_profiles()

    # ─── Init TaskSchedulerService ───
    from app.services.task_scheduler import TaskSchedulerService

    _task_scheduler = TaskSchedulerService.get_instance()

    settings = get_settings()

    # ─── Location warmup (first-token latency) ───
    # With default_location='auto', kick off the IP-geolocation probe in the
    # background NOW so the first message finds the cache warm instead of
    # injecting "Unknown" (see speed-up-first-token-latency, decision 1).
    if settings.default_location == "auto":
        try:
            from app.services.agent_runner import warm_location_cache
            warm_location_cache()
            logger.info("Location warmup scheduled (default_location='auto')")
        except Exception as e:
            logger.warning("Location warmup failed: %s", e)

    # ─── Optional source intelligence ───
    try:
        from app.code_intelligence.bootstrap import (
            build_code_intelligence_service,
            recover_code_intelligence_metadata,
        )
        from app.code_intelligence.service import configure_code_intelligence_service

        configure_code_intelligence_service(build_code_intelligence_service(settings))
        await recover_code_intelligence_metadata()
        logger.info("Code intelligence service initialized")
    except Exception as e:
        logger.warning("Code intelligence init failed: %s", e)

    # ── Observability (OTel + auto instrumentation) ──
    init_observability(settings)
    if settings.trace_enabled:
        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            FastAPIInstrumentor.instrument_app(app_instance)
            HTTPXClientInstrumentor().instrument()
            OpenAIInstrumentor().instrument()
            logger.info("Observability: auto-instrumentation enabled (FastAPI/httpx/openai)")
        except ImportError as e:
            logger.warning("Observability: auto-instrumentation skipped (%s)", e)

    # ─── Aeval: inject real AChat runner (change ②, eval_integration) ───
    # Runs here (not in create_app) because storage init is async and the
    # trace bridge must install on the initialised OTel provider (§14.1.2).
    # Needs EVAL_AGENT_ID (被评 agent); missing credentials → 明确告警, 子应用
    # 保持 503 (POST /runs) 而非崩溃。
    if settings.eval_harness_enabled:
        try:
            from agent_eval.api.app import set_runner

            from app.eval_integration.config import create_aeval_runner

            runner = await create_aeval_runner(settings)
            set_runner(runner)
            logger.info(
                "Aeval: real AChat runner injected (agent=%s, api_base=%s)",
                settings.eval_agent_id,
                settings.eval_api_base or f"http://127.0.0.1:{settings.port}",
            )
        except Exception as e:
            logger.warning("Aeval runner injection failed: %s", e)
            logger.warning(
                "Aeval: eval API mounted without a runner — POST /runs returns "
                "503. Check EVAL_AGENT_ID and related eval_* settings."
            )

    # ─── Infrastructure factory ───
    try:
        from app.infra.factory import build_infrastructure, close_infrastructure
        _infrastructure = await build_infrastructure(settings)
    except Exception as e:
        logger.warning("Infrastructure build failed: %s", e)

    # ─── MemoryService ───
    _curator_job = None
    try:
        from app.memory.memory_service import MemoryService
        _memory_service = MemoryService(settings)
        await _memory_service.initialize()
    except Exception as e:
        logger.warning("MemoryService init failed: %s", e)
        _memory_service = None

    # ─── CuratorJob (nightly memory lifecycle) ───
    if _memory_service and settings.memory_curator_enabled:
        try:
            from app.memory.curator import CuratorJob
            _curator_job = CuratorJob(settings, _memory_service)
            await _curator_job.start()
            logger.info("CuratorJob scheduled (cron=%s)", settings.memory_auto_dream_cron)
        except Exception as e:
            logger.warning("CuratorJob init failed: %s", e)
            _curator_job = None

    # ─── RAG overhaul schema migration (before RAGService init) ───
    try:
        from app.db.migrations.rag_overhaul_migration import migrate_rag_overhaul
        await migrate_rag_overhaul()
    except Exception as e:
        logger.warning("RAG overhaul migration failed: %s", e)

    # ─── user_settings RAG config migration ───
    try:
        from app.db.migrations.user_settings_rag_config import migrate_user_settings_rag_config
        await migrate_user_settings_rag_config()
    except Exception as e:
        logger.warning("user_settings RAG config migration failed: %s", e)

    # ─── RAGService ───
    try:
        from app.services.rag_service import RAGService
        _rag_service = RAGService(settings)
        # Wire infrastructure backends into RAG
        if _infrastructure and _infrastructure.milvus_client:
            _wire_milvus_to_rag(_rag_service, _infrastructure.milvus_client, settings)
        # Inject embed_fn and generate_fn for RAG search/rewrite/rerank
        embed_fn = _make_embed_fn(settings)
        if embed_fn:
            _rag_service.set_embed_fn(embed_fn)
            logger.info("RAG: embed_fn injected (model=%s)", settings.embedding_model)
        else:
            logger.warning("RAG: embed_fn not available (EMBEDDING_API_KEY not set)")

        generate_fn = _make_generate_fn(settings)
        if generate_fn:
            _rag_service.set_generate_fn(generate_fn)
            logger.info("RAG: generate_fn injected")
        else:
            logger.warning("RAG: generate_fn not available (no LLM API key)")

        # Inject generate_fn into MemoryService for auto_memory + auto_dream
        # Set MEMORY_ENABLED=false in .env to disable memory pipeline (saves API calls during RAG testing)
        _memory_enabled = os.environ.get("MEMORY_ENABLED", "true").lower() not in ("false", "0", "no", "")
        if generate_fn and _memory_service and _memory_enabled:
            _memory_service.set_generate_fn(generate_fn)
            logger.info("Memory: generate_fn injected")
        elif not _memory_enabled:
            logger.info("Memory: pipeline disabled (MEMORY_ENABLED=false)")

        # Inject embed_fn into MemoryService for vector search + indexing
        if embed_fn and _memory_service and _memory_enabled:
            _memory_service.set_embed_fn(embed_fn)
            logger.info("Memory: embed_fn injected (model=%s)", settings.embedding_model)
        elif not _memory_enabled:
            logger.info("Memory: embed_fn skipped (MEMORY_ENABLED=false)")

        # Wire KG backend if Neo4j driver and LLM are both available (KGStore belongs to RAG, independent of memory system)
        if _infrastructure and _infrastructure.neo4j_driver and generate_fn:
            _wire_kg_to_rag(
                _rag_service, _infrastructure.neo4j_driver, settings, generate_fn,
                milvus_client=_infrastructure.milvus_client,
                embed_fn=embed_fn,
            )
            _kg_wired = True

        await _rag_service.initialize()
    except Exception as e:
        logger.warning("RAGService init failed: %s", e)
        _rag_service = None

    # ─── RAGEvalService ───
    try:
        from app.rag.eval.service import RAGEvalService
        _rag_eval_service = RAGEvalService(settings, _rag_service)
        if _rag_eval_service.eval_llm_available():
            logger.info("RAGEvalService initialized (eval LLM: %s)", settings.eval_llm_model or "default")
        else:
            logger.warning("RAGEvalService: eval LLM not configured (EVAL_LLM_API_KEY required for eval runs)")
        if _rag_eval_service.dataset_llm_available():
            logger.info("RAGEvalService: dataset generation LLM available (%s)", settings.eval_dataset_llm_model or "default")
        else:
            logger.warning("RAGEvalService: dataset generation LLM not configured (EVAL_DATASET_LLM_API_KEY required for auto-generation)")
    except Exception as e:
        logger.warning("RAGEvalService init failed: %s", e)
        _rag_eval_service = None

    # ─── PromptAssembler ───
    try:
        from app.services.pending_dispatch_plans import get_planner_snapshot
        from app.services.prompt_assembler import (
            ConstraintsSource,
            ContextAssembler,
            PlannerSource,
            ProfileSource,
            RecallSource,
            SourceRegistry,
            TaskMemBuffer,
            TaskMemSource,
            ToolStateSource,
            ToolStateTracker,
        )
        from app.tools.registry import tool_registry as _tool_reg

        # Create shared buffers and mount to app.state
        task_mem_buffer = TaskMemBuffer()
        tool_state_tracker = ToolStateTracker()
        app_instance.state.task_mem_buffer = task_mem_buffer
        app_instance.state.tool_state_tracker = tool_state_tracker

        registry = SourceRegistry()
        _source_flags = []
        if _memory_service:
            # ProfileSource reads only from Preference table (single-write mode)
            registry.register(ProfileSource(
                preference_provider=_memory_service.preference,
            ))
            registry.register(RecallSource(_memory_service))
            _source_flags.append("Profile")
            _source_flags.append("Recall")
        else:
            logger.warning(
                "PromptAssembler: memory_service unavailable, "
                "ProfileSource and RecallSource will be skipped"
            )
        # PlannerSource — reads dispatch plan state
        registry.register(PlannerSource(provider=get_planner_snapshot))
        _source_flags.append("Planner")
        # TaskMemSource — reads step observations from shared buffer
        registry.register(TaskMemSource(buffer=task_mem_buffer))
        _source_flags.append("TaskMem")
        # ToolStateSource — reads tool registry + recent call traces
        registry.register(ToolStateSource(
            registry_provider=lambda: _tool_reg._tools,
            tracker=tool_state_tracker,
        ))
        _source_flags.append("ToolState")
        # ConstraintsSource — default constraints for cache-stable system prompt
        _default_constraints = (
            "你是一个可靠的 AI 助手。请遵守以下约束：\n"
            "- 回答要准确、简洁、有条理\n"
            "- 不确定时如实说明，不要编造信息\n"
            "- 使用用户提问时的语言进行回复"
        )
        registry.register(ConstraintsSource(constraints_text=_default_constraints))
        _source_flags.append("Constraints")
        app_instance.state.prompt_assembler = ContextAssembler(registry=registry)
        logger.info(
            "PromptAssembler initialized: %d Sources registered (%s)",
            len(_source_flags), ", ".join(_source_flags),
        )
    except Exception as e:
        logger.warning("PromptAssembler init failed: %s", e)

    # ─── HookRegistry ───
    try:
        from app.services.hook_registry import HookRegistry
        from app.services.hooks import register_all

        hook_registry = HookRegistry()
        register_all(hook_registry)
        app_instance.state.hook_registry = hook_registry
        logger.info("HookRegistry initialized with built-in hooks")
    except Exception as e:
        logger.warning("HookRegistry init failed: %s", e)
        app_instance.state.hook_registry = None

    # ─── AgentLoadTracker ───
    try:
        from app.services.agent_load_tracker import agent_load_tracker
        await agent_load_tracker.init_from_db()
        logger.info("AgentLoadTracker initialized")
    except Exception as e:
        logger.warning("AgentLoadTracker init failed: %s", e)

    # ─── Orphan worktree cleanup ───
    await _cleanup_orphan_worktrees(settings)

    # ─── DocumentService ───
    try:
        from app.services.document_service import DocumentService
        _document_service = DocumentService(db=None, rag=_rag_service)
        logger.info("DocumentService initialized")
    except Exception as e:
        logger.warning("DocumentService init failed: %s", e)
        _document_service = None

    # ─── RagTaskWorker ───
    global _rag_task_worker
    if settings.rag_task_worker_enabled and _document_service:
        try:
            from app.services.rag_task_worker import RagTaskWorker
            _rag_task_worker = RagTaskWorker.get_instance()
            _rag_task_worker.set_document_service(_document_service)
            await _rag_task_worker.start(interval_seconds=settings.rag_task_worker_interval)
        except Exception as e:
            logger.warning("RagTaskWorker init failed: %s", e)
            _rag_task_worker = None
    elif not settings.rag_task_worker_enabled:
        logger.info("RagTaskWorker disabled (rag_task_worker_enabled=False) — degraded sync mode")

    # ─── ObsidianSyncService ───
    try:
        from app.services.obsidian_sync_service import ObsidianSyncService
        if _document_service:
            _obsidian_sync_service = ObsidianSyncService(document_service=_document_service)
            logger.info("ObsidianSyncService initialized")
        else:
            logger.warning("ObsidianSyncService skipped (DocumentService not available)")
            _obsidian_sync_service = None
    except Exception as e:
        logger.warning("ObsidianSyncService init failed: %s", e)
        _obsidian_sync_service = None

    # ─── Startup Status Dashboard ───
    _log_startup_dashboard(settings)

    # ─── Crash recovery scan ───
    try:
        from app.services.recovery_scan import scan_interrupted_messages
        await scan_interrupted_messages()
    except Exception as e:
        logger.warning("Recovery scan failed: %s", e)

    yield

    # Shutdown
    if _rag_task_worker:
        try:
            await _rag_task_worker.stop()
        except Exception:
            pass
    try:
        from app.code_intelligence.service import shutdown_code_intelligence_service
        await shutdown_code_intelligence_service()
    except Exception:
        pass
    # Best-effort close of cached LLM HTTP clients (connection pools).
    try:
        from app.adapters.custom_adapter import close_cached_clients
        await close_cached_clients()
    except Exception:
        pass
    shutdown_observability()
    if _curator_job:
        try:
            await _curator_job.stop()
        except Exception:
            pass
    if _memory_service:
        try:
            await _memory_service.close()
        except Exception:
            pass
    if _infrastructure:
        try:
            from app.infra.factory import close_infrastructure
            await close_infrastructure(_infrastructure)
        except Exception:
            pass
    await close_db()


async def _seed_guide_agent() -> None:
    """Idempotently seed the builtin guide agent (小A) at startup.

    Creates the agent if it doesn't exist; does nothing if already present.
    Model config is resolved at runtime from GUIDE_AGENT_* env vars
    (see agent_runner._build_guide_model_profile).
    """
    try:
        from sqlalchemy import select

        from app.db.engine import get_local_db
        from app.db.models import Agent
        from app.services.guide_prompt import GUIDE_SYSTEM_PROMPT
        from app.utils.clock import now_ms

        async with get_local_db() as db:
            existing = (
                await db.execute(select(Agent).where(Agent.is_guide.is_(True)))
            ).scalar_one_or_none()
            if existing is not None:
                if existing.avatar != "icon-22":
                    existing.avatar = "icon-22"
                    await db.commit()
                return

            guide = Agent(
                id="ag_guide_builtin",
                name="小A",
                avatar="icon-22",
                description="系统管理引导 Agent，帮你管理 Agent / Skill / MCP / 知识库 / 记忆",
                system_prompt=GUIDE_SYSTEM_PROMPT,
                adapter_name="custom",
                tool_names=[
                    "manage_agents",
                    "manage_skills",
                    "manage_mcp",
                    "manage_documents",
                    "manage_memory",
                    "manage_profile",
                    "manage_conversations",
                    "manage_tasks",
                ],
                is_builtin=True,
                is_guide=True,
                created_at=now_ms(),
            )
            db.add(guide)
        logger.info("Guide agent (小A) seeded successfully")
    except Exception as e:
        logger.warning("Guide agent seed failed: %s", e)


async def _migrate_agent_model_profiles() -> None:
    """One-time migration: copy baked-in model config from agents to model_profiles.

    Scans the agents table for rows that still have model_provider set (pre-migration),
    deduplicates by (provider, model_id, api_key, api_base_url), and inserts
    into model_profiles. Marks the earliest-created profile as default.
    """
    try:
        from sqlalchemy import select, text

        from app.db.engine import get_local_db
        from app.db.models import ModelProfile
        from app.utils.clock import now_ms
        from app.utils.ids import new_model_profile_id

        async with get_local_db() as db:
            # Check if agents table still has model_provider column
            # (may have been dropped by a prior migration on PG)
            has_col = True
            try:
                result = await db.execute(
                    text("SELECT model_provider FROM agents WHERE model_provider IS NOT NULL LIMIT 1")
                )
                result.fetchall()
            except Exception:
                has_col = False

            if not has_col:
                logger.info("Agent model migration: column already dropped, skipping")
                return

            # Scan agents with baked-in model config
            rows = (
                await db.execute(
                    text(
                        "SELECT id, model_provider, model_id, "
                        "api_key, api_base_url, supports_vision, created_at "
                        "FROM agents "
                        "WHERE model_provider IS NOT NULL AND model_id IS NOT NULL "
                        "ORDER BY created_at ASC"
                    )
                )
            ).fetchall()

            if not rows:
                logger.info("Agent model migration: no agents with baked-in model config found")
                return

            migrated = 0
            for row in rows:
                _agent_id, provider, model_id, api_key, api_base_url, supports_vision, _created_at = row

                # Check if a matching profile already exists
                existing = (
                    await db.execute(
                        select(ModelProfile).where(
                            ModelProfile.provider == provider,
                            ModelProfile.model_id == model_id,
                        )
                    )
                ).scalar_one_or_none()

                if existing is not None:
                    continue

                # Check if any profile exists (to set is_default)
                all_profiles = (
                    await db.execute(
                        select(ModelProfile)
                    )
                ).scalars().all()
                is_default = len(all_profiles) == 0

                profile = ModelProfile(
                    id=new_model_profile_id(),
                    name=f"{provider}/{model_id}",
                    provider=provider,
                    model_id=model_id,
                    api_key=api_key,
                    api_base_url=api_base_url,
                    is_default=is_default,
                    supports_vision=bool(supports_vision),
                    last_test_status="untested",
                    last_tested_at=None,
                    created_at=now_ms(),
                    updated_at=now_ms(),
                )
                db.add(profile)
                await db.flush()
                migrated += 1

            logger.info(
                "Agent model migration: migrated %d model profile(s) from %d agent(s)",
                migrated, len(rows),
            )
    except Exception as e:
        logger.warning("Agent model migration failed: %s", e)


async def _cleanup_orphan_worktrees(settings) -> None:
    """Remove orphaned worktree dirs and prune stale git worktree metadata."""
    import os

    from app.services.worktree_service import (
        get_worktrees_root,
        git_worktree_prune,
        is_git_repo,
        prune_orphan_worktrees,
    )

    try:
        worktrees_root = get_worktrees_root()
        cleaned = await prune_orphan_worktrees(worktrees_root)
        if cleaned:
            logger.info("Worktree GC: cleaned %d orphan worktree(s)", len(cleaned))

        # Prune stale git worktree metadata in each conversation workspace repo.
        # Supports both legacy flat layout and user-scoped layout.
        ws_root = str(settings.workspace_path)
        if os.path.isdir(ws_root):
            for name in os.listdir(ws_root):
                child = os.path.join(ws_root, name)
                if is_git_repo(child):
                    await git_worktree_prune(child)
                elif name == "users" and os.path.isdir(child):
                    for user_name in os.listdir(child):
                        user_dir = os.path.join(child, user_name)
                        if not os.path.isdir(user_dir):
                            continue
                        user_ws = os.path.join(user_dir, "workspaces")
                        if not os.path.isdir(user_ws):
                            continue
                        for conv_name in os.listdir(user_ws):
                            conv_ws = os.path.join(user_ws, conv_name)
                            if is_git_repo(conv_ws):
                                await git_worktree_prune(conv_ws)
    except Exception as e:
        logger.warning("Orphan worktree cleanup failed: %s", e)


def _log_startup_dashboard(settings) -> None:
    """Log a formatted status dashboard of all initialized services."""
    divider = "=" * 60
    logger.info("\n" + divider)
    logger.info("AChat Backend - Startup Status")
    logger.info(divider)

    # Database
    db_status = "✓ PostgreSQL" if settings.database_url else "✗ Database not configured"
    logger.info("Database:        %s", db_status)

    # Infrastructure services
    infra_status = []
    if _infrastructure:
        if _infrastructure.milvus_client:
            infra_status.append("✓ Milvus")
        else:
            infra_status.append("✗ Milvus (degraded)")
        if _infrastructure.neo4j_driver:
            infra_status.append("✓ Neo4j")
        else:
            infra_status.append("✗ Neo4j (degraded)")
        if _infrastructure.redis_client:
            infra_status.append("✓ Redis")
        else:
            infra_status.append("✗ Redis (removed)")
    else:
        infra_status.append("✗ Infrastructure not initialized")
    
    logger.info("Infrastructure:  %s", ", ".join(infra_status))

    # Memory system
    mem_status = []
    if _memory_service:
        mem_status.append("✓ MemoryService")
        if _memory_service.preference:
            mem_status.append("Preference")
        mem_status.append(f"Indexed({ _memory_service.bm25.count() })")
    else:
        mem_status.append("✗ MemoryService not initialized")
    
    logger.info("Memory System:   %s", " ".join(mem_status))

    # RAG system
    rag_status = "✓ RAGService" if _rag_service else "✗ RAGService not initialized"
    logger.info("RAG System:      %s", rag_status)

    # Observability
    obs_status = "✓ OTel+Phoenix" if settings.trace_enabled else "✗ tracing disabled"
    eval_flags = []
    if settings.eval_rule_enabled:
        eval_flags.append("RuleEval")
    if settings.eval_judge_enabled:
        eval_flags.append("JudgeEval")
    eval_str = ", ".join(eval_flags) if eval_flags else "disabled"
    logger.info("Observability:   %s (eval: %s)", obs_status, eval_str)

    # KG backend
    kg_status = "✓ wired" if _kg_wired else "✗ not wired"
    logger.info("KG Backend:      %s", kg_status)

    # Prompt assembler
    has_assembler = bool(getattr(_app_ref.state, "prompt_assembler", None)) if _app_ref else False
    assembler_status = "✓ PromptAssembler" if has_assembler else "✗ PromptAssembler not initialized"
    logger.info("Prompt Asmblr:   %s", assembler_status)

    rag_worker_status = "✓ RagTaskWorker" if _rag_task_worker else "✗ RagTaskWorker not initialized"
    logger.info("RAG Worker:     %s", rag_worker_status)

    # Document service
    doc_status = "✓ DocumentService" if _document_service else "✗ DocumentService not initialized"
    logger.info("Document Svc:   %s", doc_status)

    obs_status = "✓ ObsidianSync" if _obsidian_sync_service else "✗ ObsidianSync not initialized"
    logger.info("Obsidian Sync:  %s", obs_status)

    # Server config
    logger.info("Server:          http://%s:%s", settings.host, settings.port)
    logger.info("Debug Mode:      %s", "ON" if settings.debug else "OFF")
    logger.info(divider)


def _make_embed_fn(settings):
    """Create embedding function using OpenAI-compatible API."""
    api_key = settings.embedding_api_key
    api_url = settings.embedding_api_url or "https://api.openai.com/v1"
    model = settings.embedding_model or "text-embedding-3-small"
    if not api_key:
        return None
    import httpx
    client = httpx.Client(timeout=30.0)
    def embed(text: str) -> list[float]:
        resp = client.post(
            f"{api_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": text, "model": model},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    return embed


def _make_generate_fn(settings):
    """Create LLM generate function using OpenAI-compatible API.

    Priority: llm_api_key > openai_api_key > deepseek_api_key.
    When llm_api_key is set, uses llm_api_url and llm_model for full configurability
    (e.g. DashScope, Ollama, or any OpenAI-compatible endpoint).
    """
    # Priority 1: dedicated LLM config (supports DashScope and other OpenAI-compatible APIs)
    if settings.llm_api_key:
        api_key = settings.llm_api_key
        api_url = settings.llm_api_url or "https://api.openai.com/v1"
        model = settings.llm_model or "gpt-4o-mini"
    # Priority 2: OpenAI key
    elif settings.openai_api_key:
        api_key = settings.openai_api_key
        api_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
    # Priority 3: DeepSeek key
    elif settings.deepseek_api_key:
        api_key = settings.deepseek_api_key
        api_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"
    else:
        return None
    import httpx
    client = httpx.Client(timeout=60.0)
    def generate(system_prompt: str, user_msg: str) -> str:
        resp = client.post(
            f"{api_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    return generate


def _wire_milvus_to_rag(rag_service, milvus_client, settings):
    """Wire MilvusClient into RAGService's HybridStore via callback functions.

    Collection schema: dense embedding (FLOAT_VECTOR + COSINE + IVF_FLAT)
    + BM25 sparse (content VARCHAR with chinese analyzer + content_sparse
    SPARSE_FLOAT_VECTOR + Function(BM25) + SPARSE_INVERTED_INDEX + DAAT_MAXSCORE).
    """
    collection_name = "rag_embeddings"
    dim = settings.rag_milvus_dim

    def _ensure_collection():
        """Drop + recreate if old schema lacks content_sparse; create if missing."""
        from pymilvus import DataType, Function, FunctionType

        if not milvus_client.has_collection(collection_name):
            schema = milvus_client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field(
                "content", DataType.VARCHAR, max_length=65535,
                enable_analyzer=True, analyzer_params={"type": "chinese"},
            )
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
            schema.add_field("file_id", DataType.VARCHAR, max_length=64)
            schema.add_field("chunk_index", DataType.INT64)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
            schema.add_field("content_sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field("user_id", DataType.VARCHAR, max_length=64, default_value="")

            schema.add_function(Function(
                name="content_bm25",
                input_field_names=["content"],
                output_field_names=["content_sparse"],
                function_type=FunctionType.BM25,
            ))

            milvus_client.create_collection(
                collection_name, schema=schema, metric_type="COSINE",
            )

            index_params = milvus_client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 128},
            )
            index_params.add_index(
                field_name="content_sparse",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
            )
            milvus_client.create_index(collection_name, index_params)
            logger.info("RAG: Milvus collection '%s' created with BM25 sparse schema", collection_name)
        else:
            # Check if old schema has content_sparse; if not, drop + recreate
            try:
                desc = milvus_client.describe_collection(collection_name)
                field_names = {f["name"] for f in desc.get("fields", [])}
                if "content_sparse" not in field_names:
                    logger.warning("RAG: Milvus collection has old schema (no content_sparse), dropping + recreating")
                    milvus_client.drop_collection(collection_name)
                    # Recursively call to recreate
                    _ensure_collection()
            except Exception as e:
                logger.warning("RAG: Milvus collection schema check failed: %s", e)

    # Check Milvus version >= 2.5 for native BM25 Function support
    try:
        version_info = milvus_client.get_server_version()
        version_str = str(version_info)
        major_minor = version_str.split(".")[:2]
        major = int(major_minor[0]) if major_minor[0].isdigit() else 0
        minor = int(major_minor[1]) if len(major_minor) > 1 and major_minor[1].isdigit() else 0
        if major < 2 or (major == 2 and minor < 5):
            logger.warning(
                "RAG: Milvus version %s < 2.5, native BM25 Function not supported. "
                "Collection schema changes aborted, system will use PG fallback.",
                version_str,
            )
            return
    except Exception as e:
        logger.warning("RAG: Milvus version check failed: %s", e)

    _ensure_collection()

    def milvus_search(embedding, k, user_id=None):
        try:
            if not milvus_client.has_collection(collection_name):
                return []
            milvus_client.load_collection(collection_name)
            search_kwargs = {
                "collection_name": collection_name,
                "data": [embedding],
                "limit": k,
                "output_fields": ["content"],
            }
            if user_id:
                search_kwargs["filter"] = f'user_id == "{user_id}"'
            results = milvus_client.search(**search_kwargs)
            return [
                {"pg_id": hit["id"], "content": hit["entity"].get("content", ""), "score": hit["distance"]}
                for hit in results[0]
            ]
        except Exception as e:
            logger.warning("Milvus search error: %s", e)
            return []

    def milvus_bm25_search(query_text, k, drop_ratio=0.0, user_id=None):
        """Milvus native BM25 search."""
        try:
            if not milvus_client.has_collection(collection_name):
                return []
            milvus_client.load_collection(collection_name)
            search_kwargs = {
                "collection_name": collection_name,
                "data": [query_text],
                "anns_field": "content_sparse",
                "limit": k,
                "output_fields": ["content", "chunk_id", "file_id", "chunk_index"],
                "search_params": {"metric_type": "BM25", "params": {"drop_ratio_search": drop_ratio}},
            }
            if user_id:
                search_kwargs["filter"] = f'user_id == "{user_id}"'
            results = milvus_client.search(**search_kwargs)
            return [
                {"pg_id": hit["id"], "content": hit["entity"].get("content", ""), "score": hit["distance"]}
                for hit in results[0]
            ]
        except Exception as e:
            logger.warning("Milvus BM25 search error: %s", e)
            return []

    def milvus_hybrid_search(query_text, query_embedding, k, vector_weight=0.7, bm25_weight=0.3, drop_ratio=0.0, user_id=None):
        """Milvus hybrid_search with WeightedRanker for dense + BM25 fusion."""
        try:
            from pymilvus import AnnSearchRequest, WeightedRanker

            if not milvus_client.has_collection(collection_name):
                return []
            milvus_client.load_collection(collection_name)

            vector_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=k,
            )
            bm25_req = AnnSearchRequest(
                data=[query_text],
                anns_field="content_sparse",
                param={"metric_type": "BM25", "params": {"drop_ratio_search": drop_ratio}},
                limit=k,
            )
            reranker = WeightedRanker(vector_weight, bm25_weight)
            results = milvus_client.hybrid_search(
                collection_name=collection_name,
                reqs=[vector_req, bm25_req],
                ranker=reranker,
                limit=k,
                output_fields=["content", "chunk_id", "file_id", "chunk_index"],
            )
            return [
                {"pg_id": hit["id"], "content": hit["entity"].get("content", ""), "score": hit["distance"]}
                for hit in results[0]
            ]
        except Exception as e:
            logger.warning("Milvus hybrid search error: %s", e)
            return []

    def milvus_insert(ids, contents, embeddings, user_id=None):
        try:
            if not milvus_client.has_collection(collection_name):
                _ensure_collection()
            data = [
                {
                    "id": int(i),
                    "embedding": emb,
                    "content": txt,
                    "user_id": user_id or "",
                    "chunk_id": str(i),
                    "file_id": "",
                    "chunk_index": idx,
                }
                for idx, (i, txt, emb) in enumerate(
                    zip(ids, contents, embeddings, strict=False)
                )
            ]
            milvus_client.insert(collection_name, data)
        except Exception as e:
            logger.warning("Milvus insert error: %s", e)

    def milvus_delete(ids):
        try:
            if milvus_client.has_collection(collection_name):
                milvus_client.delete(
                    collection_name,
                    filter=f"id in {list(int(i) for i in ids)}",
                )
        except Exception as e:
            logger.warning("Milvus delete error: %s", e)

    rag_service.set_milvus_backend(
        milvus_search, milvus_insert,
        bm25_search_fn=milvus_bm25_search,
        hybrid_search_fn=milvus_hybrid_search,
    )
    rag_service.set_milvus_delete_fn(milvus_delete)
    logger.info("RAG: Milvus backend wired (dense + BM25 sparse + WeightedRanker)")


def _wire_kg_to_rag(rag_service, neo4j_driver, settings, generate_fn, *, milvus_client=None, embed_fn=None):
    """Wire KGStore into RAGService's HybridStore for KG search/index/delete.
    Also inject KGStore + Extractor into GraphBuildTask and GraphRetrieval.
    If milvus_client + embed_fn available, inject MilvusGraphVectorStore too.
    """
    from app.graph.extractors.factory import GraphExtractorFactory
    from app.graph.kgstore import KGStore
    from app.rag.graph_build_task import GraphBuildTask
    from app.rag.graph_retrieval import GraphRetrieval

    extractor = GraphExtractorFactory.create("llm", {"llm_fn": generate_fn}) if generate_fn else None
    kg_store = KGStore(settings, neo4j_driver, extractor)

    # Inject into GraphBuildTask and GraphRetrieval (class-level injection)
    GraphBuildTask.set_kg_store(kg_store, extractor)
    GraphRetrieval.set_kg_store(kg_store)

    # Inject embed_fn into GraphBuildTask and GraphRetrieval (for MilvusGraphVectorStore)
    if embed_fn:
        GraphBuildTask.set_embed_fn(embed_fn)
        GraphRetrieval.set_embed_fn(embed_fn)

    # Inject MilvusGraphVectorStore if Milvus client is available
    if milvus_client:
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            MilvusGraphVectorStore.set_client(milvus_client, settings.rag_milvus_dim)
            logger.info("RAG: MilvusGraphVectorStore wired (entity + triple collections)")
        except Exception as e:
            logger.warning("RAG: MilvusGraphVectorStore wiring failed: %s", e)

    async def kg_search(query_text, k):
        return await kg_store.search(query_text, k)

    async def kg_index(doc_hash, chunks):
        await kg_store.index_document(doc_hash, chunks)

    async def kg_delete(doc_hash):
        await kg_store.delete_document(doc_hash)

    rag_service.set_kg_backend(kg_search)
    rag_service.set_kg_index_fn(kg_index)
    rag_service.set_kg_delete_fn(kg_delete)
    logger.info("RAG: KG backend wired (GraphBuildTask + GraphRetrieval injected)")

    # 旧数据迁移：检测无 UserKG 标签的旧 Entity 节点，清空 Neo4j + Milvus graph collections
    asyncio.create_task(_migrate_old_graph_data(neo4j_driver))


async def _migrate_old_graph_data(neo4j_driver) -> None:
    """检测旧格式图谱数据（无 UserKG 标签的 Entity 节点），如存在则清空。

    旧模型不兼容新图模型（Chunk 节点 + MENTIONS 边 + 确定性 ID + 用户隔离），
    需删除重建。用户需要重新上传文档触发图谱构建。
    """
    try:
        async with neo4j_driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity) WHERE NOT any(l IN labels(e) WHERE l STARTS WITH 'UserKG') "
                "RETURN count(e) AS count"
            )
            data = await result.data()
            count = data[0]["count"] if data else 0
            if count > 0:
                logger.warning(
                    "Graph isolation migration: detected %d old-format Entity nodes, "
                    "clearing Neo4j graph data (users need to re-upload documents to rebuild graph)",
                    count,
                )
                await session.run("MATCH (n) DETACH DELETE n")

        # 清空 Milvus graph collections
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            if MilvusGraphVectorStore.available():
                MilvusGraphVectorStore._ensure_collections()
                client = MilvusGraphVectorStore._client
                if client:
                    if client.has_collection("rag_graph_entities"):
                        client.drop_collection("rag_graph_entities")
                    if client.has_collection("rag_graph_triples"):
                        client.drop_collection("rag_graph_triples")
                    MilvusGraphVectorStore._initialized = False
                logger.info("Graph isolation migration: Milvus graph collections dropped")
        except Exception as e:
            logger.warning("Graph isolation migration: Milvus cleanup failed: %s", e)

    except Exception as e:
        logger.warning("Graph isolation migration failed: %s", e)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="AChat Backend",
        description="Multi-Agent Collaboration Workspace API",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # CORS middleware - also allow localhost variations for dev environments
    _cors_origins = settings.cors_origins_list + ["http://127.0.0.1:3000", "http://[::1]:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # CSRF: reject mutation requests from unallowed origins
    _allowed_origins = set(settings.cors_origins_list)
    # Also accept localhost variations (127.0.0.1, ::1) for dev environments
    _localhost_variants = {"http://127.0.0.1:3000", "http://[::1]:3000"}

    @app.middleware("http")
    async def csrf_origin_check(request: Request, call_next):
        """Reject POST/PATCH/DELETE requests whose Origin header doesn't match allowed origins."""
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            raw = request.headers.get("origin") or request.headers.get("referer")
            if raw:
                # Referer includes a path (e.g. http://localhost:3000/); normalise
                # to scheme://host:port so it matches the allowed-origin entries.
                parsed = urlparse(raw)
                origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else raw
                if origin not in _allowed_origins and origin not in _localhost_variants:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Origin not allowed"},
                    )
        return await call_next(request)

    # Include routers
    from app.api import (
        agents,
        artifacts,
        attachments,
        auth,
        code_intelligence,
        conversations,
        deployments,
        documents,
        eval,
        fs,
        graph,
        mcp,
        memory,
        messages,
        model_profiles,
        obsidian,
        pending,
        plan_usage,
        profile,
        rag_config,
        rag_eval,
        rag_tasks,
        runs_misc,
        skills,
        stream,
        tasks,
        workspaces,
    )
    from app.api import (
        settings as settings_router,
    )
    from app.api.mobile import routes as mobile_routes

    app.include_router(auth.router, prefix="/api", tags=["auth"])
    app.include_router(profile.router, prefix="/api", tags=["profile"])
    app.include_router(conversations.router, prefix="/api", tags=["conversations"])
    app.include_router(code_intelligence.router, prefix="/api", tags=["code-intelligence"])
    app.include_router(messages.router, prefix="/api", tags=["messages"])
    app.include_router(agents.router, prefix="/api", tags=["agents"])
    app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
    app.include_router(attachments.router, prefix="/api", tags=["attachments"])
    app.include_router(fs.router, prefix="/api", tags=["fs"])
    # pending router decorators already carry the /api prefix, so no prefix here
    app.include_router(pending.router, tags=["pending"])
    app.include_router(settings_router.router, prefix="/api", tags=["settings"])
    app.include_router(runs_misc.router, prefix="/api", tags=["runs-misc"])
    app.include_router(plan_usage.router, prefix="/api", tags=["plan-usage"])
    app.include_router(mobile_routes.router, prefix="/api", tags=["mobile"])
    app.include_router(stream.router, prefix="/api", tags=["stream"])
    app.include_router(graph.router, prefix="/api/graph", tags=["graph"])
    app.include_router(documents.router, prefix="/api", tags=["documents"])
    app.include_router(obsidian.router, prefix="/api", tags=["obsidian"])
    app.include_router(eval.router, prefix="/api", tags=["eval"])
    app.include_router(model_profiles.router, prefix="/api", tags=["model-profiles"])
    app.include_router(memory.router, prefix="", tags=["memory"])
    app.include_router(skills.router, prefix="/api", tags=["skills"])
    app.include_router(mcp.router, prefix="/api", tags=["mcp"])
    app.include_router(workspaces.router, prefix="/api", tags=["workspaces"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(rag_eval.router, prefix="/api", tags=["rag-eval"])
    app.include_router(rag_tasks.router, prefix="/api", tags=["rag-tasks"])
    app.include_router(rag_config.router, prefix="/api", tags=["rag-config"])
    # deployment preview assets served at root /deployments/{id}/... (no /api prefix);
    # the previewPath the agent emits is /deployments/{id}. Frontend proxies via rewrite.
    app.include_router(deployments.router, tags=["deployments"])

    # ─── Aeval evaluation harness (change: add-eval-harness-core) ───
    # Mounted AFTER the judge routes above: Starlette matches routes in
    # registration order, so /api/eval/judge/* keeps matching first and the
    # sub-app's routes (suites/tasks/runs/trials/compare/graders) coexist on
    # the same prefix without overlap (design doc §10.1).
    if settings.eval_harness_enabled:
        try:
            # agent_eval is consumed as an installed (editable) package —
            # no sys.path routing needed. eval_integration is the AChat
            # adapter layer inside this app package.
            from agent_eval.api.app import create_app as create_eval_app

            # Real runner injected later in lifespan (needs async DB init +
            # OTel provider for the trace bridge). Without one, the
            # storage-backed endpoints work and POST /runs returns 503.
            app.mount("/api/eval", create_eval_app())
            logger.info("Eval harness API mounted at /api/eval")
        except Exception as e:
            logger.warning("Eval harness mount failed: %s", e)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # Limit hot-reload watching to the application source directory so that
        # `.venv` / `.agenthub-data` / `node_modules` / `__pycache__` changes
        # (e.g. from an agent's `pip install`) don't trigger a reload that
        # crashes AChat with ModuleNotFoundError. See specs/workspace-env-isolation.
        reload_dirs=["app"],
        reload_excludes=[
            "**/.venv/**",
            "**/.agenthub-data/**",
            "**/node_modules/**",
            "**/__pycache__/**",
        ],
    )
