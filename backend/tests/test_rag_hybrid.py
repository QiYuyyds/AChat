"""Unit tests for HybridStore — mode detection + degradation when backends unavailable."""

import pytest
from unittest.mock import MagicMock

from app.config import Settings
from app.infra.hybrid import HybridStore, HybridResult


def _make_settings(**overrides) -> Settings:
    defaults = {
        "rag_milvus_dim": 1024,
        "rag_rrf_constant_k": 60,
        "rag_semantic_weight": 0.7,
        "kg_weight": 0.3,
        "rag_top_k": 3,
    }
    defaults.update(overrides)
    s = MagicMock(spec=Settings)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestHybridStoreMode:
    def test_unavailable_mode(self):
        hs = HybridStore(_make_settings())
        assert hs.mode() == "unavailable"

    def test_semantic_mode(self):
        hs = HybridStore(_make_settings())
        hs.set_milvus_backend(lambda emb, k: [])
        assert hs.mode() == "semantic"

    def test_keyword_mode(self):
        hs = HybridStore(_make_settings())
        hs.set_es_backend(lambda q, k: [])
        assert hs.mode() == "keyword"

    def test_hybrid_mode(self):
        hs = HybridStore(_make_settings())
        hs.set_milvus_backend(lambda emb, k: [])
        hs.set_es_backend(lambda q, k: [])
        assert hs.mode() == "hybrid"


class TestHybridStoreDegradation:
    @pytest.mark.asyncio
    async def test_search_unavailable(self):
        hs = HybridStore(_make_settings())
        results = await hs.search("query", 3)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_multi_unavailable(self):
        hs = HybridStore(_make_settings())
        results = await hs.search_multi(["q1", "q2"], 3)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_semantic_only(self):
        """When only Milvus available, should use semantic search."""
        settings = _make_settings()
        hs = HybridStore(settings)
        # Milvus returns one hit
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9, "content": "result"}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        # Mock PG load to return content
        async def mock_load(ids):
            return {1: {"content": "hello world", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) >= 1
        assert results[0].source == "semantic"

    @pytest.mark.asyncio
    async def test_search_keyword_only(self):
        """When only ES available, should use keyword search."""
        settings = _make_settings()
        hs = HybridStore(settings)
        async def es_search(q, k):
            return [{"pg_id": 2, "score": 0.8, "content": "es result"}]
        hs.set_es_backend(es_search)

        async def mock_load(ids):
            return {2: {"content": "es content", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) >= 1
        assert results[0].source == "keyword"

    @pytest.mark.asyncio
    async def test_hybrid_rrf_fusion(self):
        """Both paths should produce RRF-fused results."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9}])
        async def es_search(q, k):
            return [{"pg_id": 1, "score": 0.8}]
        hs.set_es_backend(es_search)
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "shared result", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) == 1
        assert results[0].source == "hybrid"
        # Score should be > 0 (RRF fused)
        assert results[0].score > 0

    @pytest.mark.asyncio
    async def test_embed_fn_not_set_skips_milvus(self):
        """Without embed_fn, Milvus path should be skipped."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9}])
        # No embed_fn set

        results = await hs.search("query", 3)
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_dim_mismatch_skips_milvus(self):
        """If embedding dim doesn't match rag_milvus_dim, skip Milvus."""
        settings = _make_settings(rag_milvus_dim=768)
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)  # 1024 != 768

        results = await hs.search("query", 3)
        assert results == []
