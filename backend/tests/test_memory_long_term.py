"""Unit tests for LongTerm memory — mock DB, real consolidation algorithm."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
        with patch("app.memory.long_term.get_db") as mock_db:
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

        with patch("app.memory.long_term.get_db") as mock_db:
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

        with patch("app.memory.long_term.get_db"):
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

        with patch("app.memory.long_term.get_db"):
            await ltm.add("I like programming in Python", importance=0.8)
            await ltm.add("I like programming in Python very much", importance=0.7)

        assert ltm.need_consolidation()

        with patch("app.memory.long_term.get_db"):
            result = await ltm.consolidate()

        # At least one dedup or merge should happen (identical embeddings)
        assert result.deduped + result.merged >= 1

    @pytest.mark.asyncio
    async def test_consolidation_not_needed(self):
        """need_consolidation returns False when under trigger threshold."""
        ltm = self._make_ltm(memory_consolidation_trigger=10)
        with patch("app.memory.long_term.get_db"):
            await ltm.add("one item")
        assert not ltm.need_consolidation()

    @pytest.mark.asyncio
    async def test_snapshot(self):
        """snapshot() returns a copy of items."""
        ltm = self._make_ltm()
        with patch("app.memory.long_term.get_db"):
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
        with patch("app.memory.long_term.get_db"):
            await ltm.add("first")
            await ltm.add("second")
        assert ltm.last_id() >= 0
