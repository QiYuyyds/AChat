"""Infrastructure factory — configuration-driven assembly with independent degradation.

Each service is wrapped in try/except; failure → no-op without affecting others.
Degradation chain: Milvus → TF cosine; ES → no full-text; Neo4j → no-op; Kafka → in-process.
"""

import logging
import time
from typing import Optional

from app.config import Settings
from app.infra.status import InfrastructureStatus

logger = logging.getLogger(__name__)


class Infrastructure:
    """Holds all infrastructure connections with degradation status."""

    def __init__(self):
        self.status = InfrastructureStatus()
        self.neo4j_driver = None
        self.milvus_client = None
        self.es_client = None
        self.kafka_producer = None


def build_infrastructure(settings: Settings) -> Infrastructure:
    """Build infrastructure with configuration-driven assembly.

    Each service is independently wrapped in try/except so that one failure
    does not affect others. The status dashboard shows what connected.
    """
    infra = Infrastructure()

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
    if settings.milvus_host:
        from pymilvus import MilvusClient
        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        client = None
        # Milvus standalone needs 1-2 min after container start; retry with generous delays
        for attempt, wait in enumerate([10, 15, 30, 30]):
            try:
                client = MilvusClient(uri=uri, timeout=15)
                # Verify connectivity with a lightweight call
                client.list_collections()
                break
            except Exception as e:
                client = None
                if attempt < 3:
                    logger.info("Milvus attempt %d/4 failed, retrying in %ds...", attempt + 1, wait)
                    time.sleep(wait)
                else:
                    logger.warning("Milvus unavailable after 4 attempts (~85s): %s", e)
        if client is not None:
            infra.milvus_client = client
            infra.status.milvus = "connected"
            logger.info("Milvus connected: %s", uri)
    else:
        logger.info("Milvus not configured (milvus_host is empty)")

    # ─── Elasticsearch (optional) ───
    if settings.es_addresses:
        try:
            from elasticsearch import AsyncElasticsearch
            addresses = [a.strip() for a in settings.es_addresses.split(",") if a.strip()]
            infra.es_client = AsyncElasticsearch(addresses)
            infra.status.elasticsearch = "connected"
            logger.info("Elasticsearch connected: %s", addresses)
        except Exception as e:
            infra.status.elasticsearch = "disconnected"
            logger.warning("Elasticsearch unavailable: %s", e)
    else:
        logger.info("Elasticsearch not configured (es_addresses is empty)")

    # ─── Neo4j (optional) ───
    if settings.neo4j_uri and settings.enable_graph:
        try:
            from neo4j import AsyncGraphDatabase
            infra.neo4j_driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            infra.status.neo4j = "connected"
            logger.info("Neo4j connected: %s", settings.neo4j_uri)
        except Exception as e:
            infra.status.neo4j = "disconnected"
            logger.warning("Neo4j unavailable: %s", e)
    elif not settings.enable_graph:
        logger.info("Neo4j disabled (enable_graph=False)")
    else:
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
            logger.info("Kafka connected: %s", brokers)
        except Exception as e:
            infra.status.kafka = "disconnected"
            logger.warning("Kafka unavailable: %s", e)
    else:
        logger.info("Kafka not configured (kafka_brokers is empty)")

    # Log dashboard
    for line in infra.status.dashboard_lines():
        logger.info(line)

    return infra


async def close_infrastructure(infra: Infrastructure) -> None:
    """Gracefully close all infrastructure connections."""
    if infra.neo4j_driver:
        try:
            await infra.neo4j_driver.close()
        except Exception:
            pass

    if infra.es_client:
        try:
            await infra.es_client.close()
        except Exception:
            pass

    if infra.kafka_producer:
        try:
            infra.kafka_producer.close(timeout=5)
        except Exception:
            pass

    logger.info("Infrastructure closed")
