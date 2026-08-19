"""Unit tests for MilvusGraphVectorStore + GraphBuildTask + GraphRetrieval integration.

These tests verify:
- 11.6: MilvusGraphVectorStore upsert + search code paths (with mock MilvusClient)
- 11.7: GraphRetrieval Milvus entity recall → Neo4j PPR search code path (with mock backends)
"""
from unittest.mock import MagicMock, patch

import pytest

from app.graph.types import ChunkRef, Entity, ExtractResult, Relation
from app.rag.graph_build_task import GraphBuildTask, _make_entity_id, _make_triple_id
from app.rag.graph_retrieval import GraphRetrieval
from app.rag.milvus_graph_vector_store import MilvusGraphVectorStore


class TestMilvusGraphVectorStore:
    """Test MilvusGraphVectorStore with a mock MilvusClient."""

    def setup_method(self):
        """Reset class state before each test."""
        MilvusGraphVectorStore._client = None
        MilvusGraphVectorStore._dim = 0
        MilvusGraphVectorStore._initialized = False

    @pytest.mark.asyncio
    async def test_unavailable_returns_empty(self):
        """When MilvusClient not injected, all ops should be no-ops."""
        assert not MilvusGraphVectorStore.available()

        await MilvusGraphVectorStore.upsert_entities([{"id": "1"}])
        await MilvusGraphVectorStore.upsert_triples([{"id": "1"}])

        assert await MilvusGraphVectorStore.search_entities("q", [0.1] * 4, 5) == []
        assert await MilvusGraphVectorStore.search_triples("q", [0.1] * 4, 5) == []

    @pytest.mark.asyncio
    async def test_upsert_and_search_with_mock_client(self):
        """Test entity/triple upsert + search with a mock MilvusClient."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = False

        MilvusGraphVectorStore.set_client(mock_client, dim=4)
        assert MilvusGraphVectorStore.available()

        # Ensure collections are created
        MilvusGraphVectorStore._ensure_collections()
        assert mock_client.create_collection.call_count == 2
        assert mock_client.create_index.call_count == 2

        # Upsert entities
        entities = [
            {"id": "ent1", "content": "Apple (Organization)", "embedding": [0.1, 0.2, 0.3, 0.4]},
        ]
        await MilvusGraphVectorStore.upsert_entities(entities)
        mock_client.upsert.assert_called_once()
        call_args = mock_client.upsert.call_args
        assert call_args[0][0] == "rag_graph_entities"

        # Upsert triples
        mock_client.upsert.reset_mock()
        triples = [
            {"id": "tri1", "content": "Apple RELATES_TO Banana", "embedding": [0.1, 0.2, 0.3, 0.4]},
        ]
        await MilvusGraphVectorStore.upsert_triples(triples)
        mock_client.upsert.assert_called_once()
        call_args = mock_client.upsert.call_args
        assert call_args[0][0] == "rag_graph_triples"

    @pytest.mark.asyncio
    async def test_search_entities_with_mock_results(self):
        """Test search_entities returns parsed results from mock."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.hybrid_search.return_value = [[
            {"id": "ent1", "distance": 0.95, "entity": {"content": "Apple (Organization)", "pg_id": 42}},
        ]]

        MilvusGraphVectorStore.set_client(mock_client, dim=4)

        results = await MilvusGraphVectorStore.search_entities("apple", [0.1, 0.2, 0.3, 0.4], 5)
        assert len(results) == 1
        assert results[0]["content"] == "Apple (Organization)"
        assert results[0]["pg_id"] == 42
        assert results[0]["score"] == 0.95


class TestGraphBuildTaskMilvusIntegration:
    """Test GraphBuildTask._upsert_to_milvus method."""

    def setup_method(self):
        """Reset class state before each test."""
        GraphBuildTask._kg_store = None
        GraphBuildTask._extractor = None
        GraphBuildTask._embed_fn = None
        MilvusGraphVectorStore._client = None
        MilvusGraphVectorStore._dim = 0
        MilvusGraphVectorStore._initialized = False

    @pytest.mark.asyncio
    async def test_upsert_to_milvus_with_embed_fn(self):
        """Test _upsert_to_milvus generates entity/triple dicts and calls upsert."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True

        MilvusGraphVectorStore.set_client(mock_client, dim=4)
        # Force _initialized to skip collection creation in test
        MilvusGraphVectorStore._initialized = True

        # Mock embed_fn
        def mock_embed(text):
            return [0.1, 0.2, 0.3, 0.4]

        GraphBuildTask._embed_fn = mock_embed

        # Patch settings to match mock embedding dim
        with patch('app.rag.graph_build_task.get_settings') as mock_settings:
            mock_settings.return_value.rag_milvus_dim = 4

            # Create test data
            doc_hash = "test_hash_123"
            chunk_ref = ChunkRef(id=0, pg_id=100, content="Apple is a company")
            result = ExtractResult(
                entities=[Entity(name="Apple", type="Organization")],
                relations=[Relation(from_name="Apple", to_name="Steve Jobs", rel_type="RELATES_TO")],
            )

            await GraphBuildTask._upsert_to_milvus(doc_hash, chunk_ref, result)

        # Verify upsert was called for both entities and triples
        assert mock_client.upsert.call_count == 2

        # Check entity upsert
        entity_call = mock_client.upsert.call_args_list[0]
        assert entity_call[0][0] == "rag_graph_entities"
        entity_data = entity_call[0][1]
        assert len(entity_data) == 1
        assert entity_data[0]["content"] == "Apple (Organization)"
        assert entity_data[0]["entity_type"] == "Organization"
        assert entity_data[0]["pg_id"] == 100

        # Check triple upsert
        triple_call = mock_client.upsert.call_args_list[1]
        assert triple_call[0][0] == "rag_graph_triples"
        triple_data = triple_call[0][1]
        assert len(triple_data) == 1
        assert triple_data[0]["content"] == "Apple RELATES_TO Steve Jobs"

    @pytest.mark.asyncio
    async def test_upsert_to_milvus_no_embed_fn(self):
        """When embed_fn is not injected, _upsert_to_milvus should be a no-op."""
        mock_client = MagicMock()
        MilvusGraphVectorStore.set_client(mock_client, dim=4)

        GraphBuildTask._embed_fn = None

        chunk_ref = ChunkRef(id=0, pg_id=100, content="test")
        result = ExtractResult(
            entities=[Entity(name="Test", type="Concept")],
        )

        await GraphBuildTask._upsert_to_milvus("hash", chunk_ref, result)
        assert mock_client.upsert.call_count == 0

    @pytest.mark.asyncio
    async def test_upsert_to_milvus_milvus_unavailable(self):
        """When MilvusGraphVectorStore is not available, _upsert_to_milvus should be a no-op."""
        assert not MilvusGraphVectorStore.available()

        GraphBuildTask._embed_fn = lambda x: [0.1, 0.2, 0.3, 0.4]

        chunk_ref = ChunkRef(id=0, pg_id=100, content="test")
        result = ExtractResult(
            entities=[Entity(name="Test", type="Concept")],
        )

        await GraphBuildTask._upsert_to_milvus("hash", chunk_ref, result)
        # No crash, no Milvus calls


class TestGraphRetrievalMilvusPath:
    """Test GraphRetrieval Milvus entity recall → Neo4j PPR search path."""

    def setup_method(self):
        """Reset class state before each test."""
        GraphRetrieval._kg_store = None
        GraphRetrieval._embed_fn = None
        MilvusGraphVectorStore._client = None
        MilvusGraphVectorStore._dim = 0
        MilvusGraphVectorStore._initialized = False

    @pytest.mark.asyncio
    async def test_milvus_entity_recall_no_embed_fn(self):
        """Without embed_fn, _milvus_entity_recall returns empty list."""
        names = await GraphRetrieval._milvus_entity_recall("query", 10)
        assert names == []

    @pytest.mark.asyncio
    async def test_milvus_entity_recall_no_milvus(self):
        """With embed_fn but no Milvus, _milvus_entity_recall returns empty list."""
        GraphRetrieval._embed_fn = lambda x: [0.1, 0.2, 0.3, 0.4]
        assert not MilvusGraphVectorStore.available()

        names = await GraphRetrieval._milvus_entity_recall("query", 10)
        assert names == []

    @pytest.mark.asyncio
    async def test_milvus_entity_recall_with_mock(self):
        """Test entity recall path with mock MilvusGraphVectorStore."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.hybrid_search.return_value = [[
            {"id": "e1", "distance": 0.9, "entity": {"content": "Apple (Organization)", "entity_type": "Organization", "pg_id": 1}},
            {"id": "e2", "distance": 0.8, "entity": {"content": "Steve Jobs (Person)", "entity_type": "Person", "pg_id": 2}},
        ]]

        MilvusGraphVectorStore.set_client(mock_client, dim=4)
        GraphRetrieval._embed_fn = lambda x: [0.1, 0.2, 0.3, 0.4]

        results = await GraphRetrieval._milvus_entity_recall("apple steve", 5)
        assert len(results) == 2
        assert results[0]["name"] == "Apple"
        assert results[0]["entity_type"] == "Organization"
        assert results[0]["score"] == 0.9
        assert results[1]["name"] == "Steve Jobs"
        assert results[1]["entity_type"] == "Person"

    @pytest.mark.asyncio
    async def test_search_milvus_to_ppr_path(self):
        """Test search(): Milvus recall → Neo4j PPR path."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.hybrid_search.return_value = [[
            {"id": "e1", "distance": 0.9, "entity": {"content": "Apple (Organization)", "entity_type": "Organization", "pg_id": 1}},
        ]]
        MilvusGraphVectorStore.set_client(mock_client, dim=4)
        MilvusGraphVectorStore._initialized = True
        GraphRetrieval._embed_fn = lambda x: [0.1, 0.2, 0.3, 0.4]

        # Mock KGStore for PPR (async mock)
        from unittest.mock import AsyncMock

        mock_kg_store = MagicMock()
        mock_kg_store.available.return_value = True
        mock_kg_store.search_with_ppr = AsyncMock(return_value=[
            {"pg_id": 100, "content": "", "score": 0.5, "entities": ["Apple"]},
        ])
        GraphRetrieval._kg_store = mock_kg_store

        results = await GraphRetrieval.search("apple company", 10, expand_depth=1)

        # Should have called search_with_ppr with the recalled entity name
        mock_kg_store.search_with_ppr.assert_called_once()
        call_args = mock_kg_store.search_with_ppr.call_args
        assert call_args[0][0] == ["Apple"]
        # New: seed_weights and entity_types kwargs should be passed
        assert "seed_weights" in call_args[1]
        assert "entity_types" in call_args[1]
        assert call_args[1]["entity_types"] == ["Organization"]
        assert len(results) == 1
        assert results[0]["pg_id"] == 100

    @pytest.mark.asyncio
    async def test_search_fallback_to_kgstore_extract(self):
        """When Milvus recall returns nothing, fallback to KGStore.search (extract from query)."""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.hybrid_search.return_value = [[]]  # No Milvus results
        MilvusGraphVectorStore.set_client(mock_client, dim=4)
        MilvusGraphVectorStore._initialized = True
        GraphRetrieval._embed_fn = lambda x: [0.1, 0.2, 0.3, 0.4]

        # Mock KGStore.search as async
        from unittest.mock import AsyncMock

        mock_kg_store = MagicMock()
        mock_kg_store.available.return_value = True
        mock_kg_store.search = AsyncMock(return_value=[
            {"pg_id": 200, "content": "", "score": 0.3, "entities": ["Banana"]},
        ])
        GraphRetrieval._kg_store = mock_kg_store

        results = await GraphRetrieval.search("banana", 10, expand_depth=1)

        # Should have called kg_store.search (fallback path)
        mock_kg_store.search.assert_called_once()
        assert len(results) == 1
        assert results[0]["pg_id"] == 200


class TestIdGeneration:
    """Test deterministic ID generation for Milvus upsert."""

    def test_entity_id_deterministic(self):
        eid1 = _make_entity_id("hash", 0, "Entity")
        eid2 = _make_entity_id("hash", 0, "Entity")
        assert eid1 == eid2
        assert len(eid1) == 32

    def test_entity_id_different_inputs(self):
        eid1 = _make_entity_id("hash1", 0, "Entity")
        eid2 = _make_entity_id("hash2", 0, "Entity")
        assert eid1 != eid2

    def test_triple_id_deterministic(self):
        tid1 = _make_triple_id("hash", 0, "A", "B", "RELATES_TO")
        tid2 = _make_triple_id("hash", 0, "A", "B", "RELATES_TO")
        assert tid1 == tid2
        assert len(tid1) == 32

    def test_triple_id_different_relations(self):
        tid1 = _make_triple_id("hash", 0, "A", "B", "RELATES_TO")
        tid2 = _make_triple_id("hash", 0, "A", "B", "PART_OF")
        assert tid1 != tid2
