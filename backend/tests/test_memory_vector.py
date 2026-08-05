"""Test memory vector search — VectorIndex, MarkdownChunker, HybridSearch."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

def test_vector_index_add_search():
    """7.1: VectorIndex add → search → remove → search returns empty."""
    from app.memory.search.vector_index import VectorIndex

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "vectors.db"
        index = VectorIndex(db_path)
        index.initialize()

        # Add vectors
        index.add("file1.md", 0, "chunk 0 text", [0.1, 0.2, 0.3], "agent_1", "wiki")
        index.add("file1.md", 1, "chunk 1 text", [0.4, 0.5, 0.6], "agent_1", "wiki")
        index.add("file2.md", 0, "chunk 2 text", [0.7, 0.8, 0.9], "agent_1", "wiki")

        # Search
        results = index.search([0.1, 0.2, 0.3], top_k=5)
        assert len(results) == 3
        assert results[0][0] == "file1.md" and results[0][1] == 0  # best match

        # Remove
        index.remove("file1.md")
        results = index.search([0.1, 0.2, 0.3], top_k=5)
        assert len(results) == 1
        assert results[0][0] == "file2.md"

        # Clear
        index.clear()
        results = index.search([0.1, 0.2, 0.3], top_k=5)
        assert len(results) == 0

        # Count
        assert index.count() == 0

        index.close()


def test_vector_index_dimension_mismatch():
    """7.1: VectorIndex rejects dimension mismatch writes."""
    from app.memory.search.vector_index import VectorIndex

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "vectors.db"
        index = VectorIndex(db_path)
        index.initialize()

        # First vector sets dimension to 3
        index.add("file1.md", 0, "chunk text", [0.1, 0.2, 0.3])

        # Attempt to add 2-dim vector — should be rejected
        index.add("file1.md", 1, "chunk text", [0.4, 0.5])
        results = index.search([0.1, 0.2, 0.3], top_k=5)
        assert len(results) == 1  # Only the first vector should remain

        # Attempt to search with mismatched dimension
        results = index.search([0.1, 0.2], top_k=5)
        assert len(results) == 0  # No results for dim mismatch

        index.close()


def test_markdown_chunker_simple_headings():
    """7.2: MarkdownChunker — heading-based chunking."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import MemoryFile
    from app.memory.search.chunker import MarkdownChunker

    mem_file = MemoryFile(
        path="test.md",
        frontmatter=MemoryFrontmatter(name="Test Name", description="Test Desc"),
        body="""## 概述
段落1

## 详情
段落2
"""
    )
    chunker = MarkdownChunker(chunk_size=512, min_chunk_size=100)
    chunks = chunker.chunk(mem_file)
    assert len(chunks) == 2
    assert chunks[0].section_path == "概述"
    assert chunks[1].section_path == "详情"
    assert "Test Name\nTest Desc" in chunks[0].text


def test_markdown_chunker_nested_headings():
    """7.2: MarkdownChunker — nested heading breadcrumb."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import MemoryFile
    from app.memory.search.chunker import MarkdownChunker

    mem_file = MemoryFile(
        path="test.md",
        frontmatter=MemoryFrontmatter(name="Test", description=""),
        body="""## 前端框架
前端相关内容

### React 19
React 细节
"""
    )
    chunker = MarkdownChunker()
    chunks = chunker.chunk(mem_file)
    assert len(chunks) == 2
    assert chunks[0].section_path == "前端框架"
    assert chunks[1].section_path == "前端框架 > React 19"


def test_markdown_chunker_short_merge():
    """7.2: MarkdownChunker — short section merging."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import MemoryFile
    from app.memory.search.chunker import MarkdownChunker

    mem_file = MemoryFile(
        path="test.md",
        frontmatter=MemoryFrontmatter(),
        body="""## 短1
x

## 短2
y
"""
    )
    chunker = MarkdownChunker(min_chunk_size=10)
    chunks = chunker.chunk(mem_file)
    assert len(chunks) == 1  # Merged
    assert chunks[0].section_path == "短1"


def test_markdown_chunker_no_headings_fallback():
    """7.2: MarkdownChunker — no headings fallback."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import MemoryFile
    from app.memory.search.chunker import MarkdownChunker

    mem_file = MemoryFile(
        path="test.md",
        frontmatter=MemoryFrontmatter(),
        body="没有标题的正文\n第二行",
    )
    chunker = MarkdownChunker()
    chunks = chunker.chunk(mem_file)
    assert len(chunks) == 1
    assert chunks[0].section_path == ""


def test_hybrid_search_vector_and_bm25_rrf():
    """7.3: HybridSearch — two-way RRF fusion (mock embed_fn + vector_index with data)."""
    from app.memory.search.bm25_index import BM25Index
    from app.memory.search.hybrid_search import HybridSearch, SearchResult
    from app.memory.search.vector_index import VectorIndex
    from app.memory.search.wikilink_expander import WikilinkExpander
    from app.config import Settings

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Setup mocks
        settings = Settings()
        settings.memory_bm25_weight = 0.3
        settings.memory_vector_weight = 0.7
        settings.memory_search_top_k = 5
        settings.memory_rrf_k = 60

        bm25 = BM25Index(tmpdir / "bm25.db")
        bm25.initialize()
        vector = VectorIndex(tmpdir / "vectors.db")
        vector.initialize()
        expander = WikilinkExpander(tmpdir / "wikilinks.db")
        expander.initialize()

        # Mock embed_fn
        def embed(text: str):
            if "前端" in text:
                return [0.9, 0.5, 0.2]
            return [0.1, 0.2, 0.3]

        search = HybridSearch(
            settings, bm25, expander, workspace_root=tmpdir, vector_index=vector, embed_fn=embed,
        )

        # Add test files to BM25
        bm25.add("file1.md", "前端框架", "React 19 是前端框架", "agent_1", "wiki", [])
        bm25.add("file2.md", "后端框架", "FastAPI 是后端", "agent_1", "wiki", [])

        # Add vectors
        vector.add("file1.md", 0, "React 19 是前端框架", [0.9, 0.5, 0.2], "agent_1", "wiki")
        vector.add("file2.md", 0, "FastAPI 是后端", [0.1, 0.2, 0.3], "agent_1", "wiki")

        # Query
        results = search._vector_search("前端框架", 5, None, None)
        assert len(results) > 0

        # Search via hybrid
        async def _search():
            return await search.search("前端框架", top_k=5, agent_id=None, bucket=None)
        import asyncio
        results = asyncio.run(_search())
        found_names = [r.name for r in results]

        assert "前端框架" in found_names or "后端框架" in found_names

        for r in results:
            assert "bm25" in r.scores
            assert "vector" in r.scores
            assert "rrf" in r.scores
            assert r.scores["rrf"] == round(r.scores["bm25"] + r.scores["vector"], 6)


def test_hybrid_search_vector_only():
    """7.3: HybridSearch — vector-only hit scenario."""
    from app.memory.search.bm25_index import BM25Index
    from app.memory.search.hybrid_search import HybridSearch
    from app.memory.search.vector_index import VectorIndex
    from app.memory.search.wikilink_expander import WikilinkExpander
    from app.config import Settings

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        settings = Settings()
        settings.memory_search_top_k = 5
        settings.memory_rrf_k = 60

        bm25 = BM25Index(tmpdir / "bm25.db")
        bm25.initialize()
        vector = VectorIndex(tmpdir / "vectors.db")
        vector.initialize()
        expander = WikilinkExpander(tmpdir / "wikilinks.db")
        expander.initialize()

        def embed(text: str):
            return [0.9, 0.5, 0.2]

        search = HybridSearch(
            settings, bm25, expander, workspace_root=tmpdir, vector_index=vector, embed_fn=embed,
        )

        # Add vectors only, no BM25 (BM25 behaves as if file didn't exist)
        vector.add("semantic.md", 0, "语义相关但关键词不匹配", [0.9, 0.5, 0.2], "agent_1", "wiki")

        async def _search():
            return await search.search("语义查询", top_k=5)
        import asyncio
        results = asyncio.run(_search())

        assert len(results) == 0  # BM25 为空时返回空（避免只是有向量文件但不在 memory 上）


def test_hybrid_search_wikilink_post_processing_only():
    """7.4: HybridSearch — wikilink post-processing expansion still attached."""
    from app.memory.search.bm25_index import BM25Index
    from app.memory.search.hybrid_search import HybridSearch
    from app.memory.search.vector_index import VectorIndex
    from app.memory.search.wikilink_expander import WikilinkExpander
    from app.config import Settings

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        settings = Settings()
        settings.memory_search_top_k = 5
        settings.memory_rrf_k = 60

        bm25 = BM25Index(tmpdir / "bm25.db")
        bm25.initialize()
        vector = VectorIndex(tmpdir / "vectors.db")
        vector.initialize()
        expander = WikilinkExpander(tmpdir / "wikilinks.db")
        expander.initialize()

        # Add file and wikilink edge
        expander.add_edge_detailed("file1.md", [("file2.md", "related_to")])
        bm25.add("file1.md", "关键词", "内容", "agent_1", "wiki", [])

        def embed(text: str):
            return [0.1, 0.2, 0.3]

        search = HybridSearch(
            settings, bm25, expander, workspace_root=tmpdir, vector_index=vector, embed_fn=embed,
        )

        async def _search():
            return await search.search("关键词", top_k=5)
        import asyncio
        results = asyncio.run(_search())

        # The search result should have expansion metadata
        assert len(results) >= 1
        found_expansion = False
        for r in results:
            if r.expansion["outlinks"] or r.expansion["inlinks"]:
                found_expansion = True
                # file2.md should NOT appear as a separate search result (only in expansion)
                break
        assert found_expansion


def test_memory_service_embed_fn_injection():
    """7.6: MemoryService — set_embed_fn enables two-way search."""
    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings()
    service = MemoryService(settings)

    def embed(text: str):
        return [0.1, 0.2, 0.3]

    # Test injection
    service.set_embed_fn(embed)
    assert service._search is None

    # After initialize, embed_fn should propagate to HybridSearch and AutoIndex
    import asyncio
    async def _test():
        await service.initialize()
        assert service._search is not None
        assert service.auto_index._embed_fn is not None
        await service.close()
    asyncio.run(_test())
