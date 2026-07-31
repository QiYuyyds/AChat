"""Unit tests for LongTerm memory — mock DB, real consolidation algorithm."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.memory.consolidation import Item
from app.memory.long_term import LongTerm


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "memory_consolidation_similarity": 0.80,
        "memory_consolidation_dedup": 0.95,
        "memory_consolidation_ttl_days": 30,
        "memory_consolidation_decay_rate": 0.995,
        "memory_consolidation_min_importance": 0.3,
        "memory_consolidation_trigger": 5,
    }
    defaults.update(overrides)
    s = MagicMock(spec=Settings)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestLongTerm:
    def _make_ltm(self, **kw) -> LongTerm:
        settings = _make_settings(**kw)
        ltm = LongTerm(settings)
        return ltm

    @pytest.mark.asyncio
    async def test_add_without_db(self):
        """add() should work in-memory even if PG write fails."""
        ltm = self._make_ltm()
        # Patch get_db to simulate PG failure
        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add("test memory", importance=0.7)

        assert len(ltm.items) == 1
        assert ltm.items[0].content == "test memory"
        assert ltm.items[0].importance == 0.7

    @pytest.mark.asyncio
    async def test_add_with_embedding(self):
        """add() should use embedding function when available."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [0.1, 0.2, 0.3])

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add("embedded memory")

        assert len(ltm.items) == 1
        assert ltm.items[0].embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_recall_empty(self):
        ltm = self._make_ltm()
        result = await ltm.recall("query")
        assert result == []

    @pytest.mark.asyncio
    async def test_recall_with_embedding(self):
        """Recall should rank by semantic similarity * 0.7 + importance * 0.3."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "cat" in text else [0.0, 1.0])

        with patch("app.memory.long_term.get_remote_db"):
            await ltm.add("I love cats", importance=0.8)
            await ltm.add("The weather today", importance=0.5)

        results = await ltm.recall("tell me about cats", top_k=2)
        # "I love cats" should rank higher (embedding similarity)
        assert len(results) >= 1
        assert "cat" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_consolidation_dedup(self):
        """Near-duplicate items should be merged during consolidation."""
        ltm = self._make_ltm(memory_consolidation_trigger=2)
        ltm.set_embed_fn(lambda text: [1.0, 0.0, 0.0])

        with patch("app.memory.long_term.get_remote_db"):
            await ltm.add("I like programming in Python", importance=0.8)
            await ltm.add("I like programming in Python very much", importance=0.7)

        assert ltm.need_consolidation()

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # At least one dedup or merge should happen (identical embeddings)
        assert result.deduped + result.merged >= 1

    @pytest.mark.asyncio
    async def test_consolidation_not_needed(self):
        """need_consolidation returns False when under trigger threshold."""
        ltm = self._make_ltm(memory_consolidation_trigger=10)
        with patch("app.memory.long_term.get_remote_db"):
            await ltm.add("one item")
        assert not ltm.need_consolidation()

    @pytest.mark.asyncio
    async def test_snapshot(self):
        """snapshot() returns a copy of items."""
        ltm = self._make_ltm()
        with patch("app.memory.long_term.get_remote_db"):
            await ltm.add("item1", importance=0.5)
            await ltm.add("item2", importance=0.7)

        snap = ltm.snapshot()
        assert len(snap) == 2
        assert snap[0].content == "item1"
        # Modifying snapshot should not affect original
        snap[0].content = "modified"
        assert ltm.items[0].content == "item1"

    def test_last_id_empty(self):
        ltm = self._make_ltm()
        assert ltm.last_id() == -1

    @pytest.mark.asyncio
    async def test_last_id(self):
        ltm = self._make_ltm()
        with patch("app.memory.long_term.get_remote_db"):
            await ltm.add("first")
            await ltm.add("second")
        assert ltm.last_id() >= 0


class TestGraphExpandedRecall:
    """Graph expansion integration tests for LongTerm.recall / recall_by_filter."""

    def _make_ltm(self, **kw) -> LongTerm:
        settings = _make_settings(**kw)
        return LongTerm(settings)

    def _seed_items(self, ltm: LongTerm, items_spec: list) -> None:
        """Populate ltm.items with pre-built Items (no DB)."""
        for idx, (content, importance, embedding, category) in enumerate(items_spec):
            it = Item(
                content=content,
                importance=importance,
                embedding=embedding,
                id=idx,
                created_at=time.time(),
                last_accessed=time.time(),
                category=category or "general",
            )
            ltm.items.append(it)
        ltm._next_id = len(ltm.items)

    @pytest.mark.asyncio
    async def test_recall_graph_expansion_score(self):
        """Graph-expanded items must appear with score=0.45."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        # Item 0: high similarity to query; Item 1: low similarity, graph-linked to 0
        self._seed_items(ltm, [
            ("Python 3.12 类型参数", 0.8, [1.0, 0.0], "general"),
            ("pip 依赖管理",         0.6, [0.0, 1.0], "general"),
        ])

        mock_gm = AsyncMock()
        mock_gm.find_related = AsyncMock(side_effect=lambda item_id, **kwargs: [1] if item_id == 0 else [])
        ltm.set_graph_memory(mock_gm)

        results = await ltm.recall("Python 特性", top_k=5)
        ids = [r.id for r in results]
        scores = {r.id: r.score for r in results}

        assert 0 in ids, "Seed item 0 must be in results"
        assert 1 in ids, "Graph-expanded item 1 must be in results"
        assert scores[1] == pytest.approx(0.45), "Expanded item score must be 0.45"

    @pytest.mark.asyncio
    async def test_recall_no_graph_memory(self):
        """recall() with graph_memory=None must behave exactly as before."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        self._seed_items(ltm, [
            ("Python 3.12 类型参数", 0.8, [1.0, 0.0], "general"),
            ("pip 依赖管理",         0.3, [0.0, 1.0], "general"),
        ])
        # graph_memory stays None (never set)

        results = await ltm.recall("Python 特性", top_k=5)
        ids = [r.id for r in results]

        # Seed with high similarity should appear; low-sim item filtered by threshold
        assert 0 in ids
        # No exception raised, no graph expansion attempted
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_recall_find_related_exception(self):
        """find_related() raising must be caught; only seeds returned."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        self._seed_items(ltm, [
            ("Python 3.12 类型参数", 0.8, [1.0, 0.0], "general"),
            ("pip 依赖管理",         0.6, [0.0, 1.0], "general"),
        ])

        mock_gm = AsyncMock()
        mock_gm.find_related = AsyncMock(side_effect=Exception("Neo4j down"))
        ltm.set_graph_memory(mock_gm)

        results = await ltm.recall("Python 特性", top_k=5)
        ids = [r.id for r in results]

        # Only seed (item 0, the high-scoring one) should appear
        assert 0 in ids
        # Expanded item must NOT appear since find_related failed
        assert 1 not in ids

    @pytest.mark.asyncio
    async def test_recall_by_filter_graph_expansion_with_category(self):
        """Graph-expanded items must respect category filter in recall_by_filter."""
        ltm = self._make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        # Item 0: seed (category=python)
        # Item 1: graph-linked, category=python  -> included
        # Item 2: graph-linked, category=general -> EXCLUDED by filter
        self._seed_items(ltm, [
            ("Python 3.12 类型参数", 0.8, [1.0, 0.0], "python"),
            ("pip 依赖管理",         0.6, [0.1, 0.9], "python"),
            ("天气很好",             0.5, [0.0, 1.0], "general"),
        ])

        mock_gm = AsyncMock()
        mock_gm.find_related = AsyncMock(side_effect=lambda item_id, **kwargs: [1, 2] if item_id == 0 else [])
        ltm.set_graph_memory(mock_gm)

        filt = MagicMock()
        filt.min_score = 0.0
        filt.categories = ["python"]
        filt.require_tags = []
        filt.max_age_hours = 0
        filt.top_k = 10

        results = await ltm.recall_by_filter("Python 特性", [1.0, 0.0], filt)
        ids = [r.id for r in results]

        assert 0 in ids, "Seed item must be in results"
        assert 1 in ids, "Graph-expanded item with matching category must be in results"
        assert 2 not in ids, "Graph-expanded item with non-matching category must be excluded"
