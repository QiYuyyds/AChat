"""RAG Engine — split → index → search → compose answer.

Ported from AGI-memory ``internal/rag/rag.py``.
Adaptation: async indexing/search; infrastructure backends injected.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable

from app.config import Settings
from app.infra.hybrid import HybridStore, RetrievalConfig
from app.rag.chunking.dispatcher import chunk_markdown as dispatch_chunk
from app.rag.chunking.nlp import count_tokens
from app.rag.chunking.presets import normalize_chunk_preset_id
from app.rag.reranker import LLMReranker
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
        self._reranker: LLMReranker | None = None
        self._hybrid = hybrid
        self._embed_fn: Callable | None = None

    def set_generate_fn(self, fn: Callable[[str, str], str]) -> None:
        self._generate_fn = fn

    def set_embed_fn(self, fn: Callable) -> None:
        self._embed_fn = fn
        if self._hybrid:
            self._hybrid.set_embed_fn(fn)

    def set_reranker(self, reranker: LLMReranker | None) -> None:
        self._reranker = reranker
        if self._hybrid:
            self._hybrid.set_reranker(reranker)

    def set_hybrid(self, hybrid: HybridStore) -> None:
        self._hybrid = hybrid

    # ─── Ingest ───────────────────────────────────────────────────────────

    async def ingest(
        self,
        doc: str,
        *,
        user_id: str | None = None,
        preset_id: str = "",
    ) -> int:
        """Split document, embed, and index to PG/Milvus/ES.

        Args:
            doc: Document text content.
            user_id: Optional user ID for data isolation.
            preset_id: Chunking preset (general/qa/semantic/separator).
                       Defaults to settings.rag_chunk_preset when empty.
        """
        pid = normalize_chunk_preset_id(preset_id or self.settings.rag_chunk_preset)
        chunk_config = {
            "chunk_size": self.settings.rag_chunk_size,
            "chunk_overlap": self.settings.rag_chunk_overlap,
            "_parser_config_json": self.settings.rag_chunk_parser_config,
        }

        # Parent split: use dispatcher with larger chunk size for parent context
        parent_config = {
            **chunk_config,
            "chunk_size": max(self.settings.rag_chunk_size * 4, 600),
            "chunk_overlap": self.settings.rag_chunk_overlap * 2,
        }
        parent_texts = dispatch_chunk(
            doc, pid, parent_config, embed_fn=self._embed_fn
        )
        if not parent_texts:
            return 0

        # Child split: for each parent, split into smaller chunks
        child_parents: list[str] = []
        chunk_contents: list[str] = []
        for parent_text in parent_texts:
            child_texts = dispatch_chunk(
                parent_text, pid, chunk_config, embed_fn=self._embed_fn
            )
            if not child_texts:
                child_texts = [parent_text]
            for child_text in child_texts:
                chunk_contents.append(child_text)
                child_parents.append(parent_text)

        if not chunk_contents:
            return 0

        chunks: list[Chunk] = [
            Chunk(id=i, content=c) for i, c in enumerate(chunk_contents)
        ]

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

        embeddings: list[list[float]] = [None] * len(chunks)  # type: ignore[list-item]
        cache_hit: list[bool] = [False] * len(chunks)
        miss_indices: list[int] = []

        for i, _chunk in enumerate(chunks):
            ch = content_hashes[i]
            cached_emb = cache_map.get(ch)
            if cached_emb is not None and (
                expected_dim == 0 or len(cached_emb) == expected_dim
            ):
                embeddings[i] = cached_emb
                cache_hit[i] = True
            else:
                miss_indices.append(i)

        if self._embed_fn and miss_indices:
            sem = asyncio.Semaphore(self.settings.rag_embed_concurrency)

            async def _embed_one(idx: int) -> tuple[int, list[float]]:
                async with sem:
                    try:
                        return idx, await asyncio.to_thread(self._embed_fn, chunks[idx].content)
                    except Exception as e:
                        logger.warning("Chunk vectorization failed (idx=%d): %s", idx, e)
                        return idx, []

            embed_tasks = [_embed_one(i) for i in miss_indices]
            results = await asyncio.gather(*embed_tasks)
            for idx, emb in results:
                embeddings[idx] = emb
                cache_hit[idx] = False

        # Compute per-chunk token counts and char positions for DB metadata
        token_counts: list[int] = [count_tokens(c) for c in contents]
        char_positions: list[tuple[int, int]] = _locate_chunks(doc, contents)

        if self._hybrid:
            await self._hybrid.index_chunks(
                doc_hash,
                contents,
                child_parents,
                embeddings,
                content_hashes=content_hashes,
                cache_hit=cache_hit,
                user_id=user_id,
                token_counts=token_counts,
                char_positions=char_positions,
            )
        else:
            logger.warning("No hybrid store configured, chunks not indexed")

        self.loaded = True
        logger.info(
            "Ingested %d chunks from %d parents (preset=%s, doc_hash=%s, cache_hits=%d)",
            len(chunks),
            len(parent_texts),
            pid,
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

    async def query(
        self,
        question: str,
        *,
        user_id: str | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> tuple[str, list[dict]]:
        if not self.loaded:
            return "Knowledge base is empty. Please upload documents first.", []
        if not self._hybrid:
            return "Search infrastructure unavailable.", []

        top_k = max(1, self.settings.rag_top_k)
        if retrieval_config and retrieval_config.final_top_k is not None:
            top_k = max(1, retrieval_config.final_top_k)
        queries = [question]

        hybrid_hits = await self._hybrid.search_multi(
            queries, top_k, user_id=user_id, retrieval_config=retrieval_config
        )
        from app.observability import start_span
        with start_span("rag.rrf_fuse", final_count=len(hybrid_hits), fusion_method="rrf"):
            fused = [
                {
                    "pg_id": h.pg_id,
                    "content": h.parent or h.content,
                    "score": h.score,
                    "source": h.source,
                    "source_info": h.source_info,
                }
                for h in hybrid_hits
            ]
        return self._compose_answer(question, fused)

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


def _locate_chunks(source: str, chunks: list[str]) -> list[tuple[int, int]]:
    """Locate each chunk's (start, end) char position in the source document.

    Uses incremental search: each chunk is searched starting from the end of the
    previous match. If a chunk is not found (e.g., overlap-modified content), the
    position is (None, None).
    """
    positions: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks:
        idx = source.find(chunk, cursor)
        if idx >= 0:
            positions.append((idx, idx + len(chunk)))
            cursor = idx + len(chunk)
        else:
            # Fallback: search from beginning (overlap may have shifted order)
            idx = source.find(chunk)
            if idx >= 0:
                positions.append((idx, idx + len(chunk)))
            else:
                positions.append((None, None))
    return positions
