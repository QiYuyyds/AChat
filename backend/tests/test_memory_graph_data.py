"""Unit tests for MemoryService.get_graph_data() — graph data aggregation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
async def memory_svc(tmp_path: Path):
    """Create and initialize a MemoryService with an isolated workspace."""
    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings(memory_workspace_dir=str(tmp_path / "memory"))
    svc = MemoryService(settings)
    await svc.initialize()
    yield svc


def _seed_graph_files(svc) -> None:
    """Seed memory files with wikilinks for graph testing."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    ws = svc.workspace

    # Wiki files with wikilinks
    write_markdown(
        ws.digest_path("wiki", "python"),
        MemoryFrontmatter(name="Python 基础", description="Python 语言核心概念", bucket="wiki", tags=["python"], importance=0.8),
        "Python is a language. [[asyncio]] [[threading]]",
    )
    write_markdown(
        ws.digest_path("wiki", "asyncio"),
        MemoryFrontmatter(name="AsyncIO", description="Async IO in Python", bucket="wiki", tags=["async", "python"], importance=0.7),
        "AsyncIO is a module. [[python]]",
    )
    write_markdown(
        ws.digest_path("wiki", "threading"),
        MemoryFrontmatter(name="Threading", description="Threading module", bucket="wiki", tags=["thread", "python"], importance=0.6),
        "Threading module. [[python]]",
    )
    write_markdown(
        ws.digest_path("procedure", "deploy-guide"),
        MemoryFrontmatter(name="Deploy Guide", description="How to deploy", bucket="procedure", tags=["deploy"], importance=0.9),
        "Deploy steps. [[python]]",
    )

    # Reindex to populate wikilinks table
    svc.auto_index.full_reindex()


class TestGetGraphData:
    """Tests for MemoryService.get_graph_data()."""

    def test_empty_graph(self, memory_svc):
        """No wikilinks → empty nodes and edges."""
        result = memory_svc.get_graph_data()
        assert result == {"nodes": [], "edges": []}

    def test_full_graph(self, memory_svc):
        """All edges and nodes returned with frontmatter + degree."""
        _seed_graph_files(memory_svc)
        result = memory_svc.get_graph_data()

        assert len(result["nodes"]) == 4
        assert len(result["edges"]) >= 4

        # Check node structure
        python_node = next(n for n in result["nodes"] if "python" in n["path"])
        assert python_node["name"] == "Python 基础"
        assert python_node["bucket"] == "wiki"
        assert python_node["degree"] >= 3  # asyncio→python, threading→python, deploy-guide→python, python→asyncio, python→threading

        # Check edge structure
        edge = result["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "predicate" in edge

    def test_bucket_filter(self, memory_svc):
        """Filter by bucket=wiki — only wiki nodes and edges between them."""
        _seed_graph_files(memory_svc)
        result = memory_svc.get_graph_data(bucket="wiki")

        wiki_paths = {n["path"] for n in result["nodes"]}
        assert all("wiki" in p for p in wiki_paths)
        # deploy-guide (procedure) should be excluded
        assert not any("procedure" in p for p in wiki_paths)
        # Edges should only connect wiki nodes
        for edge in result["edges"]:
            assert edge["source"] in wiki_paths
            assert edge["target"] in wiki_paths

    def test_min_degree_filter(self, memory_svc):
        """Filter by min_degree=2 — only nodes with degree >= 2."""
        _seed_graph_files(memory_svc)
        result = memory_svc.get_graph_data(min_degree=2)

        for node in result["nodes"]:
            assert node["degree"] >= 2
        # Edges should only connect high-degree nodes
        node_paths = {n["path"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["source"] in node_paths
            assert edge["target"] in node_paths
