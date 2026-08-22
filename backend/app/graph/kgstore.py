# kgstore — Neo4j 知识图谱存储 + 多跳遍历检索（async 适配版）
#
# 从 AGI-memory internal/graph/kgstore.py 移植，适配点：
# - Neo4j 驱动从同步 Neo4jClient 改为 neo4j.AsyncDriver
# - 所有 Cypher 操作改为 async（_run_cypher 辅助方法）
# - index_document / delete_document / search 均为 async
# - search 返回 List[dict]（含 pg_id 键）而非 List[GraphSearchResult]
# - 与 GraphMemory 共享同一 AsyncDriver 实例
# - user_id 标签隔离 + Chunk 节点 + MENTIONS 边 + 确定性 ID
import json
import logging

from app.config import Settings

from .extractors.base import GraphExtractor
from .extractors.factory import GraphExtractorFactory
from .graph_utils import (
    compute_entity_id,
    compute_triple_id,
    cypher_delete_document,
    cypher_graph_labels,
    cypher_graph_stats_edges,
    cypher_graph_stats_entity_types,
    cypher_graph_stats_nodes,
    cypher_graph_subgraph,
    cypher_merge_chunk,
    cypher_merge_entity_mention,
    cypher_merge_relation,
    cypher_query_entity_ids_by_doc_hash,
    cypher_query_triple_ids_by_doc_hash,
    cypher_search_direct,
    cypher_search_ppr_apoc,
    cypher_search_ppr_gds,
    cypher_search_subgraph,
    normalize_entity_name,
    safe_user_label,
)
from .types import (
    ChunkRef,
    Entity,
    Relation,
    get_entity_type_weight,
)

logger = logging.getLogger(__name__)


class KGStore:
    """在 Neo4j AsyncDriver 之上封装 RAG 专用的图操作：
    - index_document：文档摄入时写入 Chunk 节点、Entity 节点、MENTIONS 边、RELATION 边
    - delete_document：删除文档及其关联的孤立节点 + Milvus 向量清理
    - search：根据查询实体做 1~2 跳子图扩展，返回关联的 pg_id 列表
    所有操作在 Neo4j 不可用时均优雅降级（返回空结果，不阻塞主流程）。

    user_id 用于 Neo4j 标签隔离，确保不同用户的实体节点不 MERGE 冲突。
    """

    def __init__(
        self,
        settings: Settings,
        driver=None,  # neo4j.AsyncDriver | None
        extractor: GraphExtractor | None = None,
        user_id: str = "",
    ):
        self._driver = driver
        self.max_hops = settings.kg_max_hops
        self.kg_weight = settings.kg_weight
        self.extractor = extractor or GraphExtractorFactory.create("llm", {"llm_fn": None})
        self._user_id = user_id
        self._user_label = safe_user_label(user_id)

    # ── 基础能力 ───────────────────────────────────────────────────────────

    def available(self) -> bool:
        """图存储是否可用"""
        return self._driver is not None

    def set_user_id(self, user_id: str) -> None:
        """切换 user_id（per-request 场景）。"""
        self._user_id = user_id
        self._user_label = safe_user_label(user_id)

    # ── Cypher 辅助 ─────────────────────────────────────────────────────────

    async def _run_cypher(self, query: str, params: dict) -> list:
        """执行 Cypher 查询并返回记录列表（list[dict]）。"""
        async with self._driver.session() as session:
            result = await session.run(query, parameters=params)
            return await result.data()

    # ─────────────────────────────── 文档摄入 ──────────────────────────────

    async def index_document(
        self, doc_hash: str, chunks: list[ChunkRef], *, file_id: str = "",
    ) -> None:
        """为一批 chunks 抽取实体关系并写入图（不阻塞主 Ingest 流程）。

        1. MERGE Chunk 节点
        2. MERGE Entity + MENTIONS 边
        3. MERGE RELATION 边
        """
        if not self.available():
            return
        for c in chunks:
            result = self.extractor.extract(c.content)
            if not result.entities:
                continue

            # 1. MERGE Chunk 节点
            await self._upsert_chunk(c, doc_hash, file_id)

            # 2. 写入实体节点 + MENTIONS 边
            for ent in result.entities:
                ent.doc_hash = doc_hash
                ent.chunk_id = c.id
                ent.pg_id = c.pg_id
                await self._upsert_entity(ent, file_id)

            # 3. 写入关系边
            for rel in result.relations:
                rel.doc_hash = doc_hash
                rel.chunk_id = c.id
                rel.pg_id = c.pg_id
                await self._upsert_relation(rel, file_id)

        logger.info(
            "🕸️  知识图谱索引完成：docHash=%s，chunks=%d，user=%s",
            doc_hash, len(chunks), self._user_id or "anonymous",
        )

    async def _upsert_chunk(
        self, chunk_ref: ChunkRef, doc_hash: str, file_id: str,
    ) -> None:
        """MERGE Chunk 节点（幂等）。"""
        query = cypher_merge_chunk(self._user_label)
        try:
            await self._run_cypher(query, {
                "chunk_id": chunk_ref.id,
                "doc_hash": doc_hash,
                "file_id": file_id,
                "pg_id": chunk_ref.pg_id,
                "content_preview": chunk_ref.content[:300],
            })
        except Exception as e:
            logger.warning("⚠️  Neo4j upsertChunk 失败 (chunk=%d): %s", chunk_ref.id, e)

    async def _upsert_entity(self, ent: Entity, file_id: str = "") -> None:
        """MERGE 实体节点 + MENTIONS 边（幂等）。

        使用确定性 entity_id = hashstr(user_id:normalized_name:label)，
        确保同一用户的同名同类型实体跨 chunk MERGE 到同一节点。
        """
        normalized = normalize_entity_name(ent.name)
        label = str(ent.type)
        entity_id = compute_entity_id(self._user_id, normalized, label)
        query = cypher_merge_entity_mention(self._user_label)
        try:
            await self._run_cypher(query, {
                "entity_id": entity_id,
                "name": ent.name,
                "type": str(ent.type),
                "label": label,
                "attributes": json.dumps(ent.attributes, ensure_ascii=False) if ent.attributes else "[]",
                "doc_hash": ent.doc_hash,
                "chunk_id": ent.chunk_id,
                "file_id": file_id,
            })
        except Exception as e:
            logger.warning("⚠️  Neo4j upsertEntity 失败 (%s): %s", ent.name, e)

    async def _upsert_relation(self, rel: Relation, file_id: str = "") -> None:
        """MERGE 关系边（幂等）。

        使用确定性 triple_id = hashstr(user_id:source:source_label:relation:target:target_label)，
        确保同一用户的三元组跨 chunk MERGE 到同一边。
        """
        source_normalized = normalize_entity_name(rel.from_name)
        source_label = str(rel.rel_type)  # 简化：用 rel_type 作为 source_label 占位
        target_normalized = normalize_entity_name(rel.to_name)
        target_label = str(rel.rel_type)

        source_id = compute_entity_id(self._user_id, source_normalized, source_label)
        target_id = compute_entity_id(self._user_id, target_normalized, target_label)
        triple_id = compute_triple_id(
            self._user_id, source_normalized, source_label,
            rel.rel_type, target_normalized, target_label,
        )

        query = cypher_merge_relation(self._user_label)
        try:
            await self._run_cypher(query, {
                "source_id": source_id,
                "target_id": target_id,
                "triple_id": triple_id,
                "relation_type": rel.rel_type,
                "text": f"{rel.from_name} {rel.rel_type} {rel.to_name}",
                "chunk_id": rel.chunk_id,
                "doc_hash": rel.doc_hash,
                "file_id": file_id,
                "pg_id": rel.pg_id,
            })
        except Exception as e:
            logger.warning(
                "⚠️  Neo4j upsertRelation 失败 (%s→%s): %s",
                rel.from_name, rel.to_name, e,
            )

    # ─────────────────────────────── 文档删除 ──────────────────────────────

    async def delete_document(self, doc_hash: str) -> None:
        """删除与 doc_hash 关联的所有图谱数据 + Milvus 向量清理。

        1. 删 RELATION 边
        2. 删 MENTIONS 边
        3. 清理 orphan Entity 节点
        4. 删 Chunk 节点
        5. 清理 MilvusGraphVectorStore 中的 entity/triple 向量
        """
        if not self.available():
            # Neo4j 不可用时仍尝试从 PG ent_ids 清理 Milvus
            await self._delete_milvus_vectors(doc_hash)
            return

        # Neo4j 清理
        try:
            query = cypher_delete_document(self._user_label)
            await self._run_cypher(query, {"doc_hash": doc_hash})
        except Exception as e:
            logger.warning("⚠️  Neo4j 删除文档图谱数据失败: %s", e)

        # Milvus 向量清理
        await self._delete_milvus_vectors(doc_hash)

    async def _delete_milvus_vectors(self, doc_hash: str) -> None:
        """从 PG rag_chunks.ent_ids 读取 entity_id 列表 → 清理 Milvus 向量。

        Neo4j 不可用时也能从 PG 清理（降级场景）。
        """
        try:
            from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore
            if not MilvusGraphVectorStore.available():
                return
        except ImportError:
            return

        # 1. 从 Neo4j 查 entity_ids + triple_ids（如果 Neo4j 可用）
        entity_ids: list[str] = []
        triple_ids: list[str] = []

        if self.available():
            try:
                ent_query = cypher_query_entity_ids_by_doc_hash(self._user_label)
                ent_records = await self._run_cypher(ent_query, {"doc_hash": doc_hash})
                entity_ids = [r["entity_id"] for r in ent_records if r.get("entity_id")]

                tri_query = cypher_query_triple_ids_by_doc_hash(self._user_label)
                tri_records = await self._run_cypher(tri_query, {"doc_hash": doc_hash})
                triple_ids = [r["triple_id"] for r in tri_records if r.get("triple_id")]
            except Exception as e:
                logger.warning("⚠️  Neo4j 查询 entity/triple IDs 失败: %s", e)

        # 2. 如果 Neo4j 不可用或查不到，从 PG rag_chunks.ent_ids 读取
        if not entity_ids:
            entity_ids = await self._read_ent_ids_from_pg(doc_hash)

        # 3. 执行 Milvus 删除
        if entity_ids:
            try:
                await MilvusGraphVectorStore.delete_by_ids(entity_ids, triple_ids)
                logger.debug(
                    "Milvus 向量清理完成：doc_hash=%s, entities=%d, triples=%d",
                    doc_hash, len(entity_ids), len(triple_ids),
                )
            except Exception as e:
                logger.warning("⚠️  Milvus 向量清理失败: %s", e)

    async def _read_ent_ids_from_pg(self, doc_hash: str) -> list[str]:
        """从 PG rag_chunks.ent_ids 读取 entity_id 列表（Neo4j 不可用时的降级路径）。"""
        try:
            from sqlalchemy import select

            from app.db.engine import get_remote_db
            from app.db.models import RagChunk

            async with get_remote_db() as session:
                result = await session.execute(
                    select(RagChunk.ent_ids).where(RagChunk.doc_hash == doc_hash)
                )
                rows = result.all()
                ent_ids: list[str] = []
                for row in rows:
                    if row[0]:
                        if isinstance(row[0], str):
                            import json as _json
                            ent_ids.extend(_json.loads(row[0]))
                        elif isinstance(row[0], list):
                            ent_ids.extend(row[0])
                return ent_ids
        except Exception as e:
            logger.warning("⚠️  从 PG 读取 ent_ids 失败: %s", e)
            return []

    # ─────────────────────────────── 图检索 ────────────────────────────────

    async def search(
        self, query_text: str, top_k: int, *, expand_depth: int = 0,
    ) -> list[dict]:
        """根据查询文本抽取实体，执行 1~2 跳子图遍历，返回关联的 pg_id 列表。

        expand_depth > 0 时优先尝试 PPR 检索（GDS/APOC），失败降级为普通遍历。
        返回格式对齐 HybridStore 期望：List[dict] with pg_id, content, score, entities 键。
        """
        if not self.available():
            return []

        # 抽取查询中的实体
        extracted = self.extractor.extract(query_text)
        if not extracted.entities:
            return []

        # 构建实体名列表
        names = [e.name for e in extracted.entities]

        # PPR 优先（当 expand_depth > 0）
        if expand_depth > 0:
            ppr_results = await self.search_with_ppr(names, top_k, expand_depth)
            if ppr_results:
                return ppr_results
            # PPR 不可用时降级为普通子图遍历

        # 每跳权重递减（直接命中 > 1跳 > 2跳）
        hops = self.max_hops
        if hops <= 0:
            hops = 2
        if hops > 3:  # 防御性 clamp，避免配置错误拖死 Neo4j
            hops = 3

        query = cypher_search_subgraph(self._user_label)
        try:
            records = await self._run_cypher(query, {
                "names": names,
                "hops": int(hops),
                "limit": int(top_k * 3),
            })
        except Exception:
            # APOC 不可用时降级为直接节点匹配
            return await self._search_direct(names, top_k)

        # 收集结果
        raw: list[dict] = []
        for rec in records or []:
            pgid = _to_int64(rec.get("pgid"))
            if pgid == 0:
                continue
            raw.append({
                "pg_id": pgid,
                "seeds": _to_string_list(rec.get("seeds")),
                "degree": _to_int64(rec.get("deg")),
            })

        # 计算分数：命中种子越多 + 图中心度越高 → 分越高
        seen: set = set()
        results: list[dict] = []
        for r in raw:
            pg_id = r["pg_id"]
            if pg_id in seen:
                continue
            seen.add(pg_id)
            score = float(len(r["seeds"])) * 0.6 + float(r["degree"]) * 0.01
            results.append({
                "pg_id": pg_id,
                "content": "",
                "score": score,
                "entities": r["seeds"],
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        if len(results) > top_k:
            results = results[:top_k]
        return results

    async def search_with_ppr(
        self,
        query_entities: list[str],
        top_k: int,
        expand_depth: int,
        *,
        seed_weights: list[float] | None = None,
        entity_types: list[str] | None = None,
        weight_by_type: bool = True,
    ) -> list[dict]:
        """PPR (Personalized PageRank) 检索：从查询实体出发，通过 PageRank 打分返回关联 pg_id。

        优先尝试 Neo4j GDS 库 gds.pageRank.stream；GDS 不可用降级为
        APOC apoc.path.subgraphNodes + 手动 scoring（基于命中种子数和节点度）。
        返回格式对齐 HybridStore 期望：List[dict] with pg_id, content, score, entities 键。
        """
        if not self.available() or not query_entities:
            return []

        depth = max(1, min(expand_depth, 3))

        # ── 方案 1: GDS pageRank.stream ──
        try:
            results = await self._ppr_via_gds(
                query_entities, top_k, depth,
                seed_weights=seed_weights,
            )
            if results is not None:
                return results
        except Exception as e:
            logger.debug("GDS pageRank not available, falling back to APOC: %s", e)

        # ── 方案 2: APOC subgraphNodes + 手动 scoring ──
        try:
            return await self._ppr_via_apoc(
                query_entities, top_k, depth,
                seed_weights=seed_weights,
                entity_types=entity_types,
                weight_by_type=weight_by_type,
            )
        except Exception as e:
            logger.warning("PPR retrieval failed (APOC fallback also failed): %s", e)
            return []

    async def _ppr_via_gds(
        self,
        query_entities: list[str],
        top_k: int,
        depth: int,
        *,
        seed_weights: list[float] | None = None,
    ) -> list[dict] | None:
        """尝试用 Neo4j GDS pageRank.stream 执行 PPR。GDS 不可用返回 None。"""
        has_weights = seed_weights is not None and len(seed_weights) == len(query_entities)

        if has_weights:
            gds_query = cypher_search_ppr_gds(self._user_label, has_weights=True)
            try:
                records = await self._run_cypher(gds_query, {
                    "names": query_entities,
                    "maxIter": 20,
                    "seedWeights": list(seed_weights),
                    "limit": int(top_k * 2),
                })
            except Exception as e:
                err_msg = str(e).lower()
                if "sourcenodeweights" in err_msg or "weight" in err_msg:
                    logger.debug("GDS does not support sourceNodeWeights, retrying with sourceNodes")
                    return await self._ppr_via_gds(query_entities, top_k, depth, seed_weights=None)
                if "gds" in err_msg or "no such procedure" in err_msg or "unknown" in err_msg:
                    return None
                logger.debug("GDS pageRank query error: %s", e)
                return None
        else:
            gds_query = cypher_search_ppr_gds(self._user_label, has_weights=False)
            try:
                records = await self._run_cypher(gds_query, {
                    "names": query_entities,
                    "maxIter": 20,
                    "limit": int(top_k * 2),
                })
            except Exception as e:
                err_msg = str(e).lower()
                if "gds" in err_msg or "no such procedure" in err_msg or "unknown" in err_msg:
                    return None
                logger.debug("GDS pageRank query error: %s", e)
                return None

        if not records:
            return []

        seen: set = set()
        results: list[dict] = []
        for rec in records:
            pg_id = _to_int64(rec.get("pgid"))
            if pg_id == 0 or pg_id in seen:
                continue
            seen.add(pg_id)
            results.append({
                "pg_id": pg_id,
                "content": "",
                "score": float(rec.get("score", 0.0)),
                "entities": _to_string_list(rec.get("seeds")),
            })
        if len(results) > top_k:
            results = results[:top_k]
        return results

    async def _ppr_via_apoc(
        self,
        query_entities: list[str],
        top_k: int,
        depth: int,
        *,
        seed_weights: list[float] | None = None,
        entity_types: list[str] | None = None,
        weight_by_type: bool = True,
    ) -> list[dict]:
        """APOC subgraphNodes 遍历 + 手动 PPR-style scoring（降级方案）。"""
        apoc_query = cypher_search_ppr_apoc(self._user_label)
        records = await self._run_cypher(apoc_query, {
            "names": query_entities,
            "depth": int(depth),
            "limit": int(top_k * 3),
        })

        if not records:
            return []

        has_weights = seed_weights is not None and len(seed_weights) == len(query_entities)
        has_types = entity_types is not None and len(entity_types) == len(query_entities)

        seed_weight_map: dict[str, float] = {}
        if has_weights and has_types:
            for i, name in enumerate(query_entities):
                tw = get_entity_type_weight(entity_types[i]) if weight_by_type else 1.0
                seed_weight_map[name] = seed_weights[i] * tw
        elif has_weights:
            for i, name in enumerate(query_entities):
                seed_weight_map[name] = seed_weights[i]
        elif has_types and weight_by_type:
            for i, name in enumerate(query_entities):
                seed_weight_map[name] = get_entity_type_weight(entity_types[i])

        seen: set = set()
        results: list[dict] = []
        for rec in records:
            pg_id = _to_int64(rec.get("pgid"))
            if pg_id == 0 or pg_id in seen:
                continue
            seen.add(pg_id)
            seeds = _to_string_list(rec.get("seeds"))
            degree = _to_int64(rec.get("degree"))
            if seed_weight_map:
                type_weighted = sum(seed_weight_map.get(s, 1.0) for s in seeds)
                score = type_weighted * 0.6 + float(degree) * 0.01
            else:
                score = float(len(seeds)) * 0.6 + float(degree) * 0.01
            results.append({
                "pg_id": pg_id,
                "content": "",
                "score": score,
                "entities": seeds,
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        if len(results) > top_k:
            results = results[:top_k]
        return results

    async def _search_direct(self, names: list[str], top_k: int) -> list[dict]:
        """APOC 不可用时的降级版本：直接匹配实体所在 chunk。"""
        try:
            query = cypher_search_direct(self._user_label)
            records = await self._run_cypher(query, {
                "names": names,
                "limit": int(top_k),
            })
        except Exception:
            return []

        seen: set = set()
        results: list[dict] = []
        for rec in records or []:
            pg_id = _to_int64(rec.get("pgid"))
            name = _to_string(rec.get("name"))
            if pg_id == 0 or pg_id in seen:
                continue
            seen.add(pg_id)
            results.append({
                "pg_id": pg_id,
                "content": "",
                "score": 1.0,
                "entities": [name],
            })
        return results

    # ─────────────────────────────── 图谱可视化 ─────────────────────────────

    async def get_stats(self) -> dict:
        """返回图谱统计：节点/边总数 + 实体类型分布。Neo4j 不可用时返回空统计。"""
        if not self.available():
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}

        total_nodes = 0
        total_edges = 0
        entity_types: list[dict] = []

        try:
            nodes_records = await self._run_cypher(
                cypher_graph_stats_nodes(self._user_label), {},
            )
            if nodes_records:
                total_nodes = _to_int64(nodes_records[0].get("count"))

            edges_records = await self._run_cypher(
                cypher_graph_stats_edges(self._user_label), {},
            )
            if edges_records:
                total_edges = _to_int64(edges_records[0].get("count"))

            type_records = await self._run_cypher(
                cypher_graph_stats_entity_types(self._user_label), {},
            )
            for rec in type_records or []:
                entity_types.append({
                    "type": _to_string(rec.get("entity_label")) or "Unknown",
                    "count": _to_int64(rec.get("count")),
                })
        except Exception as e:
            logger.warning("KGStore get_stats failed: %s", e)
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "entity_types": entity_types,
        }

    async def query_subgraph(
        self,
        keyword: str,
        max_depth: int,
        max_nodes: int,
        exclude_chunk: bool,
    ) -> dict:
        """子图查询：关键词过滤 + N 跳遍历，返回归一化的 {nodes, edges}。"""
        if not self.available():
            return {"nodes": [], "edges": []}

        kw = keyword.strip() or "*"
        path_limit = max_nodes * 4
        query = cypher_graph_subgraph(self._user_label, max_depth, exclude_chunk)
        try:
            records = await self._run_cypher(query, {
                "keyword": kw,
                "limit": max_nodes,
                "path_limit": path_limit,
            })
        except Exception as e:
            logger.warning("KGStore query_subgraph failed: %s", e)
            return {"nodes": [], "edges": []}

        if not records:
            return {"nodes": [], "edges": []}

        raw_nodes = []
        raw_edges = []
        for rec in records:
            raw_nodes.extend(rec.get("nodes") or [])
            raw_edges.extend(rec.get("edges") or [])

        # Deduplicate nodes by element_id
        node_map: dict[str, dict] = {}
        for raw_node in raw_nodes:
            normalized = self._normalize_node(raw_node)
            if normalized["id"] and normalized["id"] not in node_map:
                node_map[normalized["id"]] = normalized

        # Limit nodes to max_nodes
        nodes = list(node_map.values())[:max_nodes]
        node_ids = {n["id"] for n in nodes}

        # Filter edges: both source and target must be in the node set
        edge_map: dict[str, dict] = {}
        for raw_edge in raw_edges:
            normalized = self._normalize_edge(raw_edge)
            if (
                normalized["id"]
                and normalized["source_id"] in node_ids
                and normalized["target_id"] in node_ids
                and normalized["id"] not in edge_map
            ):
                edge_map[normalized["id"]] = normalized

        edges = list(edge_map.values())[:max_nodes * 2]
        return {"nodes": nodes, "edges": edges}

    async def get_labels(self) -> list[str]:
        """返回用户图分区中所有去重的实体标签。Neo4j 不可用时返回空列表。"""
        if not self.available():
            return []

        try:
            records = await self._run_cypher(
                cypher_graph_labels(self._user_label), {},
            )
            return [_to_string(rec.get("label")) for rec in records or [] if rec.get("label")]
        except Exception as e:
            logger.warning("KGStore get_labels failed: %s", e)
            return []

    def _normalize_node(self, raw_node) -> dict:
        """将 Neo4j 原始节点归一化为 API 响应格式。"""
        labels = list(getattr(raw_node, "labels", []) or [])
        # Filter out UserKG:{hash} labels (u_ prefixed)
        filtered_labels = [lb for lb in labels if not lb.startswith("u_")]

        # Determine type
        is_chunk = "Chunk" in labels
        node_type = "Chunk" if is_chunk else "Entity"

        # Extract properties
        props = dict(getattr(raw_node, "_properties", {}) or {})

        # Determine name
        name = (
            props.get("name")
            or props.get("content_preview")
            or props.get("chunk_id")
            or props.get("entity_id")
            or ""
        )

        # If entity and has a label property, use it for type
        if not is_chunk and props.get("label"):
            node_type = props.get("label")

        return {
            "id": str(getattr(raw_node, "element_id", "") or ""),
            "name": str(name),
            "type": str(node_type),
            "labels": filtered_labels,
            "properties": props,
        }

    def _normalize_edge(self, raw_edge) -> dict:
        """将 Neo4j 原始边归一化为 API 响应格式。"""
        props = dict(getattr(raw_edge, "_properties", {}) or {})
        edge_type = str(getattr(raw_edge, "type", "") or "")
        start_node = getattr(raw_edge, "start_node", None)
        end_node = getattr(raw_edge, "end_node", None)
        return {
            "id": str(getattr(raw_edge, "element_id", "") or ""),
            "source_id": str(getattr(start_node, "element_id", "") or "") if start_node else "",
            "target_id": str(getattr(end_node, "element_id", "") or "") if end_node else "",
            "type": edge_type,
            "properties": props,
        }


# ─────────────────────────────── 内部工具 ──────────────────────────────────


def _to_int64(v) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return 0


def _to_string(v) -> str:
    if isinstance(v, str):
        return v
    return ""


def _to_string_list(v) -> list[str]:
    if isinstance(v, list):
        return [a for a in v if isinstance(a, str)]
    return []
