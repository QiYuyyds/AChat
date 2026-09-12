"""Infrastructure factory — configuration-driven assembly with independent degradation.

Each service is wrapped in try/except; failure → no-op without affecting others.
Degradation chain: Milvus → TF cosine; ES → no full-text; Neo4j → no-op; Kafka → in-process.

rag-infra-config: connection params resolve two-level — global_settings (DB) →
env → 未配置. Resolution only changes where connection params come from; the
independent degradation behaviour per service is unchanged.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.config import Settings
from app.infra.status import InfrastructureStatus, ServiceSnapshot

logger = logging.getLogger(__name__)

# Milvus standalone needs 1-2 min after container start; retry with generous delays
_MILVUS_RETRY_WAITS = [10, 15, 30, 30]


@dataclass
class ResolvedInfraConfig:
    """两级解析结果（design D2）：global_settings 落库值 → env 默认 → 未配置。

    ``*_source`` records where each service's connection params came from
    (db / env / none) — surfaced via GET /api/infra/status to explain
    "改了不生效" 类问题.
    """

    milvus_host: str = ""
    milvus_port: int = 19530
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    enable_graph: bool = False

    milvus_source: str = "none"
    neo4j_source: str = "none"
    graph_source: str = "none"


async def resolve_infra_config(settings: Settings) -> ResolvedInfraConfig:
    """Resolve infra connection params: global_settings → env → 未配置.

    Reads through global_settings_service (TTL-cached singleton row). DB
    unavailable → env-only fallback (infrastructure assembly must not crash
    because of a settings read).
    """
    cfg = ResolvedInfraConfig()
    gs = None
    try:
        from app.services.global_settings_service import get_global_settings

        gs = await get_global_settings()
    except Exception as e:
        logger.warning("global_settings unavailable for infra resolution, env-only: %s", e)

    # Explicitly-set env fields (os env or .env file); defaults are treated as 未配置.
    env_set = settings.model_fields_set

    gs_host = getattr(gs, "milvus_host", None)
    if gs_host:
        cfg.milvus_host, cfg.milvus_source = gs_host, "db"
    elif settings.milvus_host:
        cfg.milvus_host, cfg.milvus_source = settings.milvus_host, "env"
    gs_port = getattr(gs, "milvus_port", None)
    cfg.milvus_port = gs_port if gs_port is not None else settings.milvus_port

    gs_uri = getattr(gs, "neo4j_uri", None)
    if gs_uri:
        cfg.neo4j_uri, cfg.neo4j_source = gs_uri, "db"
    elif settings.neo4j_uri:
        cfg.neo4j_uri, cfg.neo4j_source = settings.neo4j_uri, "env"
    gs_user = getattr(gs, "neo4j_user", None)
    cfg.neo4j_user = gs_user if gs_user is not None else settings.neo4j_user
    gs_pwd = getattr(gs, "neo4j_password", None)
    cfg.neo4j_password = gs_pwd if gs_pwd is not None else settings.neo4j_password

    gs_graph = getattr(gs, "enable_graph", None)
    if gs_graph is not None:
        cfg.enable_graph, cfg.graph_source = bool(gs_graph), "db"
    elif "enable_graph" in env_set:
        cfg.enable_graph, cfg.graph_source = settings.enable_graph, "env"
    else:
        cfg.enable_graph, cfg.graph_source = settings.enable_graph, "none"

    return cfg


def build_milvus_client(uri: str, timeout: float = 15):
    """Construct a MilvusClient — shared by factory assembly and the test endpoint."""
    from pymilvus import MilvusClient

    return MilvusClient(uri=uri, timeout=timeout)


def build_neo4j_driver(uri: str, user: str, password: str):
    """Construct an async Neo4j driver — shared by factory assembly and the test endpoint."""
    from neo4j import AsyncGraphDatabase

    return AsyncGraphDatabase.driver(
        uri,
        auth=(user, password),
        connection_timeout=10,
    )


class Infrastructure:
    """Holds all infrastructure connections with degradation status."""

    def __init__(self):
        self.status = InfrastructureStatus()
        self.neo4j_driver = None
        self.milvus_client = None
        self.kafka_producer = None
        self.redis_client = None  # Removed in dual-DB migration; kept for backward-compat


_infra_instance: Infrastructure | None = None


def get_infrastructure() -> Infrastructure | None:
    """Return the singleton Infrastructure built at startup (or None)."""
    return _infra_instance


async def build_infrastructure(settings: Settings) -> Infrastructure:
    """Build infrastructure with configuration-driven assembly.

    Each service is independently wrapped in try/except so that one failure
    does not affect others. The status dashboard shows what connected.
    """
    infra = Infrastructure()
    cfg = await resolve_infra_config(settings)

    # ─── PostgreSQL (always expected) ───
    try:
        # PG is managed by SQLAlchemy engine; just verify the URL is set
        if settings.database_url and "postgresql" in settings.database_url:
            infra.status.postgres = "connected"
        else:
            infra.status.postgres = "disconnected"
            logger.warning("PostgreSQL not configured or using non-PG driver")
    except Exception as e:
        logger.warning("PostgreSQL status check failed: %s", e)

    # ─── Milvus (optional, with retry) ───
    if cfg.milvus_host:
        uri = f"http://{cfg.milvus_host}:{cfg.milvus_port}"
        client = None
        milvus_error: str | None = None
        for attempt, wait in enumerate(_MILVUS_RETRY_WAITS):
            try:
                client = build_milvus_client(uri)
                # Verify connectivity with a lightweight call
                client.list_collections()
                break
            except Exception as e:
                client = None
                milvus_error = f"{type(e).__name__}: {e}"
                if attempt < len(_MILVUS_RETRY_WAITS) - 1:
                    logger.info("Milvus attempt %d/%d failed, retrying in %ds...", attempt + 1, len(_MILVUS_RETRY_WAITS), wait)
                    await asyncio.sleep(wait)
                else:
                    logger.warning("Milvus unavailable after %d attempts: %s", len(_MILVUS_RETRY_WAITS), e)
        if client is not None:
            infra.milvus_client = client
            infra.status.milvus = "connected"
            infra.status.milvus_snap = ServiceSnapshot("connected", cfg.milvus_source)
            logger.info("Milvus connected: %s", uri)
        else:
            infra.status.milvus_snap = ServiceSnapshot("degraded", cfg.milvus_source, milvus_error)
    else:
        infra.status.milvus_snap = ServiceSnapshot("disabled", "none", "未配置")
        logger.info("Milvus not configured (milvus_host is empty)")

    # ─── Neo4j (optional) ───
    if cfg.neo4j_uri and cfg.enable_graph:
        driver = None
        try:
            driver = build_neo4j_driver(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
            # Driver construction is lazy — verify so the status is honest
            # (unreachable URI must show degraded, not connected).
            await driver.verify_connectivity()
            infra.neo4j_driver = driver
            infra.status.neo4j = "connected"
            infra.status.neo4j_snap = ServiceSnapshot("connected", cfg.neo4j_source)
            logger.info("Neo4j connected: %s", cfg.neo4j_uri)
        except Exception as e:
            if driver is not None:
                try:
                    await driver.close()
                except Exception:
                    pass
            detail = f"{type(e).__name__}: {e}"
            infra.status.neo4j = "disconnected"
            infra.status.neo4j_snap = ServiceSnapshot("degraded", cfg.neo4j_source, detail)
            logger.warning("Neo4j unavailable: %s", e)
    elif not cfg.enable_graph:
        reason = "enable_graph=False"
        if cfg.neo4j_source == "none":
            reason = "未配置"
        infra.status.neo4j_snap = ServiceSnapshot("disabled", cfg.neo4j_source, reason)
        logger.info("Neo4j disabled (enable_graph=False)")
    else:
        infra.status.neo4j_snap = ServiceSnapshot("disabled", "none", "未配置")
        logger.info("Neo4j not configured (neo4j_uri is empty)")

    # ─── Kafka (optional) ───
    if settings.kafka_brokers:
        try:
            from kafka import KafkaProducer
            brokers = [b.strip() for b in settings.kafka_brokers.split(",") if b.strip()]
            infra.kafka_producer = KafkaProducer(
                bootstrap_servers=brokers,
                request_timeout_ms=5000,
            )
            infra.status.kafka = "connected"
            infra.status.kafka_snap = ServiceSnapshot("connected", "env")
            logger.info("Kafka connected: %s", brokers)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            infra.status.kafka = "disconnected"
            infra.status.kafka_snap = ServiceSnapshot("degraded", "env", detail)
            logger.warning("Kafka unavailable: %s", e)
    else:
        infra.status.kafka_snap = ServiceSnapshot("disabled", "none", "未配置")
        logger.info("Kafka not configured (kafka_brokers is empty)")

    # ─── Redis (removed in dual-DB migration) ───
    # Redis KV cache and Stream write-behind have been removed.
    # The redis_client field is kept as None for backward-compat.
    infra.redis_client = None
    infra.status.redis = "removed"
    logger.info("Redis: removed (replaced by local SQLite + process-internal cache)")

    # ─── Embedding / Phoenix (config-derived, no persistent connection) ───
    if settings.embedding_api_key:
        infra.status.embedding_snap = ServiceSnapshot("connected", "env")
    else:
        infra.status.embedding_snap = ServiceSnapshot("disabled", "none", "EMBEDDING_API_KEY 未配置")
    if settings.trace_enabled and settings.phoenix_endpoint:
        infra.status.phoenix_snap = ServiceSnapshot("connected", "env")
    else:
        infra.status.phoenix_snap = ServiceSnapshot("disabled", "none", "tracing disabled")

    # Log dashboard
    for line in infra.status.dashboard_lines():
        logger.info(line)

    global _infra_instance
    _infra_instance = infra
    return infra


async def close_infrastructure(infra: Infrastructure) -> None:
    """Gracefully close all infrastructure connections."""
    if infra.neo4j_driver:
        try:
            await infra.neo4j_driver.close()
        except Exception:
            pass

    if infra.kafka_producer:
        try:
            infra.kafka_producer.close(timeout=5)
        except Exception:
            pass

    if infra.redis_client:
        try:
            await infra.redis_client.aclose()
        except Exception:
            pass

    logger.info("Infrastructure closed")
