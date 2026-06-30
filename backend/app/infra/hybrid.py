"""HybridStore — enterprise hybrid search: Milvus semantic + ES BM25 + KG + 3-way RRF fusion.

Ported from AGI-memory ``internal/rag/hybrid.py``.
Deep adaptation: PG → async session; threading → asyncio.gather; RRF unchanged.

RRF formula:
    score(d) = Σ_i  weight_i / (k + rank_i(d))

Weight distribution: semantic_weight, (1 - semantic_weight - kg_weight), kg_weight.
Any unavailable path is skipped and remaining weights renormalised.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from sqlalchemy import select, text

from app.config import Settings
from app.db.engine import get_db
from app.db.models import RagChunk
from app.graph.types import ChunkRef

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], List[float]]


@dataclass
class HybridResult:
    """Single result from hybrid search."""
    pg_id: int = 0
    content: str = ""
    score: float = 0.0
    source: str = ""  # "hybrid" | "semantic" | "keyword"
    parent: str = ""


@dataclass
class _PathHits:
    """Single-path retrieval result (rank-ordered) + success flag."""
    hits: List[dict] = field(default_factory=list)
    ok: bool = False


class HybridStore:
    """Enterprise hybrid search:
        - Milvus semantic vector search
        - Elasticsearch BM25 keyword search
        - Neo4j knowledge graph entity traversal
        - Reciprocal Rank Fusion 3-way fusion

    Search functions are injected via setters; unavailable paths return empty results.
    """

    def __init__(
        self,
        settings: Settings,
        embed_fn: Optional[EmbedFn] = None,
    ):
        self.settings = settings
        self._embed_fn = embed_fn
        self._reranker = None

        # Injected search backends (set by infrastructure factory)
        self._milvus_search_fn: Optional[Callable] = None  # (emb, k) -> List[dict]
        self._es_search_fn: Optional[Callable] = None     # (query, k) -> List[dict]
        self._kg_search_fn: Optional[Callable] = None      # (query, k) -> List[dict]

        # Milvus insert backend
        self._milvus_insert_fn: Optional[Callable] = None  # (ids, contents, embs) -> None
        self._es_index_fn: Optional[Callable] = None       # (pg_id, content, doc_hash, idx) -> None

        # KG index/delete backends
        self._kg_index_fn: Optional[Callable] = None   # (doc_hash, chunks: List[ChunkRef]) -> None
        self._kg_delete_fn: Optional[Callable] = None  # (doc_hash) -> None

    def set_embed_fn(self, fn: EmbedFn) -> None:
        self._embed_fn = fn

    def set_milvus_backend(self, search_fn: Callable, insert_fn: Optional[Callable] = None) -> None:
        self._milvus_search_fn = search_fn
        self._milvus_insert_fn = insert_fn

    def set_es_backend(self, search_fn: Callable, index_fn: Optional[Callable] = None) -> None:
        self._es_search_fn = search_fn
        self._es_index_fn = index_fn

    def set_kg_backend(self, search_fn: Callable) -> None:
        self._kg_search_fn = search_fn

    def set_kg_index_fn(self, fn: Callable) -> None:
        self._kg_index_fn = fn

    def set_kg_delete_fn(self, fn: Callable) -> None:
        self._kg_delete_fn = fn

    def set_reranker(self, reranker) -> None:
        self._reranker = reranker

    # ─── Availability ─────────────────────────────────────────────────────

    def _milvus_ok(self) -> bool:
        return self._milvus_search_fn is not None

    def _es_ok(self) -> bool:
        return self._es_search_fn is not None

    def _kg_ok(self) -> bool:
        return self._kg_search_fn is not None

    def mode(self) -> str:
        m, e = self._milvus_ok(), self._es_ok()
        if m and e:
            return "hybrid"
        if m:
            return "semantic"
        if e:
            return "keyword"
        return "unavailable"

    # ─── Indexing (async) ─────────────────────────────────────────────────

    async def index_chunks(
        self,
        doc_hash: str,
        contents: List[str],
        parents: List[str],
        embeddings: List[List[float]],
        *,
        content_hashes: Optional[List[str]] = None,
        cache_hit: Optional[List[bool]] = None,
    ) -> List[int]:
        """Persist chunks to PG + Milvus + ES. KG indexing is best-effort async.

        Args:
            content_hashes: chunk-level sha256[:16] for embedding cache reuse.
            cache_hit: True = embedding reused from cache, skip KG entity extraction.
        """
        pg_ids: List[int] = []

        for idx, content in enumerate(contents):
            embedding = embeddings[idx] if idx < len(embeddings) else []
            parent_content = parents[idx] if idx < len(parents) else ""
            ch = content_hashes[idx] if content_hashes and idx < len(content_hashes) else None
            try:
                async with get_db() as session:
                    row = RagChunk(
                        doc_hash=doc_hash,
                        chunk_idx=idx,
                        content=content,
                        parent_content=parent_content or None,
                        embedding=embedding,
                        created_at=time.time(),
                        content_hash=ch,
                    )
                    session.add(row)
                    await session.flush()
                    pg_id = row.id or 0
                    if pg_id > 0:
                        pg_ids.append(pg_id)
            except Exception as e:
                logger.warning("PG chunk save failed (idx=%d): %s", idx, e)
                continue

        # Milvus insert (fire-and-forget)
        if self._milvus_insert_fn and self._milvus_ok():
            milvus_ids, milvus_contents, milvus_embeddings = [], [], []
            dim = self.settings.rag_milvus_dim
            for i, pg_id in enumerate(pg_ids):
                emb = embeddings[i] if i < len(embeddings) else []
                if emb and (dim == 0 or len(emb) == dim):
                    milvus_ids.append(pg_id)
                    milvus_contents.append(contents[i])
                    milvus_embeddings.append(emb)
            if milvus_ids:
                try:
                    self._milvus_insert_fn(milvus_ids, milvus_contents, milvus_embeddings)
                except Exception as e:
                    logger.warning("Milvus insert failed: %s", e)

        # ES index (fire-and-forget)
        if self._es_index_fn and self._es_ok():
            for i, pg_id in enumerate(pg_ids):
                try:
                    await self._es_index_fn(pg_id, contents[i], doc_hash, i)
                except Exception as e:
                    logger.warning("ES index failed (pg_id=%s): %s", pg_id, e)

        # KG index (fire-and-forget) — skip cache-hit chunks (entity already extracted)
        if self._kg_index_fn and self._kg_ok() and pg_ids:
            chunk_refs = [
                ChunkRef(id=i, pg_id=pid, content=contents[i])
                for i, pid in enumerate(pg_ids)
                if not (cache_hit and i < len(cache_hit) and cache_hit[i])
            ]
            if chunk_refs:
                asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))

        return pg_ids

    # ─── Search (async with asyncio.gather for concurrent paths) ──────────

    async def search(self, query: str, top_k: int) -> List[HybridResult]:
        """Single-query search with auto mode detection."""
        mode = self.mode()
        if mode == "hybrid":
            return await self._search_hybrid(query, top_k)
        if mode == "semantic":
            return await self._search_semantic(query, top_k)
        if mode == "keyword":
            return await self._search_keyword(query, top_k)
        logger.warning("Search infrastructure unavailable (Milvus and ES both disconnected)")
        return []

    async def search_multi(self, queries: List[str], top_k: int) -> List[HybridResult]:
        """Multi-query search with RRF fusion across query variants."""
        queries = [q for q in (queries or []) if q]
        if not queries:
            return []
        pool = self._rerank_pool(top_k)
        if len(queries) == 1:
            results = await self.search(queries[0], pool)
            return self._finalize(queries[0], results, top_k)

        # Concurrent search for all query variants
        tasks = [self.search(q, pool) for q in queries]
        results_by_query = await asyncio.gather(*tasks, return_exceptions=True)

        k = self.settings.rag_rrf_constant_k or 60
        merged: Dict[str, dict] = {}
        for query_results in results_by_query:
            if isinstance(query_results, Exception):
                continue
            for rank, result in enumerate(query_results):
                key = f"id:{result.pg_id}" if result.pg_id else f"c:{result.content[:100]}"
                score = 1.0 / float(k + rank + 1)
                if key in merged:
                    merged[key]["score"] += score
                    if result.score > merged[key]["result"].score:
                        merged[key]["result"] = result
                else:
                    merged[key] = {"score": score, "result": result}

        out: List[HybridResult] = []
        for item in merged.values():
            result = item["result"]
            result.score = item["score"]
            out.append(result)
        out.sort(key=lambda r: r.score, reverse=True)
        if len(out) > pool:
            out = out[:pool]
        return self._finalize(queries[0], out, top_k)

    # ─── Internal: hybrid 3-way RRF ──────────────────────────────────────

    async def _search_hybrid(self, query: str, top_k: int) -> List[HybridResult]:
        fetch_k = max(top_k * 2, 10)

        # Concurrent fetch from all 3 paths
        milvus_task = self._fetch_milvus(query, fetch_k)
        es_task = self._fetch_es(query, fetch_k)
        kg_task = self._fetch_kg(query, fetch_k)
        milvus_path, es_path, kg_path = await asyncio.gather(
            milvus_task, es_task, kg_task,
        )

        if not milvus_path.ok and not es_path.ok and not kg_path.ok:
            logger.warning("All 3 search paths failed")
            return []

        # Fallback to available paths
        if not milvus_path.ok and not es_path.ok:
            return self._materialize_kg_only(kg_path.hits, top_k)
        if not milvus_path.ok:
            return await self._search_keyword(query, top_k)
        if not es_path.ok:
            return await self._search_semantic(query, top_k)

        # ── Weight normalisation: renormalise to 1.0 across AVAILABLE paths ──
        raw_sem = max(0.0, float(self.settings.rag_semantic_weight))
        raw_kw = max(0.0, 1.0 - raw_sem - self.settings.kg_weight)
        raw_kg = max(0.0, self.settings.kg_weight)

        available = 0.0
        available += raw_sem if milvus_path.ok else 0.0
        available += raw_kw if es_path.ok else 0.0
        available += raw_kg if kg_path.ok else 0.0

        if available <= 0.0:
            logger.warning("All paths unavailable after weight check")
            return []

        sem_w = raw_sem / available if milvus_path.ok else 0.0
        kw_w = raw_kw / available if es_path.ok else 0.0
        kg_w = raw_kg / available if kg_path.ok else 0.0

        k = self.settings.rag_rrf_constant_k or 60
        rrf_scores: Dict[int, float] = {}

        # ── Track path membership for source attribution ──
        milvus_ids: set[int] = set()
        es_ids: set[int] = set()

        for rank, hit in enumerate(milvus_path.hits):
            pg_id = hit.get("pg_id")
            if pg_id is None:
                continue
            milvus_ids.add(pg_id)
            rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + sem_w / (k + rank + 1)

        for rank, hit in enumerate(es_path.hits):
            pg_id = hit.get("pg_id")
            if pg_id is None:
                continue
            es_ids.add(pg_id)
            rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + kw_w / (k + rank + 1)

        if kg_path.ok:
            for rank, hit in enumerate(kg_path.hits):
                pg_id = hit.get("pg_id", 0) if isinstance(hit, dict) else getattr(hit, "pg_id", 0)
                if not pg_id:
                    continue
                rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + kg_w / (k + rank + 1)

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        if len(sorted_ids) > top_k:
            sorted_ids = sorted_ids[:top_k]
        if not sorted_ids:
            return []

        # Load content from PG
        ids = [pid for pid, _ in sorted_ids]
        row_map = await self._load_chunks_by_ids(ids)
        results: List[HybridResult] = []
        for pid, score in sorted_ids:
            row = row_map.get(pid)
            if row is None:
                continue
            # ── Set source based on which path(s) contributed ──
            in_m = pid in milvus_ids
            in_e = pid in es_ids
            if in_m and in_e:
                source = "semantic+keyword"
            elif in_m:
                source = "semantic"
            elif in_e:
                source = "keyword"
            else:
                source = "hybrid"  # kg-only (rare)
            results.append(HybridResult(
                pg_id=pid,
                content=row.get("content", ""),
                score=score,
                source=source,
                parent=row.get("parent_content", "") or "",
            ))
        return results

    async def _search_semantic(self, query: str, top_k: int) -> List[HybridResult]:
        path = await self._fetch_milvus(query, top_k)
        if not path.ok:
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: List[HybridResult] = []
        for h in path.hits:
            pid = h.get("pg_id")
            if pid is None:
                continue
            row = row_map.get(pid, {})
            content = row.get("content") or h.get("content") or ""
            if not content:
                continue
            results.append(HybridResult(
                pg_id=pid, content=content,
                score=float(h.get("score", 0.0)), source="semantic",
                parent=row.get("parent_content", "") or "",
            ))
        return results

    async def _search_keyword(self, query: str, top_k: int) -> List[HybridResult]:
        path = await self._fetch_es(query, top_k)
        if not path.ok:
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: List[HybridResult] = []
        for h in path.hits:
            pid = h.get("pg_id")
            if pid is None:
                continue
            row = row_map.get(pid, {})
            content = row.get("content") or h.get("content") or ""
            if not content:
                continue
            results.append(HybridResult(
                pg_id=pid, content=content,
                score=float(h.get("score", 0.0)), source="keyword",
                parent=row.get("parent_content", "") or "",
            ))
        return results

    # ─── 3-way fetch (each returns _PathHits) ────────────────────────────

    async def _fetch_milvus(self, query: str, fetch_k: int) -> _PathHits:
        if not self._milvus_ok():
            return _PathHits(ok=False)
        if self._embed_fn is None:
            logger.warning("embed_fn not injected, skipping Milvus path")
            return _PathHits(ok=False)
        try:
            query_emb = self._embed_fn(query)
        except Exception as e:
            logger.warning("Query vectorization failed: %s", e)
            return _PathHits(ok=False)
        if not query_emb:
            return _PathHits(ok=False)
        dim = self.settings.rag_milvus_dim
        if dim and len(query_emb) != dim:
            logger.warning("Embedding dim %d != rag_milvus_dim=%d, skipping", len(query_emb), dim)
            return _PathHits(ok=False)
        try:
            hits = self._milvus_search_fn(query_emb, fetch_k) or []
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("Milvus search failed: %s", e)
            return _PathHits(ok=False)

    async def _fetch_es(self, query: str, fetch_k: int) -> _PathHits:
        if not self._es_ok():
            return _PathHits(ok=False)
        try:
            hits = (await self._es_search_fn(query, fetch_k)) or []
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("ES search failed: %s", e)
            return _PathHits(ok=False)

    async def _fetch_kg(self, query: str, fetch_k: int) -> _PathHits:
        if not self._kg_ok():
            return _PathHits(ok=False)
        try:
            hits = (await self._kg_search_fn(query, fetch_k)) or []
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("KG search failed: %s", e)
            return _PathHits(ok=False)

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _rerank_pool(self, top_k: int) -> int:
        pool = top_k * (4 if self._reranker is not None else 2)
        return max(pool, 10)

    def _finalize(self, query: str, results: List[HybridResult], top_k: int) -> List[HybridResult]:
        if self._reranker is not None and len(results) > 1:
            return self._reranker.rerank(query, results, top_k)
        if top_k > 0 and len(results) > top_k:
            return results[:top_k]
        return results

    @staticmethod
    def _materialize_kg_only(hits: list, top_k: int) -> List[HybridResult]:
        out: List[HybridResult] = []
        for hit in hits[:top_k]:
            pg_id = hit.get("pg_id", 0) if isinstance(hit, dict) else getattr(hit, "pg_id", 0)
            content = hit.get("content", "") if isinstance(hit, dict) else getattr(hit, "content", "")
            out.append(HybridResult(
                pg_id=pg_id, content=content,
                score=float(hit.get("score", 0.0)) if isinstance(hit, dict) else getattr(hit, "score", 0.0),
                source="kg",
            ))
        return out

    @staticmethod
    async def _load_chunks_by_ids(ids: List[int]) -> Dict[int, dict]:
        """Load chunk content + parent from PG by IDs."""
        if not ids:
            return {}
        try:
            async with get_db() as session:
                stmt = select(RagChunk).where(RagChunk.id.in_(ids))
                result = await session.execute(stmt)
                rows = result.scalars().all()
            return {
                r.id: {
                    "content": r.content or "",
                    "parent_content": r.parent_content or "",
                }
                for r in rows
            }
        except Exception as e:
            logger.warning("PG chunk load failed: %s", e)
            return {}
