"""GraphRetrieval — 图谱检索增强封装：PPR + entity/triple vector search。

检索路径：
1. 先从 MilvusGraphVectorStore 向量召回 entity/triple（如果可用）
2. 合并 entity + triple 召回结果，去重并保留 recall score
3. 再从 Neo4j PPR 扩散，返回关联 pg_id 列表

封装 KGStore.search_with_ppr，提供统一检索接口供 HybridStore._fetch_kg 调用。
KGStore 不可用时返回空结果（不阻塞主检索流程）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.graph.types import get_entity_type_weight

if TYPE_CHECKING:
    from app.graph.kgstore import KGStore
    from app.infra.hybrid import RetrievalConfig

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]


@dataclass
class SeedInfo:
    """PPR 种子信息（name + entity_type + recall_score）"""
    name: str
    entity_type: str = "Unknown"
    recall_score: float = 1.0


class GraphRetrieval:
    """图谱检索增强：封装 PPR + entity/triple vector search。

    通过 set_kg_store() 注入 KGStore 实例（由 main.py 在启动时注入）。
    search() 从查询中抽取实体 → PPR 检索 → 返回关联 pg_id 列表。
    """

    _kg_store: KGStore | None = None
    _embed_fn: EmbedFn | None = None

    @classmethod
    def set_kg_store(cls, kg_store: KGStore) -> None:
        """注入 KGStore 实例（由 main.py 在启动时调用）。"""
        cls._kg_store = kg_store
        logger.info("GraphRetrieval: KGStore injected")

    @classmethod
    def set_embed_fn(cls, fn: EmbedFn) -> None:
        """注入 embed_fn（用于为查询文本生成向量，在 Milvus 向量召回时使用）。"""
        cls._embed_fn = fn
        logger.info("GraphRetrieval: embed_fn injected")

    @classmethod
    def available(cls) -> bool:
        """检查 GraphRetrieval 是否可用（KGStore 已注入且 Neo4j 可用）。"""
        return cls._kg_store is not None and cls._kg_store.available()

    @classmethod
    async def search(
        cls,
        query: str,
        top_k: int,
        expand_depth: int = 1,
        *,
        retrieval_config: RetrievalConfig | None = None,
    ) -> list[dict]:
        """先 Milvus 向量召回 entity/triple → 再 Neo4j PPR 扩散 → 返回关联 pg_id 列表。

        返回格式对齐 HybridStore 期望：List[dict] with pg_id, content, score, entities 键。
        expand_depth 控制遍历深度（1-3），默认 1。

        Args:
            retrieval_config: 检索配置，控制种子加权策略和 triple 注入。
        """
        # 解析 retrieval_config 参数
        weight_by_type = True
        weight_by_score = True
        triple_inject = True
        type_weight_overrides: dict[str, float] | None = None
        if retrieval_config is not None:
            weight_by_type = retrieval_config.graph_seed_weight_by_type
            weight_by_score = retrieval_config.graph_seed_weight_by_score
            triple_inject = retrieval_config.graph_triple_inject_seeds
            type_weight_overrides = retrieval_config.graph_entity_type_weights

        # 1. entity 向量召回
        entity_hits = await cls._milvus_entity_recall(query, top_k)

        # 2. triple 向量召回（如果启用）
        triple_hits: list[dict] = []
        if triple_inject:
            triple_hits = await cls._milvus_triple_recall(query, top_k)

        # 3. 合并 entity + triple 召回结果为 SeedInfo 列表
        seeds = _merge_seeds(
            entity_hits, triple_hits,
            weight_by_score=weight_by_score,
        )

        if seeds:
            # 用合并后的 seeds 做 Neo4j PPR 扩散
            return await cls._ppr_search_weighted(
                seeds, top_k, expand_depth,
                weight_by_type=weight_by_type,
                weight_by_score=weight_by_score,
                type_weight_overrides=type_weight_overrides,
            )

        # 降级：从查询文本抽取实体做 Neo4j PPR
        if not cls.available():
            return []

        kg_store = cls._kg_store
        assert kg_store is not None

        try:
            return await kg_store.search(query, top_k, expand_depth=expand_depth)
        except Exception as e:
            logger.warning("GraphRetrieval search failed: %s", e)
            return []

    @classmethod
    async def _milvus_entity_recall(
        cls,
        query: str,
        top_k: int,
    ) -> list[dict]:
        """从 MilvusGraphVectorStore 向量召回 entity 结果列表。

        返回格式: [{"name": str, "entity_type": str, "score": float}]
        如果 MilvusGraphVectorStore 或 embed_fn 不可用，返回空列表。
        """
        if not cls._embed_fn:
            return []
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            if not MilvusGraphVectorStore.available():
                return []
        except ImportError:
            return []

        try:
            query_emb = cls._embed_fn(query)
        except Exception as e:
            logger.warning("GraphRetrieval: query embed failed: %s", e)
            return []
        if not query_emb:
            return []

        try:
            hits = await MilvusGraphVectorStore.search_entities(
                query, query_emb, top_k,
            )
        except Exception as e:
            logger.warning("GraphRetrieval: milvus entity recall failed: %s", e)
            hits = []

        # 解析 entity 名称和类型
        results: list[dict] = []
        for hit in hits:
            content = hit.get("content", "")
            if content:
                name = content.rsplit(" (", 1)[0].strip()
                if name:
                    results.append({
                        "name": name,
                        "entity_type": hit.get("entity_type", "Unknown") or "Unknown",
                        "score": float(hit.get("score", 1.0)),
                    })

        return results

    @classmethod
    async def _milvus_triple_recall(
        cls,
        query: str,
        top_k: int,
    ) -> list[dict]:
        """从 MilvusGraphVectorStore 向量召回 triple 结果列表。

        返回格式与 search_entities 一致: [{"name": str, "entity_type": str, "score": float}]
        triple 的 subject + object 被解析为两个 entity 条目。
        """
        if not cls._embed_fn:
            return []
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            if not MilvusGraphVectorStore.available():
                return []
        except ImportError:
            return []

        try:
            query_emb = cls._embed_fn(query)
        except Exception as e:
            logger.warning("GraphRetrieval: query embed for triple recall failed: %s", e)
            return []
        if not query_emb:
            return []

        try:
            hits = await MilvusGraphVectorStore.search_triples(
                query, query_emb, top_k,
            )
        except Exception as e:
            logger.warning("GraphRetrieval: milvus triple recall failed: %s", e)
            return []

        # 解析 triple 的 subject + object 为 entity 条目
        results: list[dict] = []
        for hit in hits:
            score = float(hit.get("score", 1.0))
            source_id = hit.get("source_id", "")
            target_id = hit.get("target_id", "")
            if source_id:
                results.append({
                    "name": source_id,
                    "entity_type": "Unknown",
                    "score": score,
                })
            if target_id:
                results.append({
                    "name": target_id,
                    "entity_type": "Unknown",
                    "score": score,
                })

        return results

    @classmethod
    async def _ppr_search_weighted(
        cls,
        seeds: list[SeedInfo],
        top_k: int,
        expand_depth: int,
        *,
        weight_by_type: bool = True,
        weight_by_score: bool = True,
        type_weight_overrides: dict[str, float] | None = None,
    ) -> list[dict]:
        """用给定的 seed 列表做 Neo4j PPR 扩散，支持种子加权。"""
        if not cls.available() or not seeds:
            return []

        kg_store = cls._kg_store
        assert kg_store is not None

        # 构建 query_entities + seed_weights + entity_types
        query_entities = [s.name for s in seeds]
        entity_types = [s.entity_type for s in seeds]

        seed_weights: list[float] = []
        for s in seeds:
            type_w = get_entity_type_weight(s.entity_type, type_weight_overrides) if weight_by_type else 1.0
            score_w = s.recall_score if weight_by_score else 1.0
            seed_weights.append(type_w * score_w)

        try:
            return await kg_store.search_with_ppr(
                query_entities, top_k, expand_depth,
                seed_weights=seed_weights,
                entity_types=entity_types,
                weight_by_type=weight_by_type,
            )
        except Exception as e:
            logger.warning("GraphRetrieval weighted PPR search failed: %s", e)
            return []


def _merge_seeds(
    entity_hits: list[dict],
    triple_hits: list[dict],
    *,
    weight_by_score: bool = True,
) -> list[SeedInfo]:
    """合并 entity + triple 召回结果，去重并保留 max recall score。

    每个 hit 的格式: {"name": str, "entity_type": str, "score": float}
    """
    merged: dict[str, SeedInfo] = {}

    for hit in entity_hits + triple_hits:
        name = hit.get("name", "").strip()
        if not name:
            continue
        score = float(hit.get("score", 1.0))
        etype = hit.get("entity_type", "Unknown") or "Unknown"

        if name in merged:
            existing = merged[name]
            # 保留 max recall score
            if score > existing.recall_score:
                existing.recall_score = score
            # 保留非 Unknown 的类型
            if etype != "Unknown" and existing.entity_type == "Unknown":
                existing.entity_type = etype
        else:
            merged[name] = SeedInfo(
                name=name,
                entity_type=etype,
                recall_score=score if weight_by_score else 1.0,
            )

    return list(merged.values())
