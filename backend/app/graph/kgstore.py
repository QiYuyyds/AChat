# kgstore — Neo4j 知识图谱存储 + 多跳遍历检索（async 适配版）
#
# 从 AGI-memory internal/graph/kgstore.py 移植，适配点：
# - Neo4j 驱动从同步 Neo4jClient 改为 neo4j.AsyncDriver
# - 所有 Cypher 操作改为 async（_run_cypher 辅助方法）
# - index_document / delete_document / search 均为 async
# - search 返回 List[dict]（含 pg_id 键）而非 List[GraphSearchResult]
# - 与 GraphMemory 共享同一 AsyncDriver 实例
import logging
from typing import List, Optional

from app.config import Settings

from .extractor import Extractor
from .types import (
    ChunkRef,
    Entity,
    GraphSearchResult,
    Relation,
)

logger = logging.getLogger(__name__)


class KGStore:
    """在 Neo4j AsyncDriver 之上封装 RAG 专用的图操作：
    - index_document：文档摄入时写入实体节点和关系边
    - delete_document：删除文档及其关联的孤立节点
    - search：根据查询实体做 1~2 跳子图扩展，返回关联的 pg_id 列表
    所有操作在 Neo4j 不可用时均优雅降级（返回空结果，不阻塞主流程）。
    """

    def __init__(
        self,
        settings: Settings,
        driver=None,  # neo4j.AsyncDriver | None
        extractor: Optional[Extractor] = None,
    ):
        self._driver = driver
        self.max_hops = settings.kg_max_hops
        self.kg_weight = settings.kg_weight
        self.extractor = extractor or Extractor(None)

    # ── 基础能力 ───────────────────────────────────────────────────────────

    def available(self) -> bool:
        """图存储是否可用"""
        return self._driver is not None

    # ── Cypher 辅助 ─────────────────────────────────────────────────────────

    async def _run_cypher(self, query: str, params: dict) -> list:
        """执行 Cypher 查询并返回记录列表（list[dict]）。"""
        async with self._driver.session() as session:
            result = await session.run(query, parameters=params)
            return await result.data()

    # ─────────────────────────────── 文档摄入 ──────────────────────────────

    async def index_document(self, doc_hash: str, chunks: List[ChunkRef]) -> None:
        """为一批 chunks 抽取实体关系并写入图（不阻塞主 Ingest 流程）。"""
        if not self.available():
            return
        for c in chunks:
            result = self.extractor.extract(c.content)
            if not result.entities:
                continue
            # 写入实体节点
            for ent in result.entities:
                ent.doc_hash = doc_hash
                ent.chunk_id = c.id
                ent.pg_id = c.pg_id
                await self._upsert_entity(ent)
            # 写入关系边
            for rel in result.relations:
                rel.doc_hash = doc_hash
                rel.chunk_id = c.id
                rel.pg_id = c.pg_id
                await self._upsert_relation(rel)
        logger.info("🕸️  知识图谱索引完成：docHash=%s，chunks=%d", doc_hash, len(chunks))

    async def _upsert_entity(self, ent: Entity) -> None:
        """MERGE 实体节点（幂等）"""
        query = (
            "MERGE (e:Entity {name: $name}) "
            "SET e.type = $type, e.doc_hash = $doc_hash, e.chunk_id = $chunk_id, e.pg_id = $pg_id"
        )
        try:
            await self._run_cypher(query, {
                "name": ent.name,
                "type": str(ent.type),
                "doc_hash": ent.doc_hash,
                "chunk_id": ent.chunk_id,
                "pg_id": ent.pg_id,
            })
        except Exception as e:
            logger.warning("⚠️  Neo4j upsertEntity 失败 (%s): %s", ent.name, e)

    async def _upsert_relation(self, rel: Relation) -> None:
        """MERGE 关系边（幂等）。
        动态关系类型无法用参数传递，必须拼入查询字符串；安全性由 extractor 已过滤非法类型保证。
        """
        query = (
            "MERGE (a:Entity {name: $from}) "
            "MERGE (b:Entity {name: $to}) "
            f"MERGE (a)-[r:{rel.rel_type} {{doc_hash: $doc_hash}}]->(b) "
            "SET r.chunk_id = $chunk_id, r.pg_id = $pg_id"
        )
        try:
            await self._run_cypher(query, {
                "from": rel.from_name,
                "to": rel.to_name,
                "doc_hash": rel.doc_hash,
                "chunk_id": rel.chunk_id,
                "pg_id": rel.pg_id,
            })
        except Exception as e:
            logger.warning("⚠️  Neo4j upsertRelation 失败 (%s→%s): %s", rel.from_name, rel.to_name, e)

    # ─────────────────────────────── 文档删除 ──────────────────────────────

    async def delete_document(self, doc_hash: str) -> None:
        """删除与 doc_hash 关联的所有关系，并清理孤立实体节点"""
        if not self.available():
            return
        try:
            await self._run_cypher(
                "MATCH ()-[r {doc_hash: $doc_hash}]-() DELETE r",
                {"doc_hash": doc_hash},
            )
        except Exception as e:
            logger.warning("⚠️  Neo4j 删除文档关系失败: %s", e)
        try:
            await self._run_cypher(
                "MATCH (e:Entity) WHERE NOT (e)--() AND e.doc_hash = $doc_hash DELETE e",
                {"doc_hash": doc_hash},
            )
        except Exception as e:
            logger.warning("⚠️  Neo4j 清理孤立节点失败: %s", e)

    # ─────────────────────────────── 图检索 ────────────────────────────────

    async def search(self, query_text: str, top_k: int) -> List[dict]:
        """根据查询文本抽取实体，执行 1~2 跳子图遍历，返回关联的 pg_id 列表。

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

        # 每跳权重递减（直接命中 > 1跳 > 2跳）
        hops = self.max_hops
        if hops <= 0:
            hops = 2
        if hops > 3:  # 防御性 clamp，避免配置错误拖死 Neo4j
            hops = 3

        query = """
	MATCH (e:Entity) WHERE e.name IN $names
	CALL apoc.path.subgraphNodes(e, {
	  maxLevel: $hops,
	  relationshipFilter: "RELATES_TO|PART_OF|CAUSES|DESCRIBES|MENTIONS|WORKS_FOR|LOCATED_IN"
	})
	YIELD node AS neighbor
	WHERE neighbor:Entity AND neighbor.chunk_id IS NOT NULL
	WITH e.name AS seed, neighbor.name AS nb, neighbor.chunk_id AS cid,
	     COALESCE(neighbor.pg_id, 0) AS pgid,
	     toInteger(apoc.node.degree(neighbor)) AS degree
	RETURN cid, pgid, collect(DISTINCT seed) AS seeds, collect(DISTINCT nb) AS neighbors, max(degree) AS deg
	ORDER BY size(seeds) DESC, deg DESC
	LIMIT $limit"""

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
        raw: List[dict] = []
        for rec in records or []:
            cid = _to_int(rec.get("cid"))
            if cid < 0:
                continue
            raw.append({
                "chunk_id": cid,
                "pg_id": _to_int64(rec.get("pgid")),
                "seeds": _to_string_list(rec.get("seeds")),
                "neighbors": _to_string_list(rec.get("neighbors")),
                "degree": _to_int64(rec.get("deg")),
            })

        # 计算分数：命中种子越多 + 图中心度越高 → 分越高
        seen: set = set()
        results: List[dict] = []
        for r in raw:
            pg_id = r["pg_id"]
            if pg_id == 0 or pg_id in seen:  # 没有 pg_id 的节点（旧数据）跳过
                continue
            seen.add(pg_id)
            score = float(len(r["seeds"])) * 0.6 + float(r["degree"]) * 0.01
            score *= self.kg_weight
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

    async def _search_direct(self, names: List[str], top_k: int) -> List[dict]:
        """APOC 不可用时的降级版本：直接匹配实体所在 chunk"""
        try:
            records = await self._run_cypher(
                "MATCH (e:Entity) WHERE e.name IN $names AND e.chunk_id IS NOT NULL "
                "RETURN e.chunk_id AS cid, COALESCE(e.pg_id, 0) AS pgid, e.name AS name "
                "ORDER BY cid LIMIT $limit",
                {"names": names, "limit": int(top_k)},
            )
        except Exception:
            return []

        seen: set = set()
        results: List[dict] = []
        for rec in records or []:
            cid = _to_int(rec.get("cid"))
            pg_id = _to_int64(rec.get("pgid"))
            name = _to_string(rec.get("name"))
            if pg_id == 0 or pg_id in seen:
                continue
            seen.add(pg_id)
            results.append({
                "pg_id": pg_id,
                "content": "",
                "score": self.kg_weight,
                "entities": [name],
            })
        return results


# ─────────────────────────────── 内部工具 ──────────────────────────────────


def _to_int(v) -> int:
    if isinstance(v, bool):
        return -1
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return -1


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


def _to_string_list(v) -> List[str]:
    if isinstance(v, list):
        return [a for a in v if isinstance(a, str)]
    return []
