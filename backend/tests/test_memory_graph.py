"""Unit tests for GraphMemory — degradation when Neo4j is unavailable."""

import pytest
from unittest.mock import MagicMock

from app.config import Settings
from app.memory.graph_memory import GraphMemory, _cosine
from app.memory.consolidation import Item


def _make_settings() -> Settings:
    s = MagicMock(spec=Settings)
    s.kg_max_hops = 2
    s.kg_weight = 0.3
    return s


class TestCosineHelper:
    def test_identical_vectors(self):
        assert abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-6

    def test_empty_vectors(self):
        assert _cosine([], []) == 0.0
        assert _cosine([1.0], []) == 0.0
        assert _cosine([], [1.0]) == 0.0

    def test_mismatched_lengths(self):
        assert _cosine([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vectors(self):
        assert _cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


class TestGraphMemoryDegradation:
    """All methods should be no-op when Neo4j driver is None."""

    def _make_gm(self, driver=None) -> GraphMemory:
        return GraphMemory(
            settings=_make_settings(),
            driver=driver,
            sim_threshold=0.7,
        )

    def test_unavailable_when_no_driver(self):
        gm = self._make_gm(driver=None)
        assert gm._available() is False

    @pytest.mark.asyncio
    async def test_add_to_graph_noop(self):
        gm = self._make_gm()
        item = Item(content="test", importance=0.5, id=1)
        result = await gm.add_to_graph(item)
        assert result == -1

    @pytest.mark.asyncio
    async def test_find_related_noop(self):
        gm = self._make_gm()
        result = await gm.find_related(1)
        assert result == []

    @pytest.mark.asyncio
    async def test_delete_from_graph_noop(self):
        gm = self._make_gm()
        # Should not raise
        await gm.delete_from_graph(1)

    @pytest.mark.asyncio
    async def test_bulk_index_noop(self):
        gm = self._make_gm()
        items = [Item(content="a", importance=0.5, id=1)]
        result = await gm.bulk_index(items)
        assert result == 0

    @pytest.mark.asyncio
    async def test_filter_protected_noop(self):
        gm = self._make_gm()
        result = await gm.filter_protected([1, 2, 3])
        assert result == []

    @pytest.mark.asyncio
    async def test_update_node_noop(self):
        gm = self._make_gm()
        item = Item(content="test", importance=0.5, id=1)
        # Should not raise
        await gm.update_node(item)

    @pytest.mark.asyncio
    async def test_graph_aware_consolidate_no_ltm(self):
        gm = self._make_gm()
        result = await gm.graph_aware_consolidate()
        assert result is None

    @pytest.mark.asyncio
    async def test_close_noop(self):
        gm = self._make_gm()
        # Should not raise
        await gm.close()
        assert gm.prev_id == -1


class TestGraphMemoryLTMProxy:
    """Test LTM proxy methods when LTM is not injected."""

    def _make_gm(self) -> GraphMemory:
        return GraphMemory(settings=_make_settings(), driver=None)

    def test_sync_prev_id_no_ltm(self):
        gm = self._make_gm()
        # Should not raise
        gm.sync_prev_id()
        assert gm.prev_id == -1

    def test_set_consolidation_config_no_ltm(self):
        gm = self._make_gm()
        # Should not raise
        gm.set_consolidation_config(None)

    def test_need_consolidation_no_ltm(self):
        gm = self._make_gm()
        assert gm.need_consolidation() is False

    def test_set_ltm(self):
        gm = self._make_gm()
        mock_ltm = MagicMock()
        gm.set_ltm(mock_ltm)
        assert gm.ltm is mock_ltm

    def test_need_consolidation_with_ltm(self):
        gm = self._make_gm()
        mock_ltm = MagicMock()
        mock_ltm.need_consolidation.return_value = True
        gm.set_ltm(mock_ltm)
        assert gm.need_consolidation() is True

    def test_sync_prev_id_with_ltm(self):
        gm = self._make_gm()
        mock_ltm = MagicMock()
        mock_ltm.last_id.return_value = 42
        gm.set_ltm(mock_ltm)
        gm.sync_prev_id()
        assert gm.prev_id == 42
