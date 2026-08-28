"""Unit tests for graph isolation phase 1: graph_utils, types, KGStore.

Tests cover:
- compute_entity_id / compute_triple_id / safe_user_label / normalize_entity_name
- Cypher template functions output correct user_label
- build_entity_attributes merging logic
- KGStore._upsert_entity cross-chunk MERGE behavior
- KGStore.delete_document cleanup behavior
- Two users with same entity name don't MERGE
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.graph_utils import (
    compute_entity_id,
    compute_triple_id,
    cypher_delete_document,
    cypher_merge_chunk,
    cypher_merge_entity_mention,
    cypher_merge_relation,
    cypher_query_entity_ids_by_doc_hash,
    cypher_search_direct,
    cypher_search_ppr_apoc,
    cypher_search_ppr_gds,
    cypher_search_subgraph,
    normalize_entity_name,
    safe_user_label,
)
from app.graph.types import Entity, build_entity_attributes

# ── 9.1: compute_entity_id / compute_triple_id / safe_user_label / normalize_entity_name ──


class TestGraphUtilsDeterminism:
    def test_compute_entity_id_is_deterministic(self):
        id1 = compute_entity_id("user1", "react", "Concept")
        id2 = compute_entity_id("user1", "react", "Concept")
        assert id1 == id2
        assert len(id1) == 32

    def test_compute_entity_id_different_users(self):
        id1 = compute_entity_id("user1", "react", "Concept")
        id2 = compute_entity_id("user2", "react", "Concept")
        assert id1 != id2

    def test_compute_entity_id_different_names(self):
        id1 = compute_entity_id("user1", "react", "Concept")
        id2 = compute_entity_id("user1", "vue", "Concept")
        assert id1 != id2

    def test_compute_triple_id_is_deterministic(self):
        id1 = compute_triple_id("user1", "react", "Concept", "relates_to", "vue", "Concept")
        id2 = compute_triple_id("user1", "react", "Concept", "relates_to", "vue", "Concept")
        assert id1 == id2
        assert len(id1) == 32

    def test_compute_triple_id_different_users(self):
        id1 = compute_triple_id("user1", "react", "Concept", "relates_to", "vue", "Concept")
        id2 = compute_triple_id("user2", "react", "Concept", "relates_to", "vue", "Concept")
        assert id1 != id2

    def test_safe_user_label_format(self):
        label = safe_user_label("abc-123-def")
        assert label.startswith("u_")
        assert len(label) == 16  # u_ + 14 chars
        # Should be alphanumeric + underscore
        assert all(c.isalnum() or c == "_" for c in label)

    def test_safe_user_label_empty(self):
        label = safe_user_label("")
        assert label == "u_anonymous"

    def test_safe_user_label_deterministic(self):
        label1 = safe_user_label("user-abc")
        label2 = safe_user_label("user-abc")
        assert label1 == label2

    def test_normalize_entity_name(self):
        assert normalize_entity_name("  Apple  ") == "apple"
        assert normalize_entity_name("  Apple  Inc  ") == "apple inc"
        assert normalize_entity_name("") == ""
        assert normalize_entity_name("REACT") == "react"

    def test_entity_id_uses_normalized_name(self):
        """Entity ID should be same for 'Apple' and 'apple' (normalized)."""
        id1 = compute_entity_id("user1", normalize_entity_name("Apple"), "Organization")
        id2 = compute_entity_id("user1", normalize_entity_name("apple"), "Organization")
        assert id1 == id2


# ── 9.2: Cypher template functions output correct user_label ──


class TestCypherTemplates:
    def test_cypher_merge_chunk_contains_label(self):
        query = cypher_merge_chunk("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$chunk_id" in query
        assert "$doc_hash" in query

    def test_cypher_merge_entity_mention_contains_label(self):
        query = cypher_merge_entity_mention("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$entity_id" in query
        assert "$name" in query
        assert "MENTIONS" in query

    def test_cypher_merge_relation_contains_label(self):
        query = cypher_merge_relation("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$source_id" in query
        assert "$target_id" in query
        assert "RELATION" in query

    def test_cypher_delete_document_contains_label(self):
        query = cypher_delete_document("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$doc_hash" in query
        assert "DELETE" in query

    def test_cypher_query_entity_ids_contains_label(self):
        query = cypher_query_entity_ids_by_doc_hash("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "entity_id" in query

    def test_cypher_search_direct_contains_label(self):
        query = cypher_search_direct("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$names" in query

    def test_cypher_search_subgraph_contains_label(self):
        query = cypher_search_subgraph("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$names" in query

    def test_cypher_search_ppr_gds_with_weights(self):
        query = cypher_search_ppr_gds("u_abc123", has_weights=True)
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "sourceNodeWeights" in query

    def test_cypher_search_ppr_gds_without_weights(self):
        query = cypher_search_ppr_gds("u_abc123", has_weights=False)
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "sourceNodeWeights" not in query

    def test_cypher_search_ppr_apoc_contains_label(self):
        query = cypher_search_ppr_apoc("u_abc123")
        assert "UserKG" in query
        assert "u_abc123" in query
        assert "$names" in query

    def test_different_labels_produce_different_queries(self):
        q1 = cypher_merge_entity_mention("u_user1")
        q2 = cypher_merge_entity_mention("u_user2")
        assert q1 != q2


# ── 9.7: Entity attributes merge logic ──


class TestEntityAttributes:
    def test_build_entity_attributes_empty(self):
        assert build_entity_attributes([]) == []

    def test_build_entity_attributes_single_entity(self):
        entities = [{"attributes": [{"text": "工程师", "label": "Occupation"}]}]
        result = build_entity_attributes(entities)
        assert len(result) == 1
        assert result[0] == {"text": "工程师", "label": "Occupation"}

    def test_build_entity_attributes_merge_different(self):
        entities = [
            {"attributes": [{"text": "工程师", "label": "Occupation"}]},
            {"attributes": [{"text": "北京", "label": "Location"}]},
        ]
        result = build_entity_attributes(entities)
        assert len(result) == 2

    def test_build_entity_attributes_dedup_same(self):
        entities = [
            {"attributes": [{"text": "工程师", "label": "Occupation"}]},
            {"attributes": [{"text": "工程师", "label": "Occupation"}]},
        ]
        result = build_entity_attributes(entities)
        assert len(result) == 1

    def test_entity_has_attributes_field(self):
        ent = Entity(name="test", type="Concept")
        assert ent.attributes == []

    def test_entity_attributes_default_factory(self):
        ent1 = Entity(name="a")
        ent2 = Entity(name="b")
        ent1.attributes.append({"text": "x", "label": "y"})
        assert ent2.attributes == []


# ── 9.3: KGStore._upsert_entity cross-chunk MERGE ──


class TestKGStoreUpsertEntity:
    def _make_kg_store(self, user_id="user1"):
        from app.graph.extractor import Extractor
        from app.graph.kgstore import KGStore

        mock_driver = AsyncMock()
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_driver.session = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.kg_max_hops = 2
        settings.kg_weight = 1.0
        extractor = Extractor(None)
        return KGStore(settings, mock_driver, extractor, user_id=user_id)

    @pytest.mark.asyncio
    async def test_upsert_entity_uses_correct_label(self):
        kg = self._make_kg_store("user-abc")
        ent = Entity(name="React", type="Concept", doc_hash="doc1", chunk_id=1, pg_id=10)
        await kg._upsert_entity(ent)
        # Verify _run_cypher was called with correct user_label
        calls = kg._driver.session.return_value.run.call_args_list
        assert len(calls) > 0
        query = calls[0][0][0]
        assert kg._user_label in query

    @pytest.mark.asyncio
    async def test_upsert_entity_same_name_same_type_produces_same_id(self):
        kg = self._make_kg_store("user1")
        ent1 = Entity(name="React", type="Concept", doc_hash="doc1", chunk_id=1, pg_id=10)
        ent2 = Entity(name="React", type="Concept", doc_hash="doc1", chunk_id=5, pg_id=20)
        await kg._upsert_entity(ent1)
        await kg._upsert_entity(ent2)
        # Both calls should use the same entity_id (deterministic)
        calls = kg._driver.session.return_value.run.call_args_list
        params1 = calls[0][1]["parameters"]
        params2 = calls[1][1]["parameters"]
        assert params1["entity_id"] == params2["entity_id"]

    @pytest.mark.asyncio
    async def test_upsert_entity_different_users_produce_different_ids(self):
        kg1 = self._make_kg_store("user1")
        kg2 = self._make_kg_store("user2")

        ent = Entity(name="React", type="Concept", doc_hash="doc1", chunk_id=1, pg_id=10)
        await kg1._upsert_entity(ent)
        await kg2._upsert_entity(ent)

        calls1 = kg1._driver.session.return_value.run.call_args_list
        calls2 = kg2._driver.session.return_value.run.call_args_list
        params1 = calls1[0][1]["parameters"]
        params2 = calls2[0][1]["parameters"]
        assert params1["entity_id"] != params2["entity_id"]


# ── 9.5: KGStore.delete_document — shared entity preserved ──


class TestKGStoreDeleteDocument:
    def _make_kg_store(self, user_id="user1"):
        from app.graph.extractor import Extractor
        from app.graph.kgstore import KGStore

        mock_driver = AsyncMock()
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_driver.session = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.kg_max_hops = 2
        settings.kg_weight = 1.0
        extractor = Extractor(None)
        return KGStore(settings, mock_driver, extractor, user_id=user_id)

    @pytest.mark.asyncio
    async def test_delete_document_calls_cypher_with_label(self):
        kg = self._make_kg_store("user1")
        with patch.object(kg, "_delete_milvus_vectors", new_callable=AsyncMock):
            await kg.delete_document("doc_hash_123")
        # Verify _run_cypher was called with user_label-scoped query
        calls = kg._driver.session.return_value.run.call_args_list
        for call in calls:
            query = call[0][0]
            assert kg._user_label in query

    @pytest.mark.asyncio
    async def test_delete_document_cleanup_milvus(self):
        kg = self._make_kg_store("user1")
        with patch.object(kg, "_delete_milvus_vectors", new_callable=AsyncMock) as mock_milvus:
            await kg.delete_document("doc_hash_123")
            mock_milvus.assert_called()

    @pytest.mark.asyncio
    async def test_delete_document_neo4j_unavailable_still_cleans_milvus(self):
        """When Neo4j is unavailable, still clean Milvus from PG ent_ids."""
        from app.graph.extractor import Extractor
        from app.graph.kgstore import KGStore

        settings = MagicMock()
        settings.kg_max_hops = 2
        settings.kg_weight = 1.0
        extractor = Extractor(None)
        kg = KGStore(settings, None, extractor, user_id="user1")  # driver=None

        with patch.object(kg, "_delete_milvus_vectors", new_callable=AsyncMock) as mock_milvus:
            await kg.delete_document("doc_hash_123")
            mock_milvus.assert_called_once_with("doc_hash_123")


# ── 9.6: Two users' same entity doesn't MERGE conflict ──


class TestUserIsolation:
    def test_two_users_different_labels(self):
        label1 = safe_user_label("user-aaa")
        label2 = safe_user_label("user-bbb")
        assert label1 != label2

    def test_two_users_different_entity_ids(self):
        id1 = compute_entity_id("user-aaa", "apple", "Organization")
        id2 = compute_entity_id("user-bbb", "apple", "Organization")
        assert id1 != id2

    def test_cypher_queries_use_different_labels(self):
        label1 = safe_user_label("user-aaa")
        label2 = safe_user_label("user-bbb")
        q1 = cypher_merge_entity_mention(label1)
        q2 = cypher_merge_entity_mention(label2)
        assert label1 in q1
        assert label2 in q2
        assert label2 not in q1
        assert label1 not in q2


# ── 9.8: Integration test (mock) — ingest → build → delete → verify cleanup ──


class TestIntegrationFlow:
    def _make_kg_store(self, user_id="user1"):
        from app.graph.extractor import Extractor
        from app.graph.kgstore import KGStore

        mock_driver = AsyncMock()
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_driver.session = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.kg_max_hops = 2
        settings.kg_weight = 1.0
        extractor = Extractor(None)
        return KGStore(settings, mock_driver, extractor, user_id=user_id)

    @pytest.mark.asyncio
    async def test_ingest_then_delete_calls_cleanup(self):
        kg = self._make_kg_store("user1")
        from app.graph.types import ChunkRef

        # Simulate ingest (mocked)
        chunk = ChunkRef(id=1, pg_id=10, content="React is a framework")
        ent = Entity(name="React", type="Concept", doc_hash="doc1", chunk_id=1, pg_id=10)
        await kg._upsert_chunk(chunk, "doc1", "file1")
        await kg._upsert_entity(ent, "file1")

        # Simulate delete
        with patch.object(kg, "_delete_milvus_vectors", new_callable=AsyncMock) as mock_cleanup:
            await kg.delete_document("doc1")
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_user_id_changes_label(self):
        kg = self._make_kg_store("user1")
        old_label = kg._user_label
        kg.set_user_id("user2")
        assert kg._user_label != old_label
