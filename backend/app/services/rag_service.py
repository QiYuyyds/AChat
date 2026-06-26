"""RAGService — high-level RAG assembly entry point.

Provides ``ingest()`` and ``search()`` as the main API surface.
Wires RAGEngine + LLMRewriter + LLMReranker based on settings.
"""

import logging
from typing import Callable, List, Optional, Tuple

from app.config import Settings
from app.infra.hybrid import HybridStore
from app.rag.rag_engine import RAGEngine
from app.rag.rewriter import HistoryMessage, LLMRewriter
from app.rag.reranker import LLMReranker

logger = logging.getLogger(__name__)


class RAGService:
    """Facade for RAG operations: ingest documents and search knowledge base."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._hybrid = HybridStore(settings)
        self._engine = RAGEngine(settings, hybrid=self._hybrid)
        self._generate_fn: Optional[Callable] = None
        self._embed_fn: Optional[Callable] = None
        self._initialized = False
        # Delete backends for document cleanup (wired in main.py)
        self._es_delete_fn: Optional[Callable] = None
        self._milvus_delete_fn: Optional[Callable] = None
        self._kg_delete_fn: Optional[Callable] = None

    def set_generate_fn(self, fn: Callable) -> None:
        """Inject LLM generate function for query rewrite / rerank / answer composition."""
        self._generate_fn = fn
        self._engine.set_generate_fn(fn)

        if self.settings.rag_rewrite_enabled:
            self._engine.set_rewriter(
                LLMRewriter(fn, num_queries=self.settings.rag_rewrite_num_queries)
            )
        if self.settings.rag_rerank_enabled:
            self._engine.set_reranker(
                LLMReranker(fn, preview_len=self.settings.rag_rerank_preview_len)
            )

    def set_embed_fn(self, fn: Callable) -> None:
        """Inject embedding function for vector search."""
        self._embed_fn = fn
        self._engine.set_embed_fn(fn)

    def set_milvus_backend(self, search_fn: Callable, insert_fn: Optional[Callable] = None) -> None:
        self._hybrid.set_milvus_backend(search_fn, insert_fn)

    def set_es_backend(self, search_fn: Callable, index_fn: Optional[Callable] = None) -> None:
        self._hybrid.set_es_backend(search_fn, index_fn)

    def set_kg_backend(self, search_fn: Callable) -> None:
        self._hybrid.set_kg_backend(search_fn)

    def set_kg_index_fn(self, fn: Callable) -> None:
        """Inject KG index function for document entity extraction."""
        self._hybrid.set_kg_index_fn(fn)

    def set_es_delete_fn(self, fn: Callable) -> None:
        """Inject ES delete-by-ids function for document cleanup."""
        self._es_delete_fn = fn

    def set_milvus_delete_fn(self, fn: Callable) -> None:
        """Inject Milvus delete-by-ids function for document cleanup."""
        self._milvus_delete_fn = fn

    def set_kg_delete_fn(self, fn: Callable) -> None:
        """Inject KG delete-by-doc-hash function for document cleanup."""
        self._kg_delete_fn = fn

    async def initialize(self) -> None:
        """Load existing chunks to determine if knowledge base has data."""
        if self._initialized:
            return
        # Check if any chunks exist in PG
        try:
            from app.db.engine import get_db
            from app.db.models import RagChunk
            from sqlalchemy import select, func

            async with get_db() as session:
                stmt = select(func.count()).select_from(RagChunk)
                result = await session.execute(stmt)
                count = result.scalar() or 0
                if count > 0:
                    self._engine.loaded = True
                    logger.info("RAG service: %d existing chunks detected", count)
        except Exception as e:
            logger.warning("RAG chunk count check failed: %s", e)

        self._initialized = True
        logger.info(
            "RAGService initialized: mode=%s, rewrite=%s, rerank=%s",
            self._hybrid.mode(),
            self.settings.rag_rewrite_enabled,
            self.settings.rag_rerank_enabled,
        )

    async def ingest(self, doc: str) -> int:
        """Ingest a document: split → embed → index to PG/Milvus/ES."""
        return await self._engine.ingest(doc)

    async def search(
        self,
        query: str,
        history: Optional[List[HistoryMessage]] = None,
    ) -> Tuple[str, List[dict]]:
        """Search the knowledge base with optional history-aware rewriting."""
        return await self._engine.query_with_history(query, history)

    @property
    def engine(self) -> RAGEngine:
        return self._engine

    @property
    def hybrid(self) -> HybridStore:
        return self._hybrid

    async def delete_by_doc_hash(self, doc_hash: str) -> int:
        """Delete all RAG chunks with the given doc_hash from PG + ES + Milvus + KG.

        Returns the number of PG rows deleted.
        """
        from app.db.engine import get_db
        from app.db.models import RagChunk
        from sqlalchemy import select, delete, func

        # 1. Collect pg_ids before deleting (needed for ES/Milvus cleanup)
        async with get_db() as session:
            id_result = await session.execute(
                select(RagChunk.id).where(RagChunk.doc_hash == doc_hash)
            )
            pg_ids = [row[0] for row in id_result.all()]
            if not pg_ids:
                return 0

            # 2. Delete from PG
            result = await session.execute(
                delete(RagChunk).where(RagChunk.doc_hash == doc_hash)
            )
            deleted_count = result.rowcount or 0

        # 3. Delete from ES (best-effort)
        if self._es_delete_fn:
            try:
                await self._es_delete_fn(pg_ids)
            except Exception as e:
                logger.warning("ES delete by doc_hash failed: %s", e)

        # 4. Delete from Milvus (best-effort)
        if self._milvus_delete_fn:
            try:
                self._milvus_delete_fn(pg_ids)
            except Exception as e:
                logger.warning("Milvus delete by doc_hash failed: %s", e)

        # 5. Delete from KG (best-effort)
        if self._kg_delete_fn:
            try:
                await self._kg_delete_fn(doc_hash)
            except Exception as e:
                logger.warning("KG delete by doc_hash failed: %s", e)

        return deleted_count
