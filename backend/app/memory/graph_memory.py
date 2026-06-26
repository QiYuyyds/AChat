"""GraphMemory — long-term memory + Neo4j knowledge graph bidirectional sync.

Ported from AGI-memory ``internal/memory/graph_memory.py``.
改造: sync neo4j driver → AsyncDriver; threading.Thread → asyncio.create_task.
失败降级：Neo4j 不可用时所有方法都是 no-op，不抛异常。

Node type: (:Memory {mem_id, content, importance})
Edge types:
  FOLLOWS    — temporal adjacency
  SIMILAR_TO — semantic similarity above threshold
  CAUSES     — causal inference (LLM-extracted, optional)
  BELONGS_TO — topic membership
"""

import asyncio
import logging
import math
from typing import Any, Iterable, List, Optional

from app.config import Settings

logger = logging.getLogger(__name__)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _async_safe(name: str, coro_fn) -> None:
    """Run an async coroutine as a fire-and-forget task with error logging.

    Analogous to AGI-memory's _go_safe but uses asyncio.create_task instead
    of threading.Thread.  All exceptions are swallowed and logged.
    """
    async def _runner() -> None:
        try:
            await coro_fn()
        except Exception as e:
            logger.warning("async_safe %s error: %s", name, e)

    asyncio.create_task(_runner())


class GraphMemory:
    """Long-term memory graph enhancement layer.

    Uses Neo4j AsyncDriver for all Cypher operations. When Neo4j is
    unavailable every method silently returns — callers need not
    handle degradation.
    """

    def __init__(
        self,
        settings: Settings,
        driver=None,  # neo4j.AsyncDriver | None
        llm: Optional[Any] = None,
        sim_threshold: float = 0.7,
        ltm: Optional[Any] = None,
    ):
        self.settings = settings
        self._driver = driver
        self.llm = llm
        self.sim_thresh = sim_threshold if sim_threshold > 0 else 0.7
        self.prev_id: int = -1
        self.ltm = ltm

    def set_ltm(self, ltm: Optional[Any]) -> None:
        self.ltm = ltm

    # ─── Availability ─────────────────────────────────────────────────────

    def _available(self) -> bool:
        return self._driver is not None

    def _mem_id(self, item) -> int:
        mid = getattr(item, "id", None)
        if mid is None:
            return hash(item.content) & 0x7FFFFFFF
        return int(mid)

    # ─── Atomic graph operations (all async) ─────────────────────────────

    async def _run_cypher(self, query: str, params: dict) -> list:
        """Execute a Cypher query via async session. Returns list of record dicts."""
        async with self._driver.session() as session:
            result = await session.run(query, parameters=params)
            records = await result.data()
            return records

    async def _upsert_memory_node(self, mem_id: int, content: str, importance: float) -> None:
        if not self._available():
            return
        try:
            await self._run_cypher(
                """MERGE (m:Memory {mem_id: $id})
                   SET m.content = $content, m.importance = $importance""",
                {"id": int(mem_id), "content": content, "importance": float(importance)},
            )
        except Exception as e:
            logger.warning("Neo4j upsertMemoryNode failed (id=%s): %s", mem_id, e)

    async def _add_memory_edge(self, from_id: int, to_id: int, edge_type: str, weight: float) -> None:
        if not self._available():
            return
        if edge_type not in ("FOLLOWS", "SIMILAR_TO", "CAUSES", "BELONGS_TO"):
            logger.warning("Illegal edge type: %s", edge_type)
            return
        query = (
            "MATCH (a:Memory {mem_id: $from}), (b:Memory {mem_id: $to}) "
            "MERGE (a)-[r:" + edge_type + "]->(b) "
            "SET r.weight = $weight"
        )
        try:
            await self._run_cypher(
                query,
                {"from": int(from_id), "to": int(to_id), "weight": float(weight)},
            )
        except Exception as e:
            logger.warning("Neo4j addMemoryEdge failed (%s->%s): %s", from_id, to_id, e)

    async def _expand_memory_neighbors(self, seed_ids: List[int], hops: int) -> List[int]:
        if not self._available() or not seed_ids:
            return []
        hop_str = "1" if hops <= 1 else "1.." + str(hops)
        query = (
            "MATCH (m:Memory) WHERE m.mem_id IN $ids "
            "MATCH (m)-[:FOLLOWS|SIMILAR_TO|CAUSES|BELONGS_TO*" + hop_str + "]-(n:Memory) "
            "WHERE NOT n.mem_id IN $ids "
            "RETURN DISTINCT n.mem_id AS id"
        )
        try:
            records = await self._run_cypher(query, {"ids": [int(i) for i in seed_ids]})
        except Exception as e:
            logger.warning("Neo4j expandMemoryNeighbors failed: %s", e)
            return []
        result: List[int] = []
        for rec in records:
            v = rec.get("id")
            if v is not None:
                try:
                    result.append(int(v))
                except (TypeError, ValueError):
                    continue
        return result

    async def _delete_memory_node(self, mem_id: int) -> None:
        if not self._available():
            return
        try:
            await self._run_cypher(
                "MATCH (m:Memory {mem_id: $id}) DETACH DELETE m",
                {"id": int(mem_id)},
            )
        except Exception as e:
            logger.warning("Neo4j deleteMemoryNode failed (id=%s): %s", mem_id, e)

    async def _get_high_centrality_ids(self, candidates: List[int], threshold: int) -> List[int]:
        if not self._available() or not candidates:
            return []
        query = (
            "MATCH (m:Memory) WHERE m.mem_id IN $ids "
            "WITH m, size([(m)<-[]-() | 1]) AS indegree "
            "WHERE indegree >= $threshold "
            "RETURN m.mem_id AS id"
        )
        try:
            records = await self._run_cypher(
                query,
                {"ids": [int(i) for i in candidates], "threshold": int(threshold)},
            )
        except Exception as e:
            logger.warning("Neo4j getHighCentrality failed: %s", e)
            return []
        result: List[int] = []
        for rec in records:
            v = rec.get("id")
            if v is not None:
                try:
                    result.append(int(v))
                except (TypeError, ValueError):
                    continue
        return result

    # ─── Public API (core 4 methods) ──────────────────────────────────────

    async def add_to_graph(self, item, neighbors: Optional[Iterable] = None) -> int:
        """Sync a memory item into the graph asynchronously.

        Main path only computes mem_id and updates prev_id (so caller can
        immediately see temporal ordering). Node upsert / FOLLOWS / SIMILAR_TO
        edge writes are dispatched as a fire-and-forget asyncio task.

        Returns mem_id; returns -1 when unavailable.
        """
        if not self._available():
            return -1
        mem_id = self._mem_id(item)
        content = getattr(item, "content", "")
        importance = float(getattr(item, "importance", 0.5) or 0.5)
        prev_id = self.prev_id

        neighbor_pairs: List[tuple] = []
        if neighbors:
            new_emb = list(getattr(item, "embedding", None) or [])
            if new_emb:
                for old in neighbors:
                    if old is item:
                        continue
                    old_id = self._mem_id(old)
                    if old_id == mem_id:
                        continue
                    old_emb = list(getattr(old, "embedding", None) or [])
                    if not old_emb:
                        continue
                    neighbor_pairs.append((old_id, old_emb, new_emb))

        async def _write() -> None:
            await self._upsert_memory_node(mem_id, content, importance)
            if prev_id >= 0 and prev_id != mem_id:
                await self._add_memory_edge(prev_id, mem_id, "FOLLOWS", 1.0)
            for old_id, old_emb, new_emb in neighbor_pairs:
                sim = _cosine(old_emb, new_emb)
                if sim >= self.sim_thresh:
                    await self._add_memory_edge(old_id, mem_id, "SIMILAR_TO", sim)

        await _async_safe("graphmem.add-to-graph", _write)
        self.prev_id = mem_id
        return mem_id

    async def find_related(self, item_id: int, max_hops: Optional[int] = None) -> List[int]:
        """Expand from item_id along the graph for max_hops hops."""
        if not self._available():
            return []
        hops = max_hops if (max_hops is not None and max_hops > 0) else self.settings.kg_max_hops
        return await self._expand_memory_neighbors([int(item_id)], int(hops))

    async def delete_from_graph(self, item_id: int) -> None:
        if not self._available():
            return
        mid = int(item_id)
        if self.prev_id == mid:
            self.prev_id = -1
        await self._delete_memory_node(mid)

    async def bulk_index(self, items: Iterable) -> int:
        """Bulk index items into the graph (restore from LTM at startup)."""
        if not self._available():
            return 0
        count = 0
        prev_local = self.prev_id
        items_list = list(items)
        for item in items_list:
            mem_id = self._mem_id(item)
            await self._upsert_memory_node(
                mem_id,
                getattr(item, "content", ""),
                float(getattr(item, "importance", 0.5) or 0.5),
            )
            if prev_local >= 0 and prev_local != mem_id:
                await self._add_memory_edge(prev_local, mem_id, "FOLLOWS", 1.0)
            prev_local = mem_id
            count += 1
        self.prev_id = prev_local
        return count

    # ─── Centrality protection (for consolidate) ──────────────────────────

    async def filter_protected(self, candidate_ids: List[int], indegree_threshold: int = 3) -> List[int]:
        """Return candidate_ids with in-degree >= threshold (should be exempt from deletion)."""
        return await self._get_high_centrality_ids(candidate_ids, indegree_threshold)

    # ─── LTM proxy methods ───────────────────────────────────────────────

    def sync_prev_id(self) -> None:
        if self.ltm is None:
            return
        try:
            last = self.ltm.last_id()
        except Exception as e:
            logger.warning("sync_prev_id ltm.last_id failed: %s", e)
            return
        self.prev_id = int(last)

    def set_consolidation_config(self, cfg) -> None:
        if self.ltm is None:
            return
        try:
            self.ltm.set_consolidation_config(cfg)
        except Exception as e:
            logger.warning("set_consolidation_config proxy failed: %s", e)

    def need_consolidation(self) -> bool:
        if self.ltm is None:
            return False
        try:
            return bool(self.ltm.need_consolidation())
        except Exception as e:
            logger.warning("need_consolidation proxy failed: %s", e)
            return False

    async def update_node(self, item) -> None:
        """Sync updated memory content/importance to Neo4j node."""
        if not self._available():
            return
        await self._upsert_memory_node(
            self._mem_id(item),
            getattr(item, "content", ""),
            float(getattr(item, "importance", 0.5) or 0.5),
        )

    async def graph_aware_consolidate(self):
        """Graph-aware consolidation: protect high-centrality nodes + sync Neo4j deletes."""
        if self.ltm is None:
            return None
        try:
            result = await self.ltm.consolidate()
        except Exception as e:
            logger.warning("graph_aware_consolidate: ltm.consolidate failed: %s", e)
            return None
        if not self._available() or result is None:
            return result

        delete_ids = list(getattr(result, "delete_from_db", []) or [])
        if delete_ids:
            try:
                protected = set(await self._get_high_centrality_ids(delete_ids, 3) or [])
            except Exception as e:
                logger.warning("graph_aware_consolidate: centrality filter failed: %s", e)
                protected = set()
            if protected:
                filtered = [i for i in delete_ids if i not in protected]
                logger.info(
                    "Graph centrality protection: %d memories exempt from deletion (indegree>=3)",
                    len(delete_ids) - len(filtered),
                )
                result.delete_from_db = filtered
                delete_ids = filtered

        if delete_ids:
            async def _delete_all():
                for nid in delete_ids:
                    try:
                        await self._delete_memory_node(int(nid))
                    except Exception as e:
                        logger.warning("Neo4j node delete failed (id=%s): %s", nid, e)

            await _async_safe("graphmem.consolidate-delete", _delete_all)

        return result

    async def close(self) -> None:
        """Close the async Neo4j driver. Semantic alignment with Go version."""
        self.prev_id = -1
        if self._driver is not None:
            try:
                await self._driver.close()
            except Exception:
                pass
