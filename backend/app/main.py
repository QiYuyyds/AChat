"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import apply_env_overrides, get_settings
from app.db.engine import close_db, init_db

# ── Logging configuration (AGI-memory style) ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
# Suppress noisy third-party library logs
for _noisy in ("pymilvus", "elastic_transport", "kafka", "sqlalchemy", "neo4j.notifications"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Module-level service references (accessed by tool handlers)
_memory_service = None
_rag_service = None
_infrastructure = None
_app_ref = None
_document_service = None
_kg_wired = False


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    global _memory_service, _rag_service, _infrastructure, _app_ref, _document_service, _kg_wired
    _app_ref = app_instance

    # Startup
    apply_env_overrides()
    import app.services.agent_runner  # noqa: F401

    await init_db()

    settings = get_settings()

    # ─── Infrastructure factory ───
    try:
        from app.infra.factory import build_infrastructure, close_infrastructure
        _infrastructure = build_infrastructure(settings)
    except Exception as e:
        logger.warning("Infrastructure build failed: %s", e)

    # ─── MemoryService ───
    try:
        from app.memory.memory_service import MemoryService
        _memory_service = MemoryService(settings)
        if _infrastructure and _infrastructure.neo4j_driver:
            _memory_service.set_neo4j_driver(_infrastructure.neo4j_driver)
        await _memory_service.initialize()
    except Exception as e:
        logger.warning("MemoryService init failed: %s", e)
        _memory_service = None

    # ─── RAGService ───
    try:
        from app.services.rag_service import RAGService
        _rag_service = RAGService(settings)
        # Wire infrastructure backends into RAG
        if _infrastructure and _infrastructure.milvus_client:
            _wire_milvus_to_rag(_rag_service, _infrastructure.milvus_client, settings)
        if _infrastructure and _infrastructure.es_client:
            _wire_es_to_rag(_rag_service, _infrastructure.es_client)
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

        # Inject embed_fn and generate_fn into MemoryService for LTM semantic recall
        if embed_fn and _memory_service:
            _memory_service.set_embed_fn(embed_fn)
            logger.info("Memory: embed_fn injected")
        if generate_fn and _memory_service:
            _memory_service.set_generate_fn(generate_fn)
            logger.info("Memory: generate_fn injected")

        # Wire KG backend if Neo4j driver and LLM are both available
        if _infrastructure and _infrastructure.neo4j_driver and generate_fn:
            _wire_kg_to_rag(_rag_service, _infrastructure.neo4j_driver, settings, generate_fn)
            _kg_wired = True

        await _rag_service.initialize()
    except Exception as e:
        logger.warning("RAGService init failed: %s", e)
        _rag_service = None

    # ─── PromptAssembler ───
    try:
        from app.services.prompt_assembler import (
            ContextAssembler, SourceRegistry,
            PlannerSource, ProfileSource, RecallSource,
            TaskMemBuffer, TaskMemSource, ToolStateSource, ToolStateTracker,
            ConstraintsSource,
        )
        from app.services.pending_dispatch_plans import get_planner_snapshot
        from app.tools.registry import tool_registry as _tool_reg

        # Create shared buffers and mount to app.state
        task_mem_buffer = TaskMemBuffer()
        tool_state_tracker = ToolStateTracker()
        app_instance.state.task_mem_buffer = task_mem_buffer
        app_instance.state.tool_state_tracker = tool_state_tracker

        registry = SourceRegistry()
        if _memory_service:
            # ProfileSource now reads from both Preference AND LTM
            registry.register(ProfileSource(
                preference_provider=_memory_service.preference,
                ltm=_memory_service.ltm,
            ))
            registry.register(RecallSource(_memory_service))
        # PlannerSource — reads dispatch plan state
        registry.register(PlannerSource(provider=get_planner_snapshot))
        # TaskMemSource — reads step observations from shared buffer
        registry.register(TaskMemSource(buffer=task_mem_buffer))
        # ToolStateSource — reads tool registry + recent call traces
        registry.register(ToolStateSource(
            registry_provider=lambda: _tool_reg._tools,
            tracker=tool_state_tracker,
        ))
        registry.register(ConstraintsSource())
        app_instance.state.prompt_assembler = ContextAssembler(registry=registry)
        logger.info(
            "PromptAssembler initialized: 6 Sources registered "
            "(Profile+LTM, Recall, Planner, TaskMem, ToolState, Constraints)"
        )
    except Exception as e:
        logger.warning("PromptAssembler init failed: %s", e)

    # ─── DocumentService ───
    try:
        from app.services.document_service import DocumentService
        _document_service = DocumentService(db=None, rag=_rag_service)
        logger.info("DocumentService initialized")
    except Exception as e:
        logger.warning("DocumentService init failed: %s", e)
        _document_service = None

    # ─── Startup Status Dashboard ───
    _log_startup_dashboard(settings)

    yield

    # Shutdown
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
        if _infrastructure.es_client:
            infra_status.append("✓ Elasticsearch")
        else:
            infra_status.append("✗ Elasticsearch (degraded)")
        if _infrastructure.neo4j_driver:
            infra_status.append("✓ Neo4j")
        else:
            infra_status.append("✗ Neo4j (degraded)")
    else:
        infra_status.append("✗ Infrastructure not initialized")
    
    logger.info("Infrastructure:  %s", ", ".join(infra_status))

    # Memory system
    mem_status = []
    if _memory_service:
        mem_status.append("✓ MemoryService")
        if _memory_service.stm:
            mem_status.append("STM")
        if _memory_service.ltm:
            mem_status.append("LTM")
        if _memory_service.preference:
            mem_status.append("Preference")
        if _memory_service.graph_memory:
            mem_status.append("Graph")
    else:
        mem_status.append("✗ MemoryService not initialized")
    
    logger.info("Memory System:   %s", " ".join(mem_status))

    # RAG system
    rag_status = "✓ RAGService" if _rag_service else "✗ RAGService not initialized"
    logger.info("RAG System:      %s", rag_status)

    # KG backend
    kg_status = "✓ wired" if _kg_wired else "✗ not wired"
    logger.info("KG Backend:      %s", kg_status)

    # Prompt assembler
    has_assembler = bool(getattr(_app_ref.state, "prompt_assembler", None)) if _app_ref else False
    assembler_status = "✓ PromptAssembler" if has_assembler else "✗ PromptAssembler not initialized"
    logger.info("Prompt Asmblr:   %s", assembler_status)

    # Document service
    doc_status = "✓ DocumentService" if _document_service else "✗ DocumentService not initialized"
    logger.info("Document Svc:   %s", doc_status)

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
    """Wire MilvusClient into RAGService's HybridStore via callback functions."""
    collection_name = "rag_embeddings"
    dim = settings.rag_milvus_dim

    def milvus_search(embedding, k):
        try:
            if not milvus_client.has_collection(collection_name):
                return []
            milvus_client.load_collection(collection_name)
            results = milvus_client.search(
                collection_name, data=[embedding], limit=k,
                output_fields=["content"],
            )
            return [
                {"pg_id": hit["id"], "content": hit["entity"].get("content", ""), "score": hit["distance"]}
                for hit in results[0]
            ]
        except Exception as e:
            logger.warning("Milvus search error: %s", e)
            return []

    def milvus_insert(ids, contents, embeddings):
        try:
            if not milvus_client.has_collection(collection_name):
                from pymilvus import DataType
                schema = milvus_client.create_schema(auto_id=False, enable_dynamic_field=False)
                schema.add_field("id", DataType.INT64, is_primary=True)
                schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
                schema.add_field("content", DataType.VARCHAR, max_length=65535)
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
                milvus_client.create_index(collection_name, index_params)
            data = [
                {"id": int(i), "embedding": emb, "content": txt}
                for i, txt, emb in zip(ids, contents, embeddings)
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

    rag_service.set_milvus_backend(milvus_search, milvus_insert)
    rag_service.set_milvus_delete_fn(milvus_delete)
    logger.info("RAG: Milvus backend wired")


def _wire_es_to_rag(rag_service, es_client):
    """Wire AsyncElasticsearch into RAGService's HybridStore via callback functions."""
    index_name = "rag_chunks"

    async def es_search(query_text, k):
        try:
            resp = await es_client.search(
                index=index_name,
                body={"query": {"match": {"content": query_text}}, "size": k},
            )
            return [
                {"pg_id": int(hit["_id"]), "content": hit["_source"].get("content", ""), "score": hit["_score"]}
                for hit in resp["hits"]["hits"]
            ]
        except Exception as e:
            logger.warning("ES search error: %s", e)
            return []

    async def es_index(pg_id, content, doc_hash, chunk_idx):
        try:
            await es_client.index(
                index=index_name,
                id=str(pg_id),
                body={
                    "content": content,
                    "doc_hash": doc_hash,
                    "chunk_idx": chunk_idx,
                },
            )
        except Exception as e:
            logger.warning("ES index error: %s", e)

    async def es_delete(ids):
        try:
            for pg_id in ids:
                await es_client.delete(index=index_name, id=str(pg_id), ignore=(404,))
        except Exception as e:
            logger.warning("ES delete error: %s", e)

    rag_service.set_es_backend(es_search, es_index)
    rag_service.set_es_delete_fn(es_delete)
    logger.info("RAG: Elasticsearch backend wired")


def _wire_kg_to_rag(rag_service, neo4j_driver, settings, generate_fn):
    """Wire KGStore into RAGService's HybridStore for KG search/index/delete."""
    from app.graph.kgstore import KGStore
    from app.graph.extractor import Extractor

    extractor = Extractor(generate_fn)
    kg_store = KGStore(settings, neo4j_driver, extractor)

    async def kg_search(query_text, k):
        return await kg_store.search(query_text, k)

    async def kg_index(doc_hash, chunks):
        await kg_store.index_document(doc_hash, chunks)

    async def kg_delete(doc_hash):
        await kg_store.delete_document(doc_hash)

    rag_service.set_kg_backend(kg_search)
    rag_service.set_kg_index_fn(kg_index)
    rag_service.set_kg_delete_fn(kg_delete)
    logger.info("RAG: KG backend wired")


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

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    from app.api import (
        agents,
        artifacts,
        attachments,
        conversations,
        deployments,
        documents,
        fs,
        internal,
        messages,
        pending,
        runs_misc,
        skills,
        stream,
    )
    from app.api import (
        settings as settings_router,
    )
    from app.api.mobile import routes as mobile_routes

    app.include_router(conversations.router, prefix="/api", tags=["conversations"])
    app.include_router(messages.router, prefix="/api", tags=["messages"])
    app.include_router(agents.router, prefix="/api", tags=["agents"])
    app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
    app.include_router(attachments.router, prefix="/api", tags=["attachments"])
    app.include_router(fs.router, prefix="/api", tags=["fs"])
    # pending router decorators already carry the /api prefix, so no prefix here
    app.include_router(pending.router, tags=["pending"])
    app.include_router(settings_router.router, prefix="/api", tags=["settings"])
    app.include_router(runs_misc.router, prefix="/api", tags=["runs-misc"])
    app.include_router(mobile_routes.router, prefix="/api", tags=["mobile"])
    app.include_router(stream.router, prefix="/api", tags=["stream"])
    app.include_router(internal.router, prefix="/api", tags=["internal"])
    app.include_router(documents.router, prefix="/api", tags=["documents"])
    app.include_router(skills.router, prefix="/api", tags=["skills"])
    # deployment preview assets served at root /deployments/{id}/... (no /api prefix);
    # the previewPath the agent emits is /deployments/{id}. Frontend proxies via rewrite.
    app.include_router(deployments.router, tags=["deployments"])

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
    )
