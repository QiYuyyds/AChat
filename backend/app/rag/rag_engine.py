"""RAG Engine — split → index → search → compose answer.

Ported from AGI-memory ``internal/rag/rag.py``.
Adaptation: async indexing/search; infrastructure backends injected.
"""

import hashlib
import logging
from collections.abc import Callable

from app.config import Settings
from app.infra.hybrid import HybridStore
from app.rag.reranker import LLMReranker
from app.rag.rewriter import HistoryMessage, LLMRewriter
from app.rag.splitter import Chunk, RecursiveSplitter

logger = logging.getLogger(__name__)


class RAGEngine:
    """RAG engine: split → index (PG/Milvus/ES) → search (RRF fusion) → LLM compose."""

    def __init__(self, settings: Settings, hybrid: HybridStore | None = None):
        self.settings = settings
        parent_size = max(settings.rag_chunk_size * 4, 600)
        parent_overlap = settings.rag_chunk_overlap * 2
        self.parent_splitter = RecursiveSplitter(parent_size, parent_overlap)
        self.child_splitter = RecursiveSplitter(settings.rag_chunk_size, settings.rag_chunk_overlap)
        self.loaded = False
        self._generate_fn: Callable[[str, str], str] | None = None
        self._rewriter: LLMRewriter | None = None
        self._reranker: LLMReranker | None = None
        self._hybrid = hybrid
        self._embed_fn: Callable | None = None

    def set_generate_fn(self, fn: Callable[[str, str], str]) -> None:
        self._generate_fn = fn

    def set_embed_fn(self, fn: Callable) -> None:
        self._embed_fn = fn
        if self._hybrid:
            self._hybrid.set_embed_fn(fn)

    def set_rewriter(self, rewriter: LLMRewriter | None) -> None:
        self._rewriter = rewriter

    def set_reranker(self, reranker: LLMReranker | None) -> None:
        self._reranker = reranker
        if self._hybrid:
            self._hybrid.set_reranker(reranker)

    def set_hybrid(self, hybrid: HybridStore) -> None:
        self._hybrid = hybrid

    # ─── Ingest ───────────────────────────────────────────────────────────

    async def ingest(self, doc: str, *, user_id: str | None = None) -> int:
        """Split document, embed, and index to PG/Milvus/ES."""
        parents = self.parent_splitter.split(doc)
        chunks: list[Chunk] = []
        child_parents: list[str] = []
        for parent in parents:
            for child in self.child_splitter.split(parent.content):
                child.id = len(chunks)
                chunks.append(child)
                child_parents.append(parent.content)
        if not chunks:
            return 0

        doc_hash = hashlib.sha256(doc.encode("utf-8")).hexdigest()[:16]
        contents = [chunk.content for chunk in chunks]

        # Compute chunk-level content_hash for embedding cache reuse
        content_hashes: list[str] = [
            hashlib.sha256(c.encode("utf-8")).hexdigest()[:16] for c in contents
        ]

        # Build embedding cache: batch query PG for existing content_hash + embedding
        cache_map: dict[str, list[float]] = {}
        if content_hashes:
            cache_map = await self._lookup_embedding_cache(content_hashes)

        # Determine expected embedding dimension from settings
        expected_dim = self.settings.rag_milvus_dim

        embeddings: list[list[float]] = []
        cache_hit: list[bool] = []  # True = skip embed_fn and KG extraction
        for i, chunk in enumerate(chunks):
            ch = content_hashes[i]
            cached_emb = cache_map.get(ch)
            if cached_emb is not None and (
                expected_dim == 0 or len(cached_emb) == expected_dim
            ):
                # Cache hit: reuse existing embedding, skip embed_fn + KG
                embeddings.append(cached_emb)
                cache_hit.append(True)
            else:
                # Cache miss (or dim mismatch): generate new embedding
                embedding: list[float] = []
                if self._embed_fn:
                    try:
                        embedding = self._embed_fn(chunk.content)
                    except Exception as e:
                        logger.warning(
                            "Chunk vectorization failed (idx=%d): %s", i, e
                        )
                embeddings.append(embedding)
                cache_hit.append(False)

        if self._hybrid:
            await self._hybrid.index_chunks(
                doc_hash,
                contents,
                child_parents,
                embeddings,
                content_hashes=content_hashes,
                cache_hit=cache_hit,
                user_id=user_id,
            )
        else:
            logger.warning("No hybrid store configured, chunks not indexed")

        self.loaded = True
        logger.info(
            "Ingested %d chunks from %d parents (doc_hash=%s, cache_hits=%d)",
            len(chunks),
            len(parents),
            doc_hash,
            sum(cache_hit),
        )
        return len(chunks)

    async def _lookup_embedding_cache(
        self, content_hashes: list[str]
    ) -> dict[str, list[float]]:
        """Batch query PG for existing embeddings by content_hash.

        Returns a mapping content_hash -> embedding for all hits.
        """
        if not content_hashes:
            return {}
        try:
            from sqlalchemy import select

            from app.db.engine import get_db
            from app.db.models import RagChunk

            # Deduplicate hashes for the IN query
            unique_hashes = list(set(content_hashes))
            async with get_db() as session:
                stmt = (
                    select(RagChunk.content_hash, RagChunk.embedding)
                    .where(
                        RagChunk.content_hash.in_(unique_hashes),
                        RagChunk.embedding.isnot(None),
                    )
                )
                result = await session.execute(stmt)
                rows = result.all()

            cache: dict[str, list[float]] = {}
            for row in rows:
                ch = row[0]
                emb = row[1]
                if ch and emb and ch not in cache:
                    cache[ch] = list(emb)
            return cache
        except Exception as e:
            logger.warning("Embedding cache lookup failed: %s", e)
            return {}

    # ─── Search ───────────────────────────────────────────────────────────

    async def query(self, question: str, *, user_id: str | None = None) -> tuple[str, list[dict]]:
        return await self.query_with_history(question, [], user_id=user_id)

    async def query_with_history(
        self,
        question: str,
        history: list[HistoryMessage] | None = None,
        *,
        user_id: str | None = None,
    ) -> tuple[str, list[dict]]:
        if not self.loaded:
            return "Knowledge base is empty. Please upload documents first.", []
        if not self._hybrid:
            return "Search infrastructure unavailable.", []

        top_k = max(1, self.settings.rag_top_k)
        queries = [question]
        if self._rewriter:
            from app.observability import start_span
            with start_span("rag.query_rewrite", original=question[:100]):
                rewritten = self._rewriter.rewrite(question, history or [])
            if rewritten:
                queries = rewritten

        hybrid_hits = await self._hybrid.search_multi(queries, top_k, user_id=user_id)
        from app.observability import start_span
        with start_span("rag.rrf_fuse", final_count=len(hybrid_hits), fusion_method="rrf"):
            fused = [
                {
                    "pg_id": h.pg_id,
                    "content": h.parent or h.content,
                    "score": h.score,
                    "source": h.source,
                }
                for h in hybrid_hits
            ]
        ask_query = queries[0] if queries else question
        return self._compose_answer(ask_query, fused)

    def _compose_answer(self, question: str, fused: list[dict]) -> tuple[str, list[dict]]:
        fused = self._dedupe_results(fused)
        if not fused:
            return "No relevant content found in knowledge base.", []

        context = "\n\n".join(r["content"] for r in fused if r.get("content"))
        if not context:
            return "No relevant content found in knowledge base.", []

        if self._generate_fn:
            system_prompt = (
                "You are a knowledge-base QA assistant. Answer based ONLY on the provided context. "
                "If the context is insufficient, say so."
            )
            user_msg = f"Context:\n{context}\n\nQuestion: {question}"
            return self._generate_fn(system_prompt, user_msg), fused

        return f"[Knowledge Base Results]\n{context}", fused

    @staticmethod
    def _dedupe_results(results: list[dict]) -> list[dict]:
        seen = set()
        deduped: list[dict] = []
        for item in results:
            content = (item.get("content") or "").strip()
            if not content or content in seen:
                continue
            seen.add(content)
            deduped.append(item)
        return deduped
