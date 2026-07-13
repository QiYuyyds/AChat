"""Backfill rag_chunks.user_id in PG + re-index Milvus and ES entries.

The migrate_to_multi_user.py script originally missed rag_chunks. This script:
1. Backfills rag_chunks.user_id in PG with the default user's ID
2. Re-indexes all rag_chunks into Milvus with the correct user_id
3. Re-indexes all rag_chunks into ES with the correct user_id

Usage::

    cd backend
    python -m scripts.backfill_rag_chunks_user_id
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, text

from app.config import get_settings
from app.db.engine import get_db, init_db
from app.db.models import RagChunk, User

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 500


async def backfill_pg(default_user_id: str) -> int:
    """Backfill rag_chunks.user_id in PostgreSQL."""
    async with get_db() as db:
        result = await db.execute(
            text(
                "UPDATE rag_chunks SET user_id = :uid "
                "WHERE user_id IS NULL"
            ),
            {"uid": default_user_id},
        )
        count = result.rowcount or 0
        if count:
            logger.info(f"[pg] Back-filled {count} row(s) in rag_chunks")
        else:
            logger.info("[pg] rag_chunks already back-filled (0 rows)")
        return count


def _check_milvus_collection_has_user_id(client, collection_name: str) -> bool:
    """Check if the Milvus collection has a user_id field."""
    try:
        desc = client.describe_collection(collection_name)
        fields = desc.get("fields", [])
        return any(f.get("name") == "user_id" for f in fields)
    except Exception:
        return False


def _recreate_milvus_collection(client, collection_name: str, dim: int) -> None:
    """Drop and recreate the Milvus collection with user_id field."""
    from pymilvus import DataType

    if client.has_collection(collection_name):
        client.drop_collection(collection_name)
        logger.info(f"[milvus] Dropped old collection '{collection_name}'")

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("content", DataType.VARCHAR, max_length=65535)
    schema.add_field("user_id", DataType.VARCHAR, max_length=64, default_value="")
    client.create_collection(
        collection_name, schema=schema, metric_type="COSINE",
    )
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="IVF_FLAT",
        metric_type="COSINE",
        params={"nlist": 128},
    )
    client.create_index(collection_name, index_params)
    logger.info(f"[milvus] Created new collection '{collection_name}' with user_id field")


async def reindex_milvus(default_user_id: str) -> None:
    """Re-index all rag_chunks into Milvus with correct user_id."""
    settings = get_settings()
    if not settings.milvus_host:
        logger.info("[milvus] Skipped (MILVUS_HOST not configured)")
        return

    try:
        from pymilvus import MilvusClient
    except ImportError:
        logger.info("[milvus] Skipped (pymilvus not installed)")
        return

    collection_name = "rag_embeddings"
    dim = settings.rag_milvus_dim
    client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")

    if not client.has_collection(collection_name):
        logger.info("[milvus] Collection does not exist, creating new one")
        _recreate_milvus_collection(client, collection_name, dim)
    elif not _check_milvus_collection_has_user_id(client, collection_name):
        logger.info("[milvus] Collection exists but lacks user_id field, recreating")
        _recreate_milvus_collection(client, collection_name, dim)
    else:
        logger.info("[milvus] Collection already has user_id field")

    client.load_collection(collection_name)

    total = 0
    offset = 0
    while True:
        async with get_db() as db:
            result = await db.execute(
                select(RagChunk.id, RagChunk.content, RagChunk.embedding)
                .order_by(RagChunk.id)
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            rows = result.all()
        if not rows:
            break

        data = []
        for row in rows:
            emb = row.embedding
            if not emb:
                continue
            if dim and len(emb) != dim:
                continue
            data.append({
                "id": int(row.id),
                "embedding": emb,
                "content": row.content,
                "user_id": default_user_id,
            })

        if data:
            client.insert(collection_name, data)
            total += len(data)
            logger.info(f"[milvus] Re-indexed {total} chunks so far...")

        offset += len(rows)
        if len(rows) < BATCH_SIZE:
            break

    logger.info(f"[milvus] Done: re-indexed {total} chunks with user_id={default_user_id}")
    client.close()


async def reindex_es(default_user_id: str) -> None:
    """Re-index all rag_chunks into ES with correct user_id."""
    settings = get_settings()
    if not settings.es_addresses:
        logger.info("[es] Skipped (ES_ADDRESSES not configured)")
        return

    try:
        from elasticsearch import AsyncElasticsearch
    except ImportError:
        logger.info("[es] Skipped (elasticsearch not installed)")
        return

    index_name = "rag_chunks"
    es_client = AsyncElasticsearch(settings.es_addresses.split(","))

    total = 0
    offset = 0
    while True:
        async with get_db() as db:
            result = await db.execute(
                select(
                    RagChunk.id,
                    RagChunk.content,
                    RagChunk.doc_hash,
                    RagChunk.chunk_idx,
                )
                .order_by(RagChunk.id)
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            rows = result.all()
        if not rows:
            break

        for row in rows:
            try:
                await es_client.index(
                    index=index_name,
                    id=str(row.id),
                    body={
                        "content": row.content,
                        "doc_hash": row.doc_hash,
                        "chunk_idx": row.chunk_idx,
                        "user_id": default_user_id,
                    },
                )
                total += 1
                if total % 500 == 0:
                    logger.info(f"[es] Re-indexed {total} chunks so far...")
            except Exception as e:
                logger.warning(f"[es] Failed to index chunk {row.id}: {e}")

        offset += len(rows)
        if len(rows) < BATCH_SIZE:
            break

    logger.info(f"[es] Done: re-indexed {total} chunks with user_id={default_user_id}")
    await es_client.close()


async def main() -> None:
    settings = get_settings()
    await init_db()

    email = settings.default_user_email or "admin@local"

    async with get_db() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    if user is None:
        logger.error(f"Default user '{email}' not found. Run migrate_to_multi_user first.")
        return

    default_user_id = user.id
    logger.info(f"Default user: {email} (id={default_user_id})")

    # 1. Backfill PG
    await backfill_pg(default_user_id)

    # 2. Re-index Milvus
    await reindex_milvus(default_user_id)

    # 3. Re-index ES
    await reindex_es(default_user_id)

    logger.info("\n[backfill] All done!")


if __name__ == "__main__":
    asyncio.run(main())
