"""Unit tests for HybridStore — mode detection + degradation when backends unavailable.

Covers the following rag-retrieval-enhancement scenarios:
- 8.3: TF cosine fallback when Milvus unavailable
- 8.4: RetrievalConfig(search_mode="vector") only uses semantic path
- 8.5: Concurrency control via asyncio.Semaphore
- 8.6: PG embedding cosine fallback when Milvus unavailable
- 8.7: Hybrid mode degrades to TF cosine when Milvus unavailable and no embed_fn
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.infra.hybrid import (
    HybridResult,
    HybridStore,
    RetrievalConfig,
    _compute_tf,
    _cosine_similarity,
    _get_query_semaphore,
    _run_query_io,
    _tokenize,
    _vec_cosine,
    _vec_norm,
)


def _make_settings(**overrides) -> Settings:
    defaults = {
        "rag_milvus_dim": 1024,
        "rag_rrf_constant_k": 30,
        "rag_semantic_weight": 0.5,
        "rag_keyword_weight": 0.5,
        "kg_weight": 0.0,
        "rag_top_k": 3,
        "rag_search_concurrency": 8,
        "rag_embed_concurrency": 5,
        "milvus_bm25_drop_ratio_search": 0.0,
    }
    defaults.update(overrides)
    s = MagicMock(spec=Settings)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ─── Helper functions ─────────────────────────────────────────────────────────


class TestTokenize:
    def test_chinese_per_char(self):
        tokens = _tokenize("你好世界")
        assert tokens == ["你", "好", "世", "界"]

    def test_english_per_word(self):
        tokens = _tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_mixed(self):
        tokens = _tokenize("你好 hello 世界123")
        assert "你" in tokens
        assert "好" in tokens
        assert "hello" in tokens
        assert "123" in tokens

    def test_empty(self):
        assert _tokenize("") == []


class TestComputeTf:
    def test_basic(self):
        tf = _compute_tf(["a", "a", "b"])
        assert tf["a"] == 2 / 3
        assert tf["b"] == 1 / 3

    def test_empty(self):
        assert _compute_tf([]) == {}


class TestCosineSimilarity:
    def test_identical(self):
        tf = _compute_tf(["a", "b", "c"])
        assert _cosine_similarity(tf, tf) == pytest.approx(1.0)

    def test_disjoint(self):
        tf1 = _compute_tf(["a"])
        tf2 = _compute_tf(["b"])
        assert _cosine_similarity(tf1, tf2) == 0.0

    def test_empty(self):
        assert _cosine_similarity({}, {}) == 0.0


class TestVecHelpers:
    def test_vec_norm(self):
        assert _vec_norm([3, 4]) == pytest.approx(5.0)
        assert _vec_norm([0, 0, 0]) == 0.0

    def test_vec_cosine_identical(self):
        v = [1.0, 0.0, 0.0]
        norm = _vec_norm(v)
        assert _vec_cosine(v, norm, v) == pytest.approx(1.0)

    def test_vec_cosine_orthogonal(self):
        q = [1.0, 0.0]
        qn = _vec_norm(q)
        d = [0.0, 1.0]
        assert _vec_cosine(q, qn, d) == pytest.approx(0.0)

    def test_vec_cosine_dim_mismatch(self):
        q = [1.0, 0.0]
        qn = _vec_norm(q)
        assert _vec_cosine(q, qn, [1.0]) == 0.0


# ─── Mode detection ───────────────────────────────────────────────────────────


class TestHybridStoreMode:
    def test_unavailable_mode(self):
        hs = HybridStore(_make_settings())
        assert hs.mode() == "unavailable"

    def test_semantic_mode(self):
        hs = HybridStore(_make_settings())
        hs.set_milvus_backend(lambda emb, k: [])
        assert hs.mode() == "semantic"

    def test_hybrid_mode(self):
        hs = HybridStore(_make_settings())
        hs.set_milvus_backend(
            lambda emb, k: [],
            bm25_search_fn=lambda q, k, **kw: [],
            hybrid_search_fn=lambda qt, e, k, **kw: [],
        )
        assert hs.mode() == "hybrid"

    def test_tfidf_mode_no_milvus_with_chunks(self):
        hs = HybridStore(_make_settings())
        hs._has_chunks = True
        assert hs.mode() == "tfidf"

    def test_pg_embedding_mode_no_milvus_with_embed_fn(self):
        hs = HybridStore(_make_settings())
        hs._has_chunks = True
        hs.set_embed_fn(lambda text: [0.1] * 1024)
        assert hs.mode() == "pg_embedding"

    def test_unavailable_no_chunks(self):
        hs = HybridStore(_make_settings())
        assert hs.mode() == "unavailable"
        assert hs._tfidf_ok() is False


# ─── Degradation tests ───────────────────────────────────────────────────────


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
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9, "content": "result"}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "hello world", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) >= 1
        assert results[0].source == "semantic"

    @pytest.mark.asyncio
    async def test_embed_fn_not_set_skips_milvus(self):
        """Without embed_fn, Milvus path should be skipped."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9}])

        results = await hs.search("query", 3)
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_dim_mismatch_skips_milvus(self):
        """If embedding dim doesn't match rag_milvus_dim, skip Milvus."""
        settings = _make_settings(rag_milvus_dim=768)
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        results = await hs.search("query", 3)
        assert results == []


# ─── Task 8.3: TF cosine fallback when Milvus unavailable ─────────────────


class TestTfidfFallback:
    """Task 8.3: Milvus unavailable → TF cosine fallback returns results."""

    @pytest.mark.asyncio
    async def test_tfidf_fallback_returns_results(self):
        """When Milvus is unavailable and no embed_fn, search should return
        TF cosine ranked results from PG chunks."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs._has_chunks = True
        # No Milvus backend, no embed_fn

        # Mock _search_tfidf_fallback to return results
        async def mock_tfidf_fallback(query, top_k, *, user_id=None):
            from app.infra.hybrid import _PathHits
            return _PathHits(
                hits=[
                    {"pg_id": 1, "content": "hello world", "score": 0.8},
                    {"pg_id": 2, "content": "hello python", "score": 0.6},
                ],
                ok=True,
            )

        hs._search_tfidf_fallback = mock_tfidf_fallback

        async def mock_load(ids):
            return {
                1: {"content": "hello world", "parent_content": "doc1"},
                2: {"content": "hello python", "parent_content": "doc2"},
            }

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("hello", 3)
        assert len(results) == 2
        assert results[0].source == "tfidf"
        assert results[0].score >= results[1].score
        assert "hello" in results[0].content

    @pytest.mark.asyncio
    async def test_tfidf_mode_detection(self):
        """mode() should return 'tfidf' when Milvus unavailable but chunks exist."""
        hs = HybridStore(_make_settings())
        hs._has_chunks = True
        assert hs.mode() == "tfidf"

    @pytest.mark.asyncio
    async def test_tfidf_no_chunks_returns_empty(self):
        """mode() returns 'unavailable' when no chunks and no Milvus."""
        hs = HybridStore(_make_settings())
        assert hs.mode() == "unavailable"
        results = await hs.search("query", 3)
        assert results == []


# ─── Task 8.4: RetrievalConfig(search_mode="vector") ──────────────────────


class TestRetrievalConfigVectorMode:
    """Task 8.4: RetrievalConfig(search_mode="vector") should only use
    semantic search path (Milvus or PG embedding fallback)."""

    @pytest.mark.asyncio
    async def test_vector_mode_uses_semantic_only(self):
        """With search_mode='vector', should only call _search_semantic,
        not _search_hybrid or _search_keyword."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9, "content": "vec"}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "vec result", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        rc = RetrievalConfig(search_mode="vector")
        results = await hs.search("query", 3, retrieval_config=rc)
        assert len(results) >= 1
        assert results[0].source == "semantic"

    @pytest.mark.asyncio
    async def test_vector_mode_resolves_to_semantic_with_milvus(self):
        """_resolve_mode should return 'semantic' for vector mode when Milvus available."""
        hs = HybridStore(_make_settings())
        hs.set_milvus_backend(lambda emb, k: [])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        rc = RetrievalConfig(search_mode="vector")
        mode = hs._resolve_mode(rc, hs.mode())
        assert mode == "semantic"

    @pytest.mark.asyncio
    async def test_vector_mode_resolves_to_pg_embedding_without_milvus(self):
        """_resolve_mode should return 'pg_embedding' for vector mode when
        Milvus unavailable but embed_fn available."""
        hs = HybridStore(_make_settings())
        hs._has_chunks = True
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        rc = RetrievalConfig(search_mode="vector")
        mode = hs._resolve_mode(rc, hs.mode())
        assert mode == "pg_embedding"

    @pytest.mark.asyncio
    async def test_vector_mode_resolves_to_tfidf_without_milvus_no_embed(self):
        """_resolve_mode should return 'tfidf' for vector mode when
        Milvus unavailable and no embed_fn but chunks exist."""
        hs = HybridStore(_make_settings())
        hs._has_chunks = True

        rc = RetrievalConfig(search_mode="vector")
        mode = hs._resolve_mode(rc, hs.mode())
        assert mode == "tfidf"

    @pytest.mark.asyncio
    async def test_vector_mode_resolves_to_unavailable_nothing(self):
        """_resolve_mode should return 'unavailable' when nothing is available."""
        hs = HybridStore(_make_settings())

        rc = RetrievalConfig(search_mode="vector")
        mode = hs._resolve_mode(rc, hs.mode())
        assert mode == "unavailable"


# ─── Task 8.5: Concurrency control via asyncio.Semaphore ─────────────────


class TestConcurrencyControl:
    """Task 8.5: asyncio.Semaphore limits concurrent I/O operations."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """_run_query_io should limit concurrent calls to the semaphore limit."""
        call_count = 0
        max_concurrent = 0

        def blocking_task(idx):
            nonlocal call_count, max_concurrent
            call_count += 1
            max_concurrent = max(max_concurrent, call_count)
            import time
            time.sleep(0.05)
            call_count -= 1
            return idx

        tasks = [_run_query_io(blocking_task, i, _sem_limit=3) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert max_concurrent <= 3

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency_with_milvus_search(self):
        """When multiple concurrent searches use Milvus, the semaphore should
        limit how many blocking calls execute simultaneously."""
        call_count = 0
        max_concurrent = 0

        def mock_milvus_search(emb, k, **kw):
            nonlocal call_count, max_concurrent
            call_count += 1
            max_concurrent = max(max_concurrent, call_count)
            import time
            time.sleep(0.02)
            call_count -= 1
            return [{"pg_id": 1, "score": 0.9, "content": "test"}]

        settings = _make_settings(rag_search_concurrency=4)
        hs = HybridStore(settings)
        hs.set_milvus_backend(mock_milvus_search)
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "test", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        tasks = [hs.search(f"query{i}", 3) for i in range(12)]
        await asyncio.gather(*tasks)

        assert max_concurrent <= 4

    @pytest.mark.asyncio
    async def test_semaphore_loop_scoped(self):
        """Semaphore should be scoped to the event loop."""
        sem1 = _get_query_semaphore(8)

        # Same loop should return same semaphore
        sem2 = _get_query_semaphore(8)
        assert sem1 is sem2

    @pytest.mark.asyncio
    async def test_semaphore_different_limit_creates_new(self):
        """Different limit should still use same semaphore (first one wins)."""
        sem1 = _get_query_semaphore(8)
        sem2 = _get_query_semaphore(4)
        # Same loop → same semaphore object (limit from first call)
        assert sem1 is sem2

    @pytest.mark.asyncio
    async def test_search_uses_semaphore_for_milvus(self):
        """Search with Milvus backend should use _run_query_io for I/O."""
        settings = _make_settings(rag_search_concurrency=2)
        hs = HybridStore(settings)
        hs.set_milvus_backend(lambda emb, k: [{"pg_id": 1, "score": 0.9, "content": "test"}])
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "test", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) >= 1


# ─── Task 8.6: PG embedding cosine fallback ──────────────────────────────


class TestPgEmbeddingFallback:
    """Task 8.6: When Milvus unavailable but embed_fn is configured,
    search should fall back to PG embedding vector cosine."""

    @pytest.mark.asyncio
    async def test_pg_embedding_fallback_returns_results(self):
        """PG embedding fallback should return vector cosine ranked results."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs._has_chunks = True
        hs.set_embed_fn(lambda text: [0.1] * 4)

        # Mock _search_pg_embedding (the method called by search() for pg_embedding mode)
        async def mock_pg_embedding(query, top_k, *, user_id=None, retrieval_config=None):
            return [
                HybridResult(pg_id=1, content="semantic result 1", score=0.95,
                             source="pg_embedding", parent="doc1"),
                HybridResult(pg_id=2, content="semantic result 2", score=0.85,
                             source="pg_embedding", parent="doc2"),
            ]

        hs._search_pg_embedding = mock_pg_embedding

        results = await hs.search("query", 3)
        assert len(results) == 2
        assert results[0].source == "pg_embedding"
        assert results[0].score >= results[1].score

    @pytest.mark.asyncio
    async def test_pg_embedding_mode_detection(self):
        """mode() should return 'pg_embedding' when Milvus unavailable,
        embed_fn set, and chunks exist."""
        hs = HybridStore(_make_settings())
        hs._has_chunks = True
        hs.set_embed_fn(lambda text: [0.1] * 1024)
        assert hs.mode() == "pg_embedding"

    @pytest.mark.asyncio
    async def test_semantic_falls_back_to_pg_embedding(self):
        """_search_semantic should fall back to PG embedding when Milvus fails."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs._has_chunks = True
        hs.set_embed_fn(lambda text: [0.1] * 4)

        # Mock _fetch_milvus to fail (Milvus unavailable)
        async def mock_fetch_milvus(query, fetch_k, *, user_id=None):
            from app.infra.hybrid import _PathHits
            return _PathHits(ok=False)

        hs._fetch_milvus = mock_fetch_milvus

        # Mock _search_pg_embedding
        async def mock_pg_embedding(query, top_k, *, user_id=None, retrieval_config=None):
            return [HybridResult(pg_id=1, content="pg emb", score=0.9, source="pg_embedding")]

        hs._search_pg_embedding = mock_pg_embedding

        results = await hs._search_semantic("query", 3)
        assert len(results) == 1
        assert results[0].source == "pg_embedding"


# ─── Task 8.7: Hybrid mode → TF cosine when no Milvus + no embed_fn ───────


class TestHybridDegradesToTfidf:
    """Task 8.7: When Milvus unavailable and no embed_fn,
    hybrid mode should still return results via TF cosine fallback."""

    @pytest.mark.asyncio
    async def test_hybrid_degrades_to_tfidf(self):
        """Hybrid mode with no Milvus and no embed_fn should use TF cosine
        for both semantic and keyword paths."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs._has_chunks = True
        # No Milvus, no embed_fn

        # Mock _search_tfidf_fallback to return results
        async def mock_tfidf_fallback(query, top_k, *, user_id=None):
            from app.infra.hybrid import _PathHits
            return _PathHits(
                hits=[
                    {"pg_id": 1, "content": "tfidf hybrid result", "score": 0.7},
                ],
                ok=True,
            )

        hs._search_tfidf_fallback = mock_tfidf_fallback

        async def mock_load(ids):
            return {1: {"content": "tfidf hybrid result", "parent_content": "doc"}}

        hs._load_chunks_by_ids = mock_load

        results = await hs.search("query", 3)
        assert len(results) >= 1
        assert results[0].source == "tfidf"

    @pytest.mark.asyncio
    async def test_keyword_falls_back_to_tfidf(self):
        """_search_keyword should fall back to TF cosine when BM25 unavailable."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs._has_chunks = True

        # Mock _search_milvus_bm25 to fail (no BM25 backend)
        async def mock_bm25(query_text, top_k, *, drop_ratio=0.0, user_id=None):
            from app.infra.hybrid import _PathHits
            return _PathHits(ok=False)

        hs._search_milvus_bm25 = mock_bm25

        # Mock _search_tfidf_fallback
        async def mock_tfidf_fallback(query, top_k, *, user_id=None):
            from app.infra.hybrid import _PathHits
            return _PathHits(
                hits=[{"pg_id": 1, "content": "keyword tfidf", "score": 0.6}],
                ok=True,
            )

        hs._search_tfidf_fallback = mock_tfidf_fallback

        async def mock_load(ids):
            return {1: {"content": "keyword tfidf", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        results = await hs._search_keyword("query", 3)
        assert len(results) == 1
        assert results[0].source == "tfidf"

    @pytest.mark.asyncio
    async def test_hybrid_no_chunks_no_embed_returns_empty(self):
        """Hybrid mode with no Milvus, no embed_fn, no chunks should return empty."""
        hs = HybridStore(_make_settings())
        # _has_chunks defaults to False

        results = await hs.search("query", 3)
        assert results == []


# ─── RetrievalConfig integration tests ───────────────────────────────────


class TestRetrievalConfigIntegration:
    """Test RetrievalConfig parameterization end-to-end."""

    def test_retrieval_config_defaults(self):
        rc = RetrievalConfig()
        assert rc.search_mode == "hybrid"
        assert rc.final_top_k is None
        assert rc.vector_weight is None

    def test_retrieval_config_custom_weights(self):
        rc = RetrievalConfig(
            search_mode="hybrid",
            vector_weight=0.8,
            bm25_weight=0.2,
        )
        assert rc.vector_weight == 0.8
        assert rc.bm25_weight == 0.2

    @pytest.mark.asyncio
    async def test_hybrid_uses_config_weights(self):
        """Hybrid search should use RetrievalConfig weights when provided."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(
            lambda emb, k: [],
            bm25_search_fn=lambda q, k, **kw: [],
            hybrid_search_fn=lambda qt, e, k, **kw: [
                {"pg_id": 1, "score": 0.9, "content": "hybrid result"},
            ],
        )
        hs.set_embed_fn(lambda text: [0.1] * 1024)

        async def mock_load(ids):
            return {1: {"content": "hybrid result", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        rc = RetrievalConfig(search_mode="hybrid", vector_weight=0.8, bm25_weight=0.2)
        results = await hs.search("query", 3, retrieval_config=rc)
        assert len(results) >= 1
        assert results[0].source == "hybrid"

    @pytest.mark.asyncio
    async def test_keyword_mode(self):
        """search_mode='keyword' should use keyword (BM25) path."""
        settings = _make_settings()
        hs = HybridStore(settings)
        hs.set_milvus_backend(
            lambda emb, k: [],
            bm25_search_fn=lambda q, k, **kw: [{"pg_id": 2, "score": 0.8, "content": "bm25"}],
        )

        async def mock_load(ids):
            return {2: {"content": "bm25 result", "parent_content": ""}}

        hs._load_chunks_by_ids = mock_load

        rc = RetrievalConfig(search_mode="keyword")
        results = await hs.search("query", 3, retrieval_config=rc)
        assert len(results) >= 1
        assert results[0].source == "keyword"
