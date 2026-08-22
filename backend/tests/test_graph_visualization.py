"""Tests for graph visualization API: stats, subgraph, labels.

Tests cover:
- GET /api/graph/stats returns correct structure
- GET /api/graph/stats returns empty when Neo4j unavailable
- GET /api/graph/subgraph returns nodes and edges
- GET /api/graph/subgraph with keyword filter
- GET /api/graph/subgraph with exclude_chunk=true
- GET /api/graph/subgraph with max_depth=3
- GET /api/graph/subgraph returns empty when Neo4j unavailable
- GET /api/graph/labels returns label list
- GET /api/graph/labels returns empty when Neo4j unavailable
- User isolation: user A cannot see user B's graph data
- max_nodes limit is enforced
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.extractor import Extractor
from app.graph.kgstore import KGStore


def _make_mock_driver():
    """Create a mock Neo4j AsyncDriver with session/run/data chain."""
    mock_driver = AsyncMock()
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_driver.session = MagicMock(return_value=mock_session)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_driver


def _make_kg_store(user_id="user1", driver=None):
    """Create a KGStore instance with mock driver."""
    settings = MagicMock()
    settings.kg_max_hops = 2
    settings.kg_weight = 1.0
    extractor = Extractor(None)
    d = driver or _make_mock_driver()
    return KGStore(settings, d, extractor, user_id=user_id)


def _make_mock_neo4j_node(node_id, labels, properties):
    """Create a mock Neo4j Node object."""
    node = MagicMock()
    node.element_id = node_id
    node.labels = labels
    node._properties = properties
    # Also expose properties as dict-like
    for k, v in properties.items():
        setattr(node, k, v)
    return node


def _make_mock_neo4j_rel(rel_id, rel_type, start_node, end_node, properties):
    """Create a mock Neo4j Relationship object."""
    rel = MagicMock()
    rel.element_id = rel_id
    rel.type = rel_type
    rel.start_node = start_node
    rel.end_node = end_node
    rel._properties = properties
    return rel


# ─── Cypher template tests ──────────────────────────────────────────────────


class TestCypherVisualizationTemplates:
    def test_cypher_graph_stats_nodes_contains_label(self):
        from app.graph.graph_utils import cypher_graph_stats_nodes
        query = cypher_graph_stats_nodes("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "count" in query

    def test_cypher_graph_stats_edges_contains_label(self):
        from app.graph.graph_utils import cypher_graph_stats_edges
        query = cypher_graph_stats_edges("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "count" in query

    def test_cypher_graph_stats_entity_types_contains_label(self):
        from app.graph.graph_utils import cypher_graph_stats_entity_types
        query = cypher_graph_stats_entity_types("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "entity_label" in query

    def test_cypher_graph_subgraph_contains_label(self):
        from app.graph.graph_utils import cypher_graph_subgraph
        query = cypher_graph_subgraph("u_abc123", 2, False)
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$keyword" in query
        assert "$limit" in query

    def test_cypher_graph_subgraph_exclude_chunk(self):
        from app.graph.graph_utils import cypher_graph_subgraph
        query = cypher_graph_subgraph("u_abc123", 2, True)
        assert "NOT n:Chunk" in query

    def test_cypher_graph_subgraph_no_exclude_chunk(self):
        from app.graph.graph_utils import cypher_graph_subgraph
        query = cypher_graph_subgraph("u_abc123", 2, False)
        assert "NOT n:Chunk" not in query

    def test_cypher_graph_subgraph_max_depth(self):
        from app.graph.graph_utils import cypher_graph_subgraph
        query = cypher_graph_subgraph("u_abc123", 5, False)
        assert "*1..5" in query

    def test_cypher_graph_labels_contains_label(self):
        from app.graph.graph_utils import cypher_graph_labels
        query = cypher_graph_labels("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "node_label" in query
        assert "Entity" not in query.replace("MATCH (n:Entity:UserKG", "")


# ─── KGStore.get_stats tests ────────────────────────────────────────────────


class TestKGStoreGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_correct_structure(self):
        """Test 8.1.1: GET /api/graph/stats returns correct structure."""
        kg = _make_kg_store("user1")

        # Mock _run_cypher to return different results for 3 queries
        call_count = [0]
        async def mock_run(query, params):
            call_count[0] += 1
            if call_count[0] == 1:  # nodes count
                return [{"count": 100}]
            elif call_count[0] == 2:  # edges count
                return [{"count": 200}]
            else:  # entity types
                return [
                    {"entity_label": "Concept", "count": 60},
                    {"entity_label": "Person", "count": 40},
                ]
        kg._run_cypher = mock_run

        result = await kg.get_stats()
        assert result["total_nodes"] == 100
        assert result["total_edges"] == 200
        assert len(result["entity_types"]) == 2
        assert result["entity_types"][0]["type"] == "Concept"
        assert result["entity_types"][0]["count"] == 60
        assert result["entity_types"][1]["type"] == "Person"
        assert result["entity_types"][1]["count"] == 40

    @pytest.mark.asyncio
    async def test_get_stats_returns_empty_when_neo4j_unavailable(self):
        """Test 8.1.2: GET /api/graph/stats returns empty when Neo4j unavailable."""
        kg = _make_kg_store("user1", driver=None)
        result = await kg.get_stats()
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert result["entity_types"] == []

    @pytest.mark.asyncio
    async def test_get_stats_returns_empty_on_error(self):
        """Test get_stats returns empty on Neo4j error."""
        kg = _make_kg_store("user1")
        async def mock_run(query, params):
            raise Exception("Neo4j connection error")
        kg._run_cypher = mock_run

        result = await kg.get_stats()
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert result["entity_types"] == []


# ─── KGStore.query_subgraph tests ──────────────────────────────────────────


class TestKGStoreQuerySubgraph:
    @pytest.mark.asyncio
    async def test_query_subgraph_returns_nodes_and_edges(self):
        """Test 8.1.3: GET /api/graph/subgraph returns nodes and edges."""
        kg = _make_kg_store("user1")

        node1 = _make_mock_neo4j_node("n1", ["Entity", "UserKG", "u_abc"], {"name": "React", "label": "Concept"})
        node2 = _make_mock_neo4j_node("n2", ["Entity", "UserKG", "u_abc"], {"name": "Vue", "label": "Concept"})
        rel1 = _make_mock_neo4j_rel("r1", "RELATION", node1, node2, {"doc_hash": "doc1"})

        async def mock_run(query, params):
            return [{"nodes": [node1, node2], "edges": [rel1]}]
        kg._run_cypher = mock_run

        result = await kg.query_subgraph("*", 2, 50, False)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["name"] == "React"
        assert result["edges"][0]["type"] == "RELATION"

    @pytest.mark.asyncio
    async def test_query_subgraph_with_keyword_filter(self):
        """Test 8.1.4: GET /api/graph/subgraph with keyword filter."""
        kg = _make_kg_store("user1")

        captured_params = {}
        async def mock_run(query, params):
            captured_params.update(params)
            return [{"nodes": [], "edges": []}]
        kg._run_cypher = mock_run

        await kg.query_subgraph("React", 2, 50, False)
        assert captured_params["keyword"] == "React"

    @pytest.mark.asyncio
    async def test_query_subgraph_with_exclude_chunk(self):
        """Test 8.1.5: GET /api/graph/subgraph with exclude_chunk=true."""
        kg = _make_kg_store("user1")

        captured_query = ""
        async def mock_run(query, params):
            nonlocal captured_query
            captured_query = query
            return [{"nodes": [], "edges": []}]
        kg._run_cypher = mock_run

        await kg.query_subgraph("*", 2, 50, True)
        assert "NOT n:Chunk" in captured_query

    @pytest.mark.asyncio
    async def test_query_subgraph_with_max_depth_3(self):
        """Test 8.1.6: GET /api/graph/subgraph with max_depth=3."""
        kg = _make_kg_store("user1")

        captured_query = ""
        async def mock_run(query, params):
            nonlocal captured_query
            captured_query = query
            return [{"nodes": [], "edges": []}]
        kg._run_cypher = mock_run

        await kg.query_subgraph("*", 3, 50, False)
        assert "*1..3" in captured_query

    @pytest.mark.asyncio
    async def test_query_subgraph_returns_empty_when_neo4j_unavailable(self):
        """Test 8.1.7: GET /api/graph/subgraph returns empty when Neo4j unavailable."""
        kg = _make_kg_store("user1", driver=None)
        result = await kg.query_subgraph("*", 2, 50, False)
        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_query_subgraph_returns_empty_on_error(self):
        """Test query_subgraph returns empty on Neo4j error."""
        kg = _make_kg_store("user1")
        async def mock_run(query, params):
            raise Exception("Neo4j connection error")
        kg._run_cypher = mock_run

        result = await kg.query_subgraph("*", 2, 50, False)
        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_query_subgraph_max_nodes_limit_enforced(self):
        """Test 8.1.11: max_nodes limit is enforced."""
        kg = _make_kg_store("user1")

        # Create more nodes than max_nodes allows
        nodes = []
        for i in range(100):
            n = _make_mock_neo4j_node(
                f"n{i}", ["Entity", "UserKG", "u_abc"],
                {"name": f"Entity{i}", "label": "Concept"},
            )
            nodes.append(n)

        async def mock_run(query, params):
            return [{"nodes": nodes, "edges": []}]
        kg._run_cypher = mock_run

        result = await kg.query_subgraph("*", 2, 10, False)
        assert len(result["nodes"]) <= 10

    @pytest.mark.asyncio
    async def test_query_subgraph_deduplicates_nodes(self):
        """Test that duplicate nodes (same element_id) are deduplicated."""
        kg = _make_kg_store("user1")

        node1 = _make_mock_neo4j_node("n1", ["Entity", "UserKG", "u_abc"], {"name": "React", "label": "Concept"})

        async def mock_run(query, params):
            # Return the same node twice (from different paths)
            return [{"nodes": [node1, node1], "edges": []}]
        kg._run_cypher = mock_run

        result = await kg.query_subgraph("*", 2, 50, False)
        assert len(result["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_query_subgraph_filters_edges_to_valid_nodes(self):
        """Test that edges are filtered to only include valid node pairs."""
        kg = _make_kg_store("user1")

        node1 = _make_mock_neo4j_node("n1", ["Entity", "UserKG", "u_abc"], {"name": "React", "label": "Concept"})
        node2 = _make_mock_neo4j_node("n2", ["Entity", "UserKG", "u_abc"], {"name": "Vue", "label": "Concept"})
        node3 = _make_mock_neo4j_node("n3", ["Entity", "UserKG", "u_abc"], {"name": "Angular", "label": "Concept"})

        # Edge from n1 to n2 (both in result) and n1 to n3 (n3 not in result due to max_nodes)
        rel1 = _make_mock_neo4j_rel("r1", "RELATION", node1, node2, {})
        rel2 = _make_mock_neo4j_rel("r2", "RELATION", node1, node3, {})

        async def mock_run(query, params):
            return [{"nodes": [node1, node2, node3], "edges": [rel1, rel2]}]
        kg._run_cypher = mock_run

        # max_nodes=2, so only 2 nodes will be kept
        result = await kg.query_subgraph("*", 2, 2, False)
        assert len(result["nodes"]) == 2
        # Edges should only include those where both endpoints are in the node set
        for edge in result["edges"]:
            assert edge["source_id"] in {n["id"] for n in result["nodes"]}
            assert edge["target_id"] in {n["id"] for n in result["nodes"]}


# ─── KGStore.get_labels tests ───────────────────────────────────────────────


class TestKGStoreGetLabels:
    @pytest.mark.asyncio
    async def test_get_labels_returns_label_list(self):
        """Test 8.1.8: GET /api/graph/labels returns label list."""
        kg = _make_kg_store("user1")

        async def mock_run(query, params):
            return [
                {"label": "Concept"},
                {"label": "Person"},
                {"label": "Organization"},
            ]
        kg._run_cypher = mock_run

        result = await kg.get_labels()
        assert len(result) == 3
        assert "Concept" in result
        assert "Person" in result
        assert "Organization" in result

    @pytest.mark.asyncio
    async def test_get_labels_returns_empty_when_neo4j_unavailable(self):
        """Test 8.1.9: GET /api/graph/labels returns empty when Neo4j unavailable."""
        kg = _make_kg_store("user1", driver=None)
        result = await kg.get_labels()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_labels_returns_empty_on_error(self):
        """Test get_labels returns empty on Neo4j error."""
        kg = _make_kg_store("user1")
        async def mock_run(query, params):
            raise Exception("Neo4j connection error")
        kg._run_cypher = mock_run

        result = await kg.get_labels()
        assert result == []


# ─── Node/Edge normalization tests ─────────────────────────────────────────


class TestNormalizeNodeEdge:
    def test_normalize_node_entity(self):
        """Test _normalize_node for Entity nodes."""
        kg = _make_kg_store("user1")
        raw = _make_mock_neo4j_node(
            "e1", ["Entity", "UserKG", "u_abc"],
            {"name": "React", "label": "Concept", "entity_id": "hash1"},
        )
        result = kg._normalize_node(raw)
        assert result["id"] == "e1"
        assert result["name"] == "React"
        assert result["type"] == "Concept"
        assert "Entity" in result["labels"]
        assert "u_abc" not in result["labels"]
        assert result["properties"]["entity_id"] == "hash1"

    def test_normalize_node_chunk(self):
        """Test _normalize_node for Chunk nodes."""
        kg = _make_kg_store("user1")
        raw = _make_mock_neo4j_node(
            "c1", ["Chunk", "UserKG", "u_abc"],
            {"chunk_id": 1, "doc_hash": "doc1", "content_preview": "preview..."},
        )
        result = kg._normalize_node(raw)
        assert result["id"] == "c1"
        assert result["type"] == "Chunk"
        assert result["name"] == "preview..."

    def test_normalize_edge(self):
        """Test _normalize_edge for relationships."""
        kg = _make_kg_store("user1")
        node1 = _make_mock_neo4j_node("n1", ["Entity"], {"name": "A"})
        node2 = _make_mock_neo4j_node("n2", ["Entity"], {"name": "B"})
        raw = _make_mock_neo4j_rel("r1", "RELATION", node1, node2, {"doc_hash": "doc1"})
        result = kg._normalize_edge(raw)
        assert result["id"] == "r1"
        assert result["source_id"] == "n1"
        assert result["target_id"] == "n2"
        assert result["type"] == "RELATION"
        assert result["properties"]["doc_hash"] == "doc1"


# ─── User isolation tests ───────────────────────────────────────────────────


class TestUserIsolation:
    @pytest.mark.asyncio
    async def test_user_isolation_different_users(self):
        """Test 8.1.10: user A cannot see user B's graph data."""
        kg1 = _make_kg_store("userA")
        kg2 = _make_kg_store("userB")

        # Both query stats, but with different user_labels
        captured_queries = []

        async def mock_run_1(query, params):
            captured_queries.append(query)
            return [{"count": 50}]
        kg1._run_cypher = mock_run_1

        async def mock_run_2(query, params):
            captured_queries.append(query)
            return [{"count": 100}]
        kg2._run_cypher = mock_run_2

        await kg1.get_stats()
        await kg2.get_stats()

        # The first 3 queries should use user A's label, the last 3 should use user B's
        label_a = kg1._user_label
        label_b = kg2._user_label
        assert label_a != label_b
        assert label_a in captured_queries[0]
        assert label_b in captured_queries[3]

    @pytest.mark.asyncio
    async def test_user_isolation_subgraph(self):
        """Test that subgraph queries use correct user label."""
        kg = _make_kg_store("user-test-123")
        captured_query = ""

        async def mock_run(query, params):
            nonlocal captured_query
            captured_query = query
            return [{"nodes": [], "edges": []}]
        kg._run_cypher = mock_run

        await kg.query_subgraph("*", 2, 50, False)
        assert kg._user_label in captured_query

    @pytest.mark.asyncio
    async def test_user_isolation_labels(self):
        """Test that labels queries use correct user label."""
        kg = _make_kg_store("user-test-456")
        captured_query = ""

        async def mock_run(query, params):
            nonlocal captured_query
            captured_query = query
            return []
        kg._run_cypher = mock_run

        await kg.get_labels()
        assert kg._user_label in captured_query

    @pytest.mark.asyncio
    async def test_set_user_id_updates_label(self):
        """Test that set_user_id updates the user label."""
        kg = _make_kg_store("user1")
        label1 = kg._user_label
        kg.set_user_id("user2")
        label2 = kg._user_label
        assert label1 != label2
