"""Unit tests for WikilinkExpander — get_all_edges + edge operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory.search.wikilink_expander import WikilinkExpander


@pytest.fixture
def expander(tmp_path: Path) -> WikilinkExpander:
    """Create and initialize a WikilinkExpander with a temp DB."""
    exp = WikilinkExpander(tmp_path / "wikilinks.db")
    exp.initialize()
    return exp


class TestGetAllEdges:
    """Tests for WikilinkExpander.get_all_edges()."""

    def test_empty_table(self, expander: WikilinkExpander):
        edges = expander.get_all_edges()
        assert edges == []

    def test_single_edge_no_predicate(self, expander: WikilinkExpander):
        expander.add_edges("digest/wiki/python.md", ["digest/wiki/asyncio.md"])
        edges = expander.get_all_edges()
        assert len(edges) == 1
        assert edges[0]["source"] == "digest/wiki/python.md"
        assert edges[0]["target"] == "digest/wiki/asyncio.md"
        assert edges[0]["predicate"] is None

    def test_multiple_edges_with_predicate(self, expander: WikilinkExpander):
        expander.add_edges_detailed(
            "digest/wiki/python.md",
            [
                ("digest/wiki/asyncio.md", "derived_from"),
                ("digest/wiki/threading.md", None),
                ("digest/wiki/gil.md", "related_to"),
            ],
        )
        edges = expander.get_all_edges()
        assert len(edges) == 3
        sources = {e["source"] for e in edges}
        assert sources == {"digest/wiki/python.md"}
        predicates = {e["predicate"] for e in edges}
        assert predicates == {"derived_from", None, "related_to"}

    def test_no_connection_returns_empty(self):
        exp = WikilinkExpander(Path("/nonexistent/wikilinks.db"))
        assert exp.get_all_edges() == []
