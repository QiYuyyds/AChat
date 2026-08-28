"""MilvusGraphVectorStore — 图谱 entity/triple Milvus 向量存储。

为 entity 和 triple 各创建独立 Milvus Collection，schema 对齐 rag_embeddings：
  - content: VARCHAR, enable_analyzer=True, analyzer_params={"type":"chinese"}
  - embedding: FLOAT_VECTOR, COSINE, IVF_FLAT
  - content_sparse: SPARSE_FLOAT_VECTOR + Function(BM25) + SPARSE_INVERTED_INDEX
  - user_id: VARCHAR, 用于按用户隔离检索

triple Collection 额外有 source_id / target_id 字段。

检索时先从 Milvus 向量召回 entity/triple，再从 Neo4j PPR 扩散。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)

_ENTITY_COLLECTION = "rag_graph_entities"
_TRIPLE_COLLECTION = "rag_graph_triples"

_CONTENT_MAX_LENGTH = 65535
_ID_MAX_LENGTH = 128


class MilvusGraphVectorStore:
    """图谱 entity/triple 的 Milvus 向量存储。

    通过 set_client() 注入 MilvusClient 实例（由 main.py 在启动时注入）。
    不可用时所有操作优雅降级（返回空结果，不阻塞主流程）。
    """

    _client: MilvusClient | None = None
    _dim: int = 0
    _initialized: bool = False

    @classmethod
    def set_client(cls, client: MilvusClient, dim: int) -> None:
        """注入 MilvusClient + embedding 维度（由 main.py 在启动时调用）。"""
        cls._client = client
        cls._dim = dim
        cls._initialized = False
        logger.info("MilvusGraphVectorStore: MilvusClient injected (dim=%d)", dim)

    @classmethod
    def available(cls) -> bool:
        """检查 MilvusGraphVectorStore 是否可用。"""
        return cls._client is not None

    @classmethod
    def _ensure_collections(cls) -> None:
        """幂等创建 entity + triple Collection（如果不存在）。"""
        if cls._initialized or not cls.available():
            return
        client = cls._client
        assert client is not None
        try:
            cls._ensure_entity_collection(client)
            cls._ensure_triple_collection(client)
            cls._initialized = True
            logger.info("MilvusGraphVectorStore: collections ready")
        except Exception as e:
            logger.warning("MilvusGraphVectorStore: ensure_collections failed: %s", e)

    @classmethod
    def _ensure_entity_collection(cls, client) -> None:
        """创建 entity Collection（如果不存在）。"""
        from pymilvus import DataType, Function, FunctionType

        if client.has_collection(_ENTITY_COLLECTION):
            return

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=_ID_MAX_LENGTH)
        schema.add_field(
            "content", DataType.VARCHAR, max_length=_CONTENT_MAX_LENGTH,
            enable_analyzer=True, analyzer_params={"type": "chinese"},
        )
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=cls._dim)
        schema.add_field("content_sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("entity_type", DataType.VARCHAR, max_length=64)
        schema.add_field("doc_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.INT64)
        schema.add_field("pg_id", DataType.INT64)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)

        schema.add_function(Function(
            name="content_bm25",
            input_field_names=["content"],
            output_field_names=["content_sparse"],
            function_type=FunctionType.BM25,
        ))

        client.create_collection(
            _ENTITY_COLLECTION, schema=schema, metric_type="COSINE",
        )

        index_params = client.prepare_index_params()
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
        client.create_index(_ENTITY_COLLECTION, index_params)
        logger.info("MilvusGraphVectorStore: entity collection '%s' created", _ENTITY_COLLECTION)

    @classmethod
    def _ensure_triple_collection(cls, client) -> None:
        """创建 triple Collection（如果不存在）。"""
        from pymilvus import DataType, Function, FunctionType

        if client.has_collection(_TRIPLE_COLLECTION):
            return

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=_ID_MAX_LENGTH)
        schema.add_field(
            "content", DataType.VARCHAR, max_length=_CONTENT_MAX_LENGTH,
            enable_analyzer=True, analyzer_params={"type": "chinese"},
        )
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=cls._dim)
        schema.add_field("content_sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("source_id", DataType.VARCHAR, max_length=_ID_MAX_LENGTH)
        schema.add_field("target_id", DataType.VARCHAR, max_length=_ID_MAX_LENGTH)
        schema.add_field("relation_type", DataType.VARCHAR, max_length=64)
        schema.add_field("doc_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.INT64)
        schema.add_field("pg_id", DataType.INT64)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)

        schema.add_function(Function(
            name="content_bm25",
            input_field_names=["content"],
            output_field_names=["content_sparse"],
            function_type=FunctionType.BM25,
        ))

        client.create_collection(
            _TRIPLE_COLLECTION, schema=schema, metric_type="COSINE",
        )

        index_params = client.prepare_index_params()
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
        client.create_index(_TRIPLE_COLLECTION, index_params)
        logger.info("MilvusGraphVectorStore: triple collection '%s' created", _TRIPLE_COLLECTION)

    # ── Entity upsert/search ──

    @classmethod
    async def upsert_entities(cls, entities: list[dict]) -> None:
        """批量写入 entity 向量 + BM25。

        每个 entity dict 需含: id, content, embedding, entity_type, doc_hash, chunk_id, pg_id
        可选: user_id
        """
        if not entities or not cls.available():
            return
        cls._ensure_collections()
        client = cls._client
        assert client is not None
        try:
            client.upsert(_ENTITY_COLLECTION, entities)
            logger.debug("MilvusGraphVectorStore: upserted %d entities", len(entities))
        except Exception as e:
            logger.warning("MilvusGraphVectorStore: upsert_entities failed: %s", e)

    @classmethod
    async def upsert_triples(cls, triples: list[dict]) -> None:
        """批量写入 triple 向量 + BM25。

        每个 triple dict 需含: id, content, embedding, source_id, target_id,
        relation_type, doc_hash, chunk_id, pg_id
        可选: user_id
        """
        if not triples or not cls.available():
            return
        cls._ensure_collections()
        client = cls._client
        assert client is not None
        try:
            client.upsert(_TRIPLE_COLLECTION, triples)
            logger.debug("MilvusGraphVectorStore: upserted %d triples", len(triples))
        except Exception as e:
            logger.warning("MilvusGraphVectorStore: upsert_triples failed: %s", e)

    @classmethod
    async def search_entities(
        cls,
        query_text: str,
        query_embedding: list[float],
        top_k: int,
        *,
        user_id: str = "",
    ) -> list[dict]:
        """向量召回 entity：dense embedding + BM25 hybrid search。

        返回格式: [{"id": str, "content": str, "score": float, "entity_type": str, "pg_id": int}]
        user_id 非空时按 user_id filter 过滤结果。
        """
        if not cls.available():
            return []
        cls._ensure_collections()
        client = cls._client
        assert client is not None
        if not client.has_collection(_ENTITY_COLLECTION):
            return []

        try:
            from pymilvus import AnnSearchRequest, WeightedRanker

            client.load_collection(_ENTITY_COLLECTION)

            filter_expr = f'user_id == "{user_id}"' if user_id else ""

            vector_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=top_k,
                filter=filter_expr,
            )
            bm25_req = AnnSearchRequest(
                data=[query_text],
                anns_field="content_sparse",
                param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.0}},
                limit=top_k,
                filter=filter_expr,
            )
            reranker = WeightedRanker(0.7, 0.3)
            results = client.hybrid_search(
                collection_name=_ENTITY_COLLECTION,
                reqs=[vector_req, bm25_req],
                rerank=reranker,
                limit=top_k,
                output_fields=["content", "entity_type", "pg_id", "doc_hash", "chunk_id", "user_id"],
                filter=filter_expr,
            )
            return [
                {
                    "id": hit["id"],
                    "content": hit["entity"].get("content", ""),
                    "score": hit["distance"],
                    "entity_type": hit["entity"].get("entity_type", ""),
                    "pg_id": hit["entity"].get("pg_id", 0),
                }
                for hit in (results[0] if results else [])
            ]
        except Exception as e:
            logger.warning("MilvusGraphVectorStore: search_entities failed: %s", e)
            return []

    @classmethod
    async def search_triples(
        cls,
        query_text: str,
        query_embedding: list[float],
        top_k: int,
        *,
        user_id: str = "",
    ) -> list[dict]:
        """向量召回 triple：dense embedding + BM25 hybrid search。

        返回格式: [{"id": str, "content": str, "score": float, "source_id": str, "target_id": str, "pg_id": int}]
        user_id 非空时按 user_id filter 过滤结果。
        """
        if not cls.available():
            return []
        cls._ensure_collections()
        client = cls._client
        assert client is not None
        if not client.has_collection(_TRIPLE_COLLECTION):
            return []

        try:
            from pymilvus import AnnSearchRequest, WeightedRanker

            client.load_collection(_TRIPLE_COLLECTION)

            filter_expr = f'user_id == "{user_id}"' if user_id else ""

            vector_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=top_k,
                filter=filter_expr,
            )
            bm25_req = AnnSearchRequest(
                data=[query_text],
                anns_field="content_sparse",
                param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.0}},
                limit=top_k,
                filter=filter_expr,
            )
            reranker = WeightedRanker(0.7, 0.3)
            results = client.hybrid_search(
                collection_name=_TRIPLE_COLLECTION,
                reqs=[vector_req, bm25_req],
                rerank=reranker,
                limit=top_k,
                output_fields=["content", "source_id", "target_id", "relation_type", "pg_id", "doc_hash", "chunk_id", "user_id"],
                filter=filter_expr,
            )
            return [
                {
                    "id": hit["id"],
                    "content": hit["entity"].get("content", ""),
                    "score": hit["distance"],
                    "source_id": hit["entity"].get("source_id", ""),
                    "target_id": hit["entity"].get("target_id", ""),
                    "pg_id": hit["entity"].get("pg_id", 0),
                }
                for hit in (results[0] if results else [])
            ]
        except Exception as e:
            logger.warning("MilvusGraphVectorStore: search_triples failed: %s", e)
            return []

    # ── Delete by IDs ──

    @classmethod
    async def delete_by_ids(
        cls,
        entity_ids: list[str],
        triple_ids: list[str] | None = None,
    ) -> None:
        """按 ID 批量删除 entity 和 triple 向量。

        用于文档删除时清理 MilvusGraphVectorStore 中的图谱向量。
        """
        if not cls.available():
            return
        cls._ensure_collections()
        client = cls._client
        assert client is not None

        if entity_ids:
            try:
                if client.has_collection(_ENTITY_COLLECTION):
                    client.delete(_ENTITY_COLLECTION, entity_ids)
                    logger.debug(
                        "MilvusGraphVectorStore: deleted %d entity vectors", len(entity_ids),
                    )
            except Exception as e:
                logger.warning("MilvusGraphVectorStore: delete entity vectors failed: %s", e)

        if triple_ids:
            try:
                if client.has_collection(_TRIPLE_COLLECTION):
                    client.delete(_TRIPLE_COLLECTION, triple_ids)
                    logger.debug(
                        "MilvusGraphVectorStore: deleted %d triple vectors", len(triple_ids),
                    )
            except Exception as e:
                logger.warning("MilvusGraphVectorStore: delete triple vectors failed: %s", e)
