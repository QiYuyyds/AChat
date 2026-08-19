"""GraphBuildTask — 异步图谱构建任务，带状态机、重试机制、并发控制。

文档 RAG 索引完成后由 DocumentService 触发，管理 graph_status 生命周期：
  graph_pending → graph_building → graph_indexed (成功) / error_graph (失败)

图谱构建是增强路径：失败后文档仍可正常检索（只是没有 KG 增强）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import update

from app.config import get_settings
from app.db.engine import get_remote_db
from app.db.models import Document
from app.graph.types import ChunkRef

if TYPE_CHECKING:
    from app.graph.extractor import Extractor
    from app.graph.kgstore import KGStore

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]

# ── 常量 ──
FETCH_MIN_SIZE = 100
FETCH_MAX_SIZE = 1000
MAX_EXTRACTION_ATTEMPTS = 3
RETRY_DELAYS = (2.0, 10.0)
LLM_CONCURRENCY = 5


class GraphBuildTask:
    """异步图谱构建任务：分批拉取 chunks → 并发 extract_batch → 并发 Neo4j MERGE → 更新状态。

    通过 set_kg_store() 注入 KGStore 实例（由 main.py 在启动时注入）。
    build() 是 fire-and-forget：由 asyncio.create_task() 触发，不阻塞 ingest 流程。
    """

    _kg_store: KGStore | None = None
    _extractor: Extractor | None = None
    _embed_fn: EmbedFn | None = None

    @classmethod
    def set_kg_store(cls, kg_store: KGStore, extractor: Extractor) -> None:
        """注入 KGStore + Extractor（由 main.py 在启动时调用）。"""
        cls._kg_store = kg_store
        cls._extractor = extractor
        logger.info("GraphBuildTask: KGStore + Extractor injected")

    @classmethod
    def set_embed_fn(cls, fn: EmbedFn) -> None:
        """注入 embed_fn（用于为 entity/triple 内容生成向量写入 MilvusGraphVectorStore）。"""
        cls._embed_fn = fn
        logger.info("GraphBuildTask: embed_fn injected")

    @classmethod
    def available(cls) -> bool:
        """检查 GraphBuildTask 是否可用（KGStore + Extractor 均已注入）。"""
        return cls._kg_store is not None and cls._extractor is not None

    @classmethod
    async def build(cls, doc_hash: str, chunks: list[ChunkRef], *, document_id: str = "") -> dict:
        """构建知识图谱：分批抽取实体 → 并发 Neo4j MERGE → 更新 Document.graph_status。

        失败时按 RETRY_DELAYS 间隔重试 MAX_EXTRACTION_ATTEMPTS 次。
        全部失败后 graph_status = 'error_graph'，但不影响文档检索。
        document_id 用于更新 Document.graph_status；为空时尝试通过 doc_hash 反查。
        """
        if not cls.available():
            logger.warning("GraphBuildTask not available (KGStore/Extractor not injected)")
            return {"status": "skipped", "doc_hash": doc_hash}

        settings = get_settings()
        max_attempts = max(1, settings.rag_graph_max_extraction_attempts)
        retry_delays = _parse_retry_delays(settings.rag_graph_retry_delays)
        llm_concurrency = settings.rag_graph_concurrency or LLM_CONCURRENCY

        # 反查 document_id（如果未提供）
        if not document_id:
            document_id = await cls._find_document_id_by_doc_hash(doc_hash)

        # 状态流转：开始 → graph_building
        await cls._update_graph_status(document_id, "graph_building")

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await cls._build_once(doc_hash, chunks, llm_concurrency)
                # 成功 → graph_indexed
                await cls._update_graph_status(document_id, "graph_indexed")
                logger.info(
                    "GraphBuildTask: graph_indexed (doc_hash=%s, chunks=%d, attempt=%d)",
                    doc_hash, len(chunks), attempt,
                )
                return {"status": "graph_indexed", "doc_hash": doc_hash, "chunks": len(chunks)}
            except Exception as e:
                last_error = e
                logger.warning(
                    "GraphBuildTask: attempt %d/%d failed (doc_hash=%s): %s",
                    attempt, max_attempts, doc_hash, e,
                )
                if attempt < max_attempts:
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    await asyncio.sleep(delay)

        # 全部失败 → error_graph
        await cls._update_graph_status(document_id, "error_graph")
        logger.error(
            "GraphBuildTask: error_graph (doc_hash=%s, chunks=%d, last_error=%s)",
            doc_hash, len(chunks), last_error,
        )
        return {"status": "error_graph", "doc_hash": doc_hash, "error": str(last_error)}

    @classmethod
    async def _build_once(
        cls,
        doc_hash: str,
        chunks: list[ChunkRef],
        llm_concurrency: int,
    ) -> None:
        """单次构建：分批 extract_batch → Neo4j MERGE。失败抛异常。"""
        kg_store = cls._kg_store
        extractor = cls._extractor
        assert kg_store is not None and extractor is not None

        if not chunks:
            return

        # 分批处理（自适应 batch size）
        batch_size = _resolve_batch_size(len(chunks))
        total = len(chunks)

        for start in range(0, total, batch_size):
            batch = chunks[start:start + batch_size]

            # 并发抽取实体
            results = await extractor.extract_batch(
                batch, concurrency=llm_concurrency,
            )

            # 构建 chunk-ref 映射，为实体/关系打标 pg_id + chunk_id + doc_hash
            for idx, chunk_ref in enumerate(batch):
                result = results[idx] if idx < len(results) else None
                if not result or not result.entities:
                    continue
                # 打标元数据
                for ent in result.entities:
                    ent.doc_hash = doc_hash
                    ent.chunk_id = chunk_ref.id
                    ent.pg_id = chunk_ref.pg_id
                for rel in result.relations:
                    rel.doc_hash = doc_hash
                    rel.chunk_id = chunk_ref.id
                    rel.pg_id = chunk_ref.pg_id

                # 并发 Neo4j MERGE
                await cls._merge_to_neo4j(kg_store, result)

                # 同步写入 MilvusGraphVectorStore（entity + triple）
                await cls._upsert_to_milvus(doc_hash, chunk_ref, result)

            logger.debug(
                "GraphBuildTask: batch %d-%d/%d done (doc_hash=%s)",
                start, min(start + batch_size, total), total, doc_hash,
            )

    @classmethod
    async def _merge_to_neo4j(cls, kg_store: KGStore, result) -> None:
        """将一批实体/关系并发写入 Neo4j（使用 KGStore 的 _upsert 方法）。"""
        settings = get_settings()
        neo4j_concurrency = max(1, settings.rag_graph_neo4j_concurrency or 4)
        sem = asyncio.Semaphore(neo4j_concurrency)

        async def _upsert_entity(ent):
            async with sem:
                await kg_store._upsert_entity(ent)

        async def _upsert_relation(rel):
            async with sem:
                await kg_store._upsert_relation(rel)

        tasks = []
        for ent in result.entities:
            tasks.append(_upsert_entity(ent))
        for rel in result.relations:
            tasks.append(_upsert_relation(rel))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    @classmethod
    async def _upsert_to_milvus(cls, doc_hash: str, chunk_ref: ChunkRef, result) -> None:
        """将实体和三元组写入 MilvusGraphVectorStore（entity + triple Collection）。"""
        if not cls._embed_fn:
            return
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            if not MilvusGraphVectorStore.available():
                return
        except ImportError:
            return

        embed_fn = cls._embed_fn
        settings = get_settings()
        dim = settings.rag_milvus_dim

        # 构建 entity dicts
        entity_dicts: list[dict] = []
        for ent in result.entities:
            ent_id = _make_entity_id(doc_hash, chunk_ref.id, ent.name)
            content = f"{ent.name} ({ent.type})"
            try:
                emb = embed_fn(content)
            except Exception as e:
                logger.warning("GraphBuildTask: entity embed failed (%s): %s", ent.name, e)
                continue
            if not emb or (dim and len(emb) != dim):
                continue
            entity_dicts.append({
                "id": ent_id,
                "content": content,
                "embedding": emb,
                "entity_type": str(ent.type),
                "doc_hash": doc_hash,
                "chunk_id": chunk_ref.id,
                "pg_id": chunk_ref.pg_id,
            })

        # 构建 triple dicts
        triple_dicts: list[dict] = []
        for rel in result.relations:
            triple_id = _make_triple_id(doc_hash, chunk_ref.id, rel.from_name, rel.to_name, rel.rel_type)
            content = f"{rel.from_name} {rel.rel_type} {rel.to_name}"
            try:
                emb = embed_fn(content)
            except Exception as e:
                logger.warning("GraphBuildTask: triple embed failed (%s): %s", content, e)
                continue
            if not emb or (dim and len(emb) != dim):
                continue
            triple_dicts.append({
                "id": triple_id,
                "content": content,
                "embedding": emb,
                "source_id": _make_entity_id(doc_hash, chunk_ref.id, rel.from_name),
                "target_id": _make_entity_id(doc_hash, chunk_ref.id, rel.to_name),
                "relation_type": rel.rel_type,
                "doc_hash": doc_hash,
                "chunk_id": chunk_ref.id,
                "pg_id": chunk_ref.pg_id,
            })

        if entity_dicts:
            await MilvusGraphVectorStore.upsert_entities(entity_dicts)
        if triple_dicts:
            await MilvusGraphVectorStore.upsert_triples(triple_dicts)

    @classmethod
    async def _find_document_id_by_doc_hash(cls, doc_hash: str) -> str:
        """通过 doc_hash 从 RagChunk 反查 document_id。"""
        try:
            from sqlalchemy import select

            from app.db.models import RagChunk

            async with get_remote_db() as session:
                result = await session.execute(
                    select(RagChunk.document_id)
                    .where(RagChunk.doc_hash == doc_hash)
                    .limit(1)
                )
                row = result.first()
                return row[0] if row else ""
        except Exception as e:
            logger.warning(
                "GraphBuildTask: failed to find document_id for doc_hash=%s: %s",
                doc_hash, e,
            )
            return ""

    @classmethod
    async def _update_graph_status(cls, document_id: str, status: str) -> None:
        """乐观更新 Document.graph_status（按 document_id 匹配）。"""
        if not document_id:
            return
        try:
            async with get_remote_db() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(graph_status=status)
                )
        except Exception as e:
            logger.warning(
                "GraphBuildTask: failed to update graph_status=%s for doc=%s: %s",
                status, document_id, e,
            )


def _resolve_batch_size(total_chunks: int) -> int:
    """根据 chunk 数量自适应 batch size（100-1000）。"""
    if total_chunks <= FETCH_MIN_SIZE:
        return FETCH_MIN_SIZE
    if total_chunks > FETCH_MAX_SIZE:
        return FETCH_MAX_SIZE
    return total_chunks


def _make_entity_id(doc_hash: str, chunk_id: int, name: str) -> str:
    """生成 entity 的 Milvus primary key（确定性，幂等 upsert）。"""
    raw = f"{doc_hash}:{chunk_id}:{name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _make_triple_id(doc_hash: str, chunk_id: int, from_name: str, to_name: str, rel_type: str) -> str:
    """生成 triple 的 Milvus primary key（确定性，幂等 upsert）。"""
    raw = f"{doc_hash}:{chunk_id}:{from_name}:{rel_type}:{to_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse_retry_delays(raw: str) -> list[float]:
    """解析逗号分隔的 retry delay 字符串为 float 列表。"""
    if not raw:
        return list(RETRY_DELAYS)
    try:
        parts = [float(s.strip()) for s in raw.split(",") if s.strip()]
        return parts if parts else list(RETRY_DELAYS)
    except ValueError:
        return list(RETRY_DELAYS)
