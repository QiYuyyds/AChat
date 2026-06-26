"""RAG Engine — split → index → search → compose answer.

Ported from AGI-memory ``internal/rag/rag.py``.
Adaptation: async indexing/search; infrastructure backends injected.
"""

import hashlib
import logging
from typing import Callable, Dict, List, Optional, Tuple

from app.config import Settings
from app.infra.hybrid import HybridResult, HybridStore
from app.rag.rewriter import HistoryMessage, LLMRewriter
from app.rag.reranker import LLMReranker
from app.rag.splitter import Chunk, RecursiveSplitter

logger = logging.getLogger(__name__)


class RAGEngine:
    """RAG engine: split → index (PG/Milvus/ES) → search (RRF fusion) → LLM compose."""

    def __init__(self, settings: Settings, hybrid: Optional[HybridStore] = None):
        self.settings = settings
        parent_size = max(settings.rag_chunk_size * 4, 600)
        parent_overlap = settings.rag_chunk_overlap * 2
        self.parent_splitter = RecursiveSplitter(parent_size, parent_overlap)
        self.child_splitter = RecursiveSplitter(settings.rag_chunk_size, settings.rag_chunk_overlap)
        self.loaded = False
        self._generate_fn: Optional[Callable[[str, str], str]] = None
        self._rewriter: Optional[LLMRewriter] = None
        self._reranker: Optional[LLMReranker] = None
        self._hybrid = hybrid
        self._embed_fn: Optional[Callable] = None

    def set_generate_fn(self, fn: Callable[[str, str], str]) -> None:
        self._generate_fn = fn

    def set_embed_fn(self, fn: Callable) -> None:
        self._embed_fn = fn
        if self._hybrid:
            self._hybrid.set_embed_fn(fn)

    def set_rewriter(self, rewriter: Optional[LLMRewriter]) -> None:
        self._rewriter = rewriter

    def set_reranker(self, reranker: Optional[LLMReranker]) -> None:
        self._reranker = reranker
        if self._hybrid:
            self._hybrid.set_reranker(reranker)

    def set_hybrid(self, hybrid: HybridStore) -> None:
        self._hybrid = hybrid

    # ─── Ingest ───────────────────────────────────────────────────────────

    async def ingest(self, doc: str) -> int:
        """Split document, embed, and index to PG/Milvus/ES."""
        parents = self.parent_splitter.split(doc)
        chunks: List[Chunk] = []
        child_parents: List[str] = []
        for parent in parents:
            for child in self.child_splitter.split(parent.content):
                child.id = len(chunks)
                chunks.append(child)
                child_parents.append(parent.content)
        if not chunks:
            return 0

        doc_hash = hashlib.sha256(doc.encode("utf-8")).hexdigest()[:16]
        contents = [chunk.content for chunk in chunks]
        embeddings: List[List[float]] = []
        for i, chunk in enumerate(chunks):
            embedding: List[float] = []
            if self._embed_fn:
                try:
                    embedding = self._embed_fn(chunk.content)
                except Exception as e:
                    logger.warning("Chunk vectorization failed (idx=%d): %s", i, e)
            embeddings.append(embedding)

        if self._hybrid:
            await self._hybrid.index_chunks(doc_hash, contents, child_parents, embeddings)
        else:
            logger.warning("No hybrid store configured, chunks not indexed")

        self.loaded = True
        logger.info("Ingested %d chunks from %d parents (doc_hash=%s)", len(chunks), len(parents), doc_hash)
        return len(chunks)

    # ─── Search ───────────────────────────────────────────────────────────

    async def query(self, question: str) -> Tuple[str, List[dict]]:
        return await self.query_with_history(question, [])

    async def query_with_history(
        self,
        question: str,
        history: Optional[List[HistoryMessage]] = None,
    ) -> Tuple[str, List[dict]]:
        if not self.loaded:
            return "Knowledge base is empty. Please upload documents first.", []
        if not self._hybrid:
            return "Search infrastructure unavailable.", []

        top_k = max(1, self.settings.rag_top_k)
        queries = [question]
        if self._rewriter:
            rewritten = self._rewriter.rewrite(question, history or [])
            if rewritten:
                queries = rewritten

        hybrid_hits = await self._hybrid.search_multi(queries, top_k)
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

    def _compose_answer(self, question: str, fused: List[dict]) -> Tuple[str, List[dict]]:
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
    def _dedupe_results(results: List[dict]) -> List[dict]:
        seen = set()
        deduped: List[dict] = []
        for item in results:
            content = (item.get("content") or "").strip()
            if not content or content in seen:
                continue
            seen.add(content)
            deduped.append(item)
        return deduped
