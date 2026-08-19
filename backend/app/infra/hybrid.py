"""HybridStore — enterprise hybrid search: Milvus dense + BM25 + KG + graph RRF post-fusion.

Milvus native BM25 replaces Elasticsearch. Dense + BM25 fusion uses Milvus
WeightedRanker (hybrid_search). Graph retrieval results are fused via RRF
post-fusion (graph lives in Neo4j, cannot join WeightedRanker).

RRF post-fusion formula (graph):
    fused_score(d) = 1.0/(rrf_k + rank_chunk(d)) + graph_weight/(rrf_k + rank_graph(d))
"""

import asyncio
import logging
import math
import re
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from app.config import Settings
from app.db.engine import get_db
from app.db.models import RagChunk

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]

# ─── Milvus Collection schema constants ───
_COLLECTION_NAME = "rag_embeddings"
CONTENT_FIELD = "content"
CONTENT_SPARSE_FIELD = "content_sparse"
CONTENT_ANALYZER_PARAMS = {"type": "chinese"}
VECTOR_METRIC_TYPE = "COSINE"

_TFIDF_SAFETY_CAP = 5000

_query_semaphore_refs: dict[int, tuple] = {}


@dataclass
class RetrievalConfig:
    """Parameterize RAG search behavior. When omitted, settings defaults are used."""
    search_mode: str = "hybrid"
    final_top_k: int | None = None
    recall_top_k: int | None = None
    similarity_threshold: float | None = None
    bm25_top_k: int | None = None
    vector_weight: float | None = None
    bm25_weight: float | None = None
    kg_weight: float | None = None
    use_graph_retrieval: bool | None = None
    graph_entity_top_k: int | None = None
    graph_expand_depth: int | None = None
    bm25_drop_ratio_search: float | None = None
    include_distances: bool = False
    # Graph retrieval parameters (task 7.1-7.5)
    graph_triple_top_k: int = 10
    graph_max_nodes: int = 10000
    graph_top_k: int = 20
    graph_weight: float = 1.0
    ppr_damping: float = 0.85
    # Graph seed weighting (rag-graph-v2)
    graph_seed_weight_by_type: bool = True
    graph_seed_weight_by_score: bool = True
    graph_triple_inject_seeds: bool = True
    graph_entity_type_weights: dict[str, float] | None = None


def _resolve_recall_top_k(rc: RetrievalConfig | None, top_k: int) -> int:
    """Resolve recall-stage top_k: rc.recall_top_k → rc.final_top_k*4 → top_k*4, min 10."""
    if rc and rc.recall_top_k is not None:
        return max(rc.recall_top_k, 10)
    base = rc.final_top_k if rc and rc.final_top_k is not None else top_k
    return max(base * 4, 10)


def _tokenize(text: str) -> list[str]:
    """Tokenize text for TF cosine. Supports Chinese per-char + Latin per-word."""
    if not text:
        return []
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z][a-zA-Z0-9_]*|\d+", text.lower()):
        if re.match(r"[\u4e00-\u9fff]", chunk):
            tokens.extend(list(chunk))
        else:
            tokens.append(chunk)
    return tokens


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Compute term frequency vector from token list."""
    if not tokens:
        return {}
    total = len(tokens)
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return {t: c / total for t, c in counts.items()}


def _cosine_similarity(tf1: dict[str, float], tf2: dict[str, float]) -> float:
    """Compute cosine similarity between two TF vectors."""
    if not tf1 or not tf2:
        return 0.0
    smaller = tf1 if len(tf1) <= len(tf2) else tf2
    larger = tf2 if len(tf1) <= len(tf2) else tf1
    dot = 0.0
    for term, w in smaller.items():
        if term in larger:
            dot += w * larger[term]
    norm1 = math.sqrt(sum(w * w for w in tf1.values()))
    norm2 = math.sqrt(sum(w * w for w in tf2.values()))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def _vec_norm(v: list[float]) -> float:
    """L2 norm of a float vector."""
    return math.sqrt(sum(x * x for x in v))


def _vec_cosine(query: list[float], query_norm: float, doc: list[float]) -> float:
    """Cosine similarity between two float vectors (query norm pre-computed for reuse)."""
    if not doc or len(doc) != len(query):
        return 0.0
    dot = sum(a * b for a, b in zip(query, doc, strict=False))
    doc_norm = math.sqrt(sum(x * x for x in doc))
    if query_norm == 0.0 or doc_norm == 0.0:
        return 0.0
    return dot / (query_norm * doc_norm)


def _get_query_semaphore(limit: int) -> asyncio.Semaphore:
    """Get or create a loop-scoped Semaphore for I/O concurrency control."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    entry = _query_semaphore_refs.get(key)
    if entry is not None:
        sem, ref = entry
        if ref() is None:
            entry = None
    if entry is None:
        sem = asyncio.Semaphore(limit)
        ref = weakref.ref(loop, lambda _: _query_semaphore_refs.pop(key, None))
        _query_semaphore_refs[key] = (sem, ref)
    return sem


async def _run_query_io(func: Callable, *args, **kwargs):
    """Wrap blocking I/O in asyncio.to_thread with semaphore-gated concurrency."""
    limit = kwargs.pop("_sem_limit", 8)
    sem = _get_query_semaphore(limit)

    async def _guarded():
        async with sem:
            return await asyncio.to_thread(func, *args, **kwargs)

    return await _guarded()


@dataclass
class HybridResult:
    """Single result from hybrid search."""
    pg_id: int = 0
    content: str = ""
    score: float = 0.0
    source: str = ""  # "hybrid" | "semantic" | "keyword"
    parent: str = ""
    source_info: dict = field(default_factory=dict)


@dataclass
class _PathHits:
    """Single-path retrieval result (rank-ordered) + success flag."""
    hits: list[dict] = field(default_factory=list)
    ok: bool = False


async def _noop_path_hits() -> _PathHits:
    """Return a no-op _PathHits for unavailable paths in asyncio.gather."""
    return _PathHits(ok=False)


class HybridStore:
    """Enterprise hybrid search:
        - Milvus dense vector search (COSINE)
        - Milvus native BM25 (SPARSE_FLOAT_VECTOR + Function(BM25))
        - Milvus hybrid_search + WeightedRanker for dense+BM25 fusion
        - Neo4j knowledge graph entity traversal + RRF post-fusion

    Search functions are injected via setters; unavailable paths return empty results.
    """

    def __init__(
        self,
        settings: Settings,
        embed_fn: EmbedFn | None = None,
    ):
        self.settings = settings
        self._embed_fn = embed_fn
        self._reranker = None

        # Injected search backends (set by infrastructure factory)
        self._milvus_search_fn: Callable | None = None  # (emb, k) -> List[dict]
        self._milvus_bm25_search_fn: Callable | None = None  # (query_text, k, drop_ratio) -> List[dict]
        self._milvus_hybrid_search_fn: Callable | None = None  # (query_text, emb, k, vw, bw, dr) -> List[dict]
        self._kg_search_fn: Callable | None = None      # (query, k) -> List[dict]

        # Milvus insert backend
        self._milvus_insert_fn: Callable | None = None  # (ids, contents, embs) -> None

        # KG index/delete backends
        self._kg_index_fn: Callable | None = None   # (doc_hash, chunks: List[ChunkRef]) -> None
        self._kg_delete_fn: Callable | None = None  # (doc_hash) -> None

        # Track whether PG has any chunks (for fallback viability check)
        self._has_chunks: bool = False

    def set_embed_fn(self, fn: EmbedFn) -> None:
        self._embed_fn = fn

    def set_milvus_backend(
        self,
        search_fn: Callable,
        insert_fn: Callable | None = None,
        bm25_search_fn: Callable | None = None,
        hybrid_search_fn: Callable | None = None,
    ) -> None:
        self._milvus_search_fn = search_fn
        self._milvus_insert_fn = insert_fn
        self._milvus_bm25_search_fn = bm25_search_fn
        self._milvus_hybrid_search_fn = hybrid_search_fn

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

    def _milvus_bm25_ok(self) -> bool:
        return self._milvus_bm25_search_fn is not None

    def _milvus_hybrid_ok(self) -> bool:
        return self._milvus_hybrid_search_fn is not None

    def _kg_ok(self) -> bool:
        return self._kg_search_fn is not None

    def mode(self) -> str:
        m = self._milvus_ok()
        bm25 = self._milvus_bm25_ok()
        hybrid = self._milvus_hybrid_ok()
        if m and hybrid:
            return "hybrid"
        if m:
            return "semantic"
        if bm25:
            return "keyword"
        if self._tfidf_ok() and self._embed_fn is not None:
            return "pg_embedding"
        if self._tfidf_ok():
            return "tfidf"
        return "unavailable"

    def _tfidf_ok(self) -> bool:
        """Check if PG TF cosine fallback is viable (PG has chunks)."""
        return self._has_chunks

    async def check_pg_chunks(self) -> None:
        """Check if PG has any chunks and update _has_chunks flag. Call at startup."""
        try:
            async with get_db() as session:
                result = await session.execute(
                    select(RagChunk.id).limit(1)
                )
                self._has_chunks = result.first() is not None
        except Exception as e:
            logger.warning("Failed to check PG chunks existence: %s", e)

    # ─── Indexing (async) ─────────────────────────────────────────────────

    async def index_chunks(
        self,
        doc_hash: str,
        contents: list[str],
        parents: list[str],
        embeddings: list[list[float]],
        *,
        content_hashes: list[str] | None = None,
        cache_hit: list[bool] | None = None,
        user_id: str | None = None,
        token_counts: list[int] | None = None,
        char_positions: list[tuple[int, int]] | None = None,
    ) -> list[int]:
        """Persist chunks to PG + Milvus. KG indexing is managed by GraphBuildTask.

        Args:
            content_hashes: chunk-level sha256[:16] for embedding cache reuse.
            cache_hit: True = embedding reused from cache, skip KG entity extraction.
            user_id: owner for multi-user isolation.
            token_counts: per-chunk token count (rune length). Defaults to len(content).
            char_positions: per-chunk (start, end) char positions in the source document.
        """
        pg_ids: list[int] = []

        for idx, content in enumerate(contents):
            embedding = embeddings[idx] if idx < len(embeddings) else []
            parent_content = parents[idx] if idx < len(parents) else ""
            ch = content_hashes[idx] if content_hashes and idx < len(content_hashes) else None
            tc = token_counts[idx] if token_counts and idx < len(token_counts) else len(content)
            cp = char_positions[idx] if char_positions and idx < len(char_positions) else (None, None)
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
                        user_id=user_id,
                        chunk_token_count=tc,
                        start_char_pos=cp[0],
                        end_char_pos=cp[1],
                    )
                    session.add(row)
                    await session.flush()
                    pg_id = row.id or 0
                    if pg_id > 0:
                        pg_ids.append(pg_id)
            except Exception as e:
                logger.warning("PG chunk save failed (idx=%d): %s", idx, e)
                continue

        if pg_ids:
            self._has_chunks = True

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
                    await asyncio.to_thread(
                        self._milvus_insert_fn,
                        milvus_ids, milvus_contents, milvus_embeddings,
                        user_id=user_id,
                    )
                except Exception as e:
                    logger.warning("Milvus insert failed: %s", e)

        # KG index is now managed by GraphBuildTask (triggered by DocumentService)

        return pg_ids

    # ─── Search (async with asyncio.gather for concurrent paths) ──────────

    async def search(
        self,
        query: str,
        top_k: int,
        *,
        user_id: str | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> list[HybridResult]:
        """Single-query search with auto mode detection."""
        mode = self.mode()
        if retrieval_config is not None:
            mode = self._resolve_mode(retrieval_config, mode)
        if mode == "hybrid":
            return await self._search_hybrid(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
        if mode == "semantic":
            return await self._search_semantic(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
        if mode == "keyword":
            return await self._search_keyword(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
        if mode == "tfidf":
            return await self._search_tfidf(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
        if mode == "pg_embedding":
            return await self._search_pg_embedding(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
        logger.warning("Search infrastructure unavailable (Milvus disconnected and no PG chunks)")
        return []

    async def search_multi(
        self,
        queries: list[str],
        top_k: int,
        *,
        user_id: str | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> list[HybridResult]:
        """Multi-query search with RRF fusion across query variants."""
        queries = [q for q in (queries or []) if q]
        if not queries:
            return []
        pool = self._rerank_pool(top_k)
        if len(queries) == 1:
            results = await self.search(queries[0], pool, user_id=user_id, retrieval_config=retrieval_config)
            return self._finalize(queries[0], results, top_k)

        tasks = [
            self.search(q, pool, user_id=user_id, retrieval_config=retrieval_config)
            for q in queries
        ]
        results_by_query = await asyncio.gather(*tasks, return_exceptions=True)

        k = self.settings.rag_rrf_constant_k or 60
        merged: dict[str, dict] = {}
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

        out: list[HybridResult] = []
        for item in merged.values():
            result = item["result"]
            result.score = item["score"]
            out.append(result)
        out.sort(key=lambda r: r.score, reverse=True)
        if len(out) > pool:
            out = out[:pool]
        return self._finalize(queries[0], out, top_k)

    # ─── Internal: Milvus hybrid + graph RRF post-fusion ────────────────

    def _resolve_mode(self, rc: RetrievalConfig, auto_mode: str) -> str:
        """Resolve search mode from RetrievalConfig, clamped by availability."""
        requested = rc.search_mode
        m = self._milvus_ok()
        bm25 = self._milvus_bm25_ok()
        if requested == "vector":
            if m:
                return "semantic"
            return "pg_embedding" if self._embed_fn else ("tfidf" if self._tfidf_ok() else "unavailable")
        if requested == "keyword":
            return "keyword" if bm25 else ("tfidf" if self._tfidf_ok() else "unavailable")
        return auto_mode

    async def _search_tfidf(self, query: str, top_k: int, *, user_id: str | None = None, retrieval_config: RetrievalConfig | None = None) -> list[HybridResult]:
        """PG TF cosine fallback search."""
        recall_k = _resolve_recall_top_k(retrieval_config, top_k)
        path = await self._search_tfidf_fallback(query, recall_k, user_id=user_id)
        if not path.ok:
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: list[HybridResult] = []
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
                score=float(h.get("score", 0.0)), source="tfidf",
                parent=row.get("parent_content", "") or "",
                source_info=row.get("source_info", {}),
            ))
        if top_k > 0 and len(results) > top_k:
            results = results[:top_k]
        return results

    async def _search_hybrid(
        self,
        query: str,
        top_k: int,
        *,
        user_id: str | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> list[HybridResult]:
        recall_k = _resolve_recall_top_k(retrieval_config, top_k)

        # Resolve weights and drop_ratio
        raw_vw = max(0.0, float(
            retrieval_config.vector_weight if retrieval_config and retrieval_config.vector_weight is not None
            else self.settings.rag_semantic_weight
        ))
        raw_bw = max(0.0, float(
            retrieval_config.bm25_weight if retrieval_config and retrieval_config.bm25_weight is not None
            else self.settings.rag_keyword_weight
        ))
        drop_ratio = float(
            retrieval_config.bm25_drop_ratio_search if retrieval_config and retrieval_config.bm25_drop_ratio_search is not None
            else self.settings.milvus_bm25_drop_ratio_search
        )

        # Try Milvus hybrid_search (WeightedRanker) first
        milvus_hybrid_task = (
            self._search_milvus_hybrid(
                query, recall_k, vector_weight=raw_vw, bm25_weight=raw_bw,
                drop_ratio=drop_ratio, user_id=user_id,
            )
            if self._milvus_hybrid_ok()
            else _noop_path_hits()
        )

        # Concurrent graph fetch
        expand_depth = 0
        use_graph = False
        if retrieval_config:
            if retrieval_config.graph_expand_depth is not None:
                expand_depth = retrieval_config.graph_expand_depth
            if retrieval_config.use_graph_retrieval:
                use_graph = True
                if expand_depth == 0:
                    expand_depth = 1

        graph_task = (
            self._fetch_kg(query, recall_k, expand_depth=expand_depth, retrieval_config=retrieval_config)
            if use_graph
            else _noop_path_hits()
        )

        milvus_path, graph_path = await asyncio.gather(milvus_hybrid_task, graph_task)

        # If Milvus hybrid failed, fall back to individual paths
        if not milvus_path.ok:
            sem_task = self._fetch_milvus(query, recall_k, user_id=user_id)
            kw_task = (
                self._search_milvus_bm25(query, recall_k, drop_ratio=drop_ratio, user_id=user_id)
                if self._milvus_bm25_ok()
                else _noop_path_hits()
            )
            sem_path, kw_path = await asyncio.gather(sem_task, kw_task)

            if not sem_path.ok and self._embed_fn:
                sem_path = await self._search_pg_embedding_fallback(query, recall_k, user_id=user_id)
            if not kw_path.ok and self._tfidf_ok():
                kw_path = await self._search_tfidf_fallback(query, recall_k, user_id=user_id)

            if not sem_path.ok and not kw_path.ok and not graph_path.ok:
                return []

            if not sem_path.ok and not kw_path.ok:
                return self._materialize_kg_only(graph_path.hits, top_k) if graph_path.ok else []
            if not sem_path.ok:
                return await self._search_keyword(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
            if not kw_path.ok:
                return await self._search_semantic(query, top_k, user_id=user_id, retrieval_config=retrieval_config)

            # RRF 2-way fallback (semantic + keyword)
            k = self.settings.rag_rrf_constant_k or 60
            rrf_scores: dict[int, float] = {}
            sem_ids: set[int] = set()
            kw_ids: set[int] = set()

            for rank, hit in enumerate(sem_path.hits):
                pg_id = hit.get("pg_id")
                if pg_id is None:
                    continue
                sem_ids.add(pg_id)
                rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + raw_vw / (k + rank + 1)

            for rank, hit in enumerate(kw_path.hits):
                pg_id = hit.get("pg_id")
                if pg_id is None:
                    continue
                kw_ids.add(pg_id)
                rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + raw_bw / (k + rank + 1)

            # Graph RRF post-fusion
            if graph_path.ok:
                graph_w = float(
                    retrieval_config.graph_weight if retrieval_config
                    else 1.0
                )
                for rank, hit in enumerate(graph_path.hits):
                    pg_id = hit.get("pg_id", 0) if isinstance(hit, dict) else getattr(hit, "pg_id", 0)
                    if not pg_id:
                        continue
                    rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + graph_w / (k + rank + 1)

            sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_ids) > top_k:
                sorted_ids = sorted_ids[:top_k]
            if not sorted_ids:
                return []

            ids = [pid for pid, _ in sorted_ids]
            row_map = await self._load_chunks_by_ids(ids)
            results: list[HybridResult] = []
            for pid, score in sorted_ids:
                row = row_map.get(pid)
                if row is None:
                    continue
                in_m = pid in sem_ids
                in_k = pid in kw_ids
                if in_m and in_k:
                    source = "semantic+keyword"
                elif in_m:
                    source = "semantic"
                elif in_k:
                    source = "keyword"
                else:
                    source = "hybrid"
                results.append(HybridResult(
                    pg_id=pid,
                    content=row.get("content", ""),
                    score=score,
                    source=source,
                    parent=row.get("parent_content", "") or "",
                    source_info=row.get("source_info", {}),
                ))
            return results

        # Milvus hybrid succeeded — graph RRF post-fusion if graph results available
        if not graph_path.ok or not graph_path.hits:
            # No graph results: return Milvus hybrid results directly
            return await self._materialize_milvus_hits(milvus_path, top_k)

        # RRF post-fusion: Milvus hybrid + graph
        graph_w = float(
            retrieval_config.graph_weight if retrieval_config
            else 1.0
        )
        fused = self._fuse_chunk_rankings(milvus_path.hits, graph_path.hits, self.settings.rag_rrf_constant_k or 60, graph_w)
        if len(fused) > top_k:
            fused = fused[:top_k]
        if not fused:
            return []

        ids = [h["pg_id"] for h in fused if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results_final: list[HybridResult] = []
        for h in fused:
            pid = h.get("pg_id")
            if pid is None:
                continue
            row = row_map.get(pid, {})
            content = row.get("content") or h.get("content") or ""
            if not content:
                continue
            results_final.append(HybridResult(
                pg_id=pid, content=content,
                score=float(h.get("score", 0.0)), source="hybrid",
                parent=row.get("parent_content", "") or "",
                source_info=row.get("source_info", {}),
            ))
        return results_final

    async def _search_milvus_bm25(
        self, query_text: str, top_k: int, *,
        drop_ratio: float = 0.0,
        user_id: str | None = None,
    ) -> _PathHits:
        """Milvus native BM25 search via injected callback."""
        from app.observability import start_span
        if not self._milvus_bm25_ok():
            return _PathHits(ok=False)
        try:
            kwargs: dict = {"drop_ratio": drop_ratio}
            if user_id is not None:
                kwargs["user_id"] = user_id
            with start_span("rag.milvus_bm25_search", top_k=top_k) as span:
                hits = await _run_query_io(
                    self._milvus_bm25_search_fn, query_text, top_k, **kwargs,
                    _sem_limit=self.settings.rag_search_concurrency,
                ) or []
                if span.is_recording():
                    span.set_attribute("agenthub.hits", len(hits))
                    span.set_attribute("agenthub.empty", not hits)
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("Milvus BM25 search failed: %s", e)
            return _PathHits(ok=False)

    async def _search_milvus_hybrid(
        self, query_text: str, top_k: int, *,
        vector_weight: float, bm25_weight: float, drop_ratio: float,
        user_id: str | None = None,
    ) -> _PathHits:
        """Milvus hybrid_search with WeightedRanker via injected callback."""
        from app.observability import start_span
        if not self._milvus_hybrid_ok():
            return _PathHits(ok=False)
        if self._embed_fn is None:
            return _PathHits(ok=False)
        try:
            query_emb = self._embed_fn(query_text)
        except Exception as e:
            logger.warning("Query vectorization failed for hybrid: %s", e)
            return _PathHits(ok=False)
        if not query_emb:
            return _PathHits(ok=False)
        dim = self.settings.rag_milvus_dim
        if dim and len(query_emb) != dim:
            logger.warning("Embedding dim %d != rag_milvus_dim=%d, skipping hybrid", len(query_emb), dim)
            return _PathHits(ok=False)
        try:
            kwargs: dict = {
                "vector_weight": vector_weight,
                "bm25_weight": bm25_weight,
                "drop_ratio": drop_ratio,
            }
            if user_id is not None:
                kwargs["user_id"] = user_id
            with start_span("rag.milvus_hybrid_search", top_k=top_k) as span:
                hits = await _run_query_io(
                    self._milvus_hybrid_search_fn, query_text, query_emb, top_k, **kwargs,
                    _sem_limit=self.settings.rag_search_concurrency,
                ) or []
                if span.is_recording():
                    span.set_attribute("agenthub.hits", len(hits))
                    span.set_attribute("agenthub.empty", not hits)
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("Milvus hybrid search failed: %s", e)
            return _PathHits(ok=False)

    @staticmethod
    def _fuse_chunk_rankings(
        milvus_results: list, graph_results: list,
        rrf_k: float, graph_weight: float,
    ) -> list[dict]:
        """RRF post-fusion of Milvus hybrid results and graph results.

        fused_score(d) = 1.0/(rrf_k + rank_chunk(d)) + graph_weight/(rrf_k + rank_graph(d))
        """
        fused: dict[int, dict] = {}
        for rank, hit in enumerate(milvus_results):
            pg_id = hit.get("pg_id") if isinstance(hit, dict) else getattr(hit, "pg_id", None)
            if pg_id is None:
                continue
            score = 1.0 / (rrf_k + rank + 1)
            if pg_id in fused:
                fused[pg_id]["score"] += score
            else:
                fused[pg_id] = {"pg_id": pg_id, "score": score, "content": hit.get("content", "") if isinstance(hit, dict) else getattr(hit, "content", "")}

        for rank, hit in enumerate(graph_results):
            pg_id = hit.get("pg_id", 0) if isinstance(hit, dict) else getattr(hit, "pg_id", 0)
            if not pg_id:
                continue
            score = graph_weight / (rrf_k + rank + 1)
            if pg_id in fused:
                fused[pg_id]["score"] += score
            else:
                fused[pg_id] = {"pg_id": pg_id, "score": score, "content": hit.get("content", "") if isinstance(hit, dict) else getattr(hit, "content", "")}

        out = list(fused.values())
        out.sort(key=lambda h: h["score"], reverse=True)
        return out

    async def _materialize_milvus_hits(self, path: _PathHits, top_k: int) -> list[HybridResult]:
        """Convert Milvus hybrid hits to HybridResult list."""
        ids = [h.get("pg_id") for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: list[HybridResult] = []
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
                score=float(h.get("score", 0.0)), source="hybrid",
                parent=row.get("parent_content", "") or "",
                source_info=row.get("source_info", {}),
            ))
        if top_k > 0 and len(results) > top_k:
            results = results[:top_k]
        return results

    async def _search_semantic(self, query: str, top_k: int, *, user_id: str | None = None, retrieval_config: RetrievalConfig | None = None) -> list[HybridResult]:
        recall_k = _resolve_recall_top_k(retrieval_config, top_k)
        path = await self._fetch_milvus(query, recall_k, user_id=user_id)
        if not path.ok:
            if self._embed_fn:
                return await self._search_pg_embedding(query, top_k, user_id=user_id, retrieval_config=retrieval_config)
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: list[HybridResult] = []
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
                source_info=row.get("source_info", {}),
            ))
        return results

    async def _search_keyword(
        self, query: str, top_k: int, *,
        user_id: str | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> list[HybridResult]:
        drop_ratio = float(
            retrieval_config.bm25_drop_ratio_search if retrieval_config and retrieval_config.bm25_drop_ratio_search is not None
            else self.settings.milvus_bm25_drop_ratio_search
        )
        recall_k = _resolve_recall_top_k(retrieval_config, top_k)
        path = await self._search_milvus_bm25(query, recall_k, drop_ratio=drop_ratio, user_id=user_id)
        if not path.ok:
            if self._tfidf_ok():
                return await self._search_tfidf(query, top_k, user_id=user_id)
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: list[HybridResult] = []
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
                source_info=row.get("source_info", {}),
            ))
        if top_k > 0 and len(results) > top_k:
            results = results[:top_k]
        return results

    # ─── Path fetchers (each returns _PathHits) ──────────────────────────

    async def _fetch_milvus(self, query: str, fetch_k: int, *, user_id: str | None = None) -> _PathHits:
        from app.observability import start_span
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
            kwargs: dict = {}
            if user_id is not None:
                kwargs["user_id"] = user_id
            with start_span("rag.milvus_search", top_k=fetch_k) as span:
                hits = await _run_query_io(
                    self._milvus_search_fn, query_emb, fetch_k, **kwargs,
                    _sem_limit=self.settings.rag_search_concurrency,
                ) or []
                if span.is_recording():
                    span.set_attribute("agenthub.hits", len(hits))
                    span.set_attribute("agenthub.empty", not hits)
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("Milvus search failed: %s", e)
            return _PathHits(ok=False)

    async def _fetch_kg(
        self,
        query: str,
        fetch_k: int,
        *,
        expand_depth: int = 0,
        retrieval_config: RetrievalConfig | None = None,
    ) -> _PathHits:
        from app.observability import start_span

        # 优先使用 GraphRetrieval（PPR 增强）
        try:
            from app.rag.graph_retrieval import GraphRetrieval
            if GraphRetrieval.available():
                with start_span("rag.kg_search", top_k=fetch_k) as span:
                    hits = (await GraphRetrieval.search(query, fetch_k, expand_depth=expand_depth, retrieval_config=retrieval_config)) or []
                    if span.is_recording():
                        span.set_attribute("agenthub.hits", len(hits))
                        span.set_attribute("agenthub.skipped", not hits)
                        span.set_attribute("agenthub.backend", "graph_retrieval")
                return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("GraphRetrieval search failed, falling back to _kg_search_fn: %s", e)

        # 降级：使用现有 _kg_search_fn
        if not self._kg_ok():
            return _PathHits(ok=False)
        try:
            with start_span("rag.kg_search", top_k=fetch_k) as span:
                hits = (await self._kg_search_fn(query, fetch_k)) or []
                if span.is_recording():
                    span.set_attribute("agenthub.hits", len(hits))
                    span.set_attribute("agenthub.skipped", not hits)
                    span.set_attribute("agenthub.backend", "legacy")
            return _PathHits(hits=hits, ok=True)
        except Exception as e:
            logger.warning("KG search failed: %s", e)
            return _PathHits(ok=False)

    async def _search_tfidf_fallback(
        self, query: str, top_k: int, *, user_id: str | None = None
    ) -> _PathHits:
        """PG TF cosine fallback: pull chunks from PG, compute TF cosine ranking."""
        try:
            async with get_db() as session:
                stmt = select(RagChunk.id, RagChunk.content, RagChunk.parent_content).limit(_TFIDF_SAFETY_CAP)
                if user_id is not None:
                    stmt = stmt.where(RagChunk.user_id == user_id)
                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            logger.warning("TF cosine fallback PG query failed: %s", e)
            return _PathHits(ok=False)

        if not rows:
            return _PathHits(ok=False)

        query_tf = _compute_tf(_tokenize(query))
        if not query_tf:
            return _PathHits(ok=False)

        scored: list[dict] = []
        for row in rows:
            pg_id, content = row[0], row[1] or ""
            if not content:
                continue
            tf = _compute_tf(_tokenize(content))
            sim = _cosine_similarity(query_tf, tf)
            if sim > 0.0:
                scored.append({"pg_id": pg_id, "content": content, "score": sim})

        scored.sort(key=lambda h: h["score"], reverse=True)
        if len(scored) > top_k:
            scored = scored[:top_k]

        if len(rows) >= _TFIDF_SAFETY_CAP:
            logger.warning(
                "TF cosine fallback hit safety cap (%d rows); results may be incomplete",
                _TFIDF_SAFETY_CAP,
            )
        return _PathHits(hits=scored, ok=True)

    async def _search_pg_embedding_fallback(
        self, query: str, top_k: int, *, user_id: str | None = None
    ) -> _PathHits:
        """PG embedding vector cosine fallback when Milvus is unavailable.

        Pulls chunks with non-null embeddings from PG, computes vector cosine
        similarity against the query embedding, and returns ranked results.
        """
        if self._embed_fn is None:
            return _PathHits(ok=False)
        try:
            query_emb = await _run_query_io(
                self._embed_fn, query,
                _sem_limit=self.settings.rag_search_concurrency,
            )
        except Exception as e:
            logger.warning("PG embedding fallback: query vectorization failed: %s", e)
            return _PathHits(ok=False)
        if not query_emb:
            return _PathHits(ok=False)

        try:
            async with get_db() as session:
                stmt = (
                    select(RagChunk.id, RagChunk.content, RagChunk.parent_content, RagChunk.embedding)
                    .where(RagChunk.embedding.isnot(None))
                    .limit(_TFIDF_SAFETY_CAP)
                )
                if user_id is not None:
                    stmt = stmt.where(RagChunk.user_id == user_id)
                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            logger.warning("PG embedding fallback PG query failed: %s", e)
            return _PathHits(ok=False)

        if not rows:
            return _PathHits(ok=False)

        query_norm = _vec_norm(query_emb)
        if query_norm == 0.0:
            return _PathHits(ok=False)

        scored: list[dict] = []
        for row in rows:
            pg_id, content, _parent, emb = row[0], row[1] or "", row[2], row[3]
            if not content or not emb:
                continue
            try:
                doc_emb = list(emb) if not isinstance(emb, list) else emb
            except (TypeError, ValueError):
                continue
            if not doc_emb or len(doc_emb) != len(query_emb):
                continue
            score = _vec_cosine(query_emb, query_norm, doc_emb)
            if score > 0.0:
                scored.append({"pg_id": pg_id, "content": content, "score": score})

        scored.sort(key=lambda h: h["score"], reverse=True)
        if len(scored) > top_k:
            scored = scored[:top_k]

        if len(rows) >= _TFIDF_SAFETY_CAP:
            logger.warning(
                "PG embedding fallback hit safety cap (%d rows); results may be incomplete",
                _TFIDF_SAFETY_CAP,
            )
        return _PathHits(hits=scored, ok=True)

    async def _search_pg_embedding(
        self, query: str, top_k: int, *, user_id: str | None = None, retrieval_config: RetrievalConfig | None = None
    ) -> list[HybridResult]:
        """PG embedding vector cosine search (Milvus fallback path)."""
        recall_k = _resolve_recall_top_k(retrieval_config, top_k)
        path = await self._search_pg_embedding_fallback(query, recall_k, user_id=user_id)
        if not path.ok:
            return []
        ids = [h["pg_id"] for h in path.hits if h.get("pg_id") is not None]
        row_map = await self._load_chunks_by_ids(ids) if ids else {}
        results: list[HybridResult] = []
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
                score=float(h.get("score", 0.0)), source="pg_embedding",
                parent=row.get("parent_content", "") or "",
                source_info=row.get("source_info", {}),
            ))
        if top_k > 0 and len(results) > top_k:
            results = results[:top_k]
        return results

    def _rerank_pool(self, top_k: int) -> int:
        pool = top_k * (4 if self._reranker is not None else 2)
        return max(pool, 10)

    def _finalize(self, query: str, results: list[HybridResult], top_k: int) -> list[HybridResult]:
        if self._reranker is not None and len(results) > 1:
            return self._reranker.rerank(query, results, top_k)
        if top_k > 0 and len(results) > top_k:
            return results[:top_k]
        return results

    @staticmethod
    def _materialize_kg_only(hits: list, top_k: int) -> list[HybridResult]:
        out: list[HybridResult] = []
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
    async def _load_chunks_by_ids(ids: list[int]) -> dict[int, dict]:
        """Load chunk content + parent + source_info from PG by IDs.

        LEFT JOINs documents table to backfill document-level metadata
        (title, source_path, parent_id) for source_info.
        """
        if not ids:
            return {}
        try:
            from app.db.models import Document

            async with get_db() as session:
                stmt = (
                    select(
                        RagChunk.id,
                        RagChunk.content,
                        RagChunk.parent_content,
                        RagChunk.chunk_idx,
                        RagChunk.start_char_pos,
                        RagChunk.end_char_pos,
                        RagChunk.document_id,
                        RagChunk.version_id,
                        Document.title,
                        Document.source_path,
                        Document.parent_id,
                    )
                    .outerjoin(Document, RagChunk.document_id == Document.id)
                    .where(RagChunk.id.in_(ids))
                )
                result = await session.execute(stmt)
                rows = result.all()
            return {
                row[0]: {
                    "content": row[1] or "",
                    "parent_content": row[2] or "",
                    "source_info": {
                        "document_id": row[6],
                        "version_id": row[7],
                        "source_path": row[9] or "",
                        "title": row[8] or "",
                        "chunk_idx": row[3],
                        "start_char_pos": row[4],
                        "end_char_pos": row[5],
                        "parent_id": row[10],
                    },
                }
                for row in rows
            }
        except Exception as e:
            logger.warning("PG chunk load failed: %s", e)
            return {}
