"""Unit tests for MemoryService._sync_consolidation_to_db."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.consolidation import ConsolidationResult, Item
from app.memory.memory_service import MemoryService


class _MockSession:
    """Minimal async session mock that records executed statements."""

    def __init__(self):
        self.executed = []
        self._should_fail = False

    async def execute(self, stmt):
        if self._should_fail:
            raise RuntimeError("DB error")
        self.executed.append(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_memory_service():
    """Create a MemoryService with minimal init (no real DB)."""
    from app.config import Settings
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    svc = MemoryService(settings)
    return svc


def test_sync_empty_result_no_sql():
    """Empty ConsolidationResult should not execute any SQL."""
    svc = _make_memory_service()
    result = ConsolidationResult()
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_get_db.return_value = mock_session
        asyncio.run(svc._sync_consolidation_to_db(result))
        assert len(mock_session.executed) == 0


def test_sync_batch_delete():
    """delete_from_db triggers a batch DELETE."""
    svc = _make_memory_service()
    result = ConsolidationResult(delete_from_db=[5, 12, 18])
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_get_db.return_value = mock_session
        asyncio.run(svc._sync_consolidation_to_db(result))
        assert len(mock_session.executed) == 1


def test_sync_per_row_update():
    """update_in_db triggers per-row UPDATE for each item with an id."""
    svc = _make_memory_service()
    items = [
        Item(content="merged content 1", importance=0.8, id=1, category="fact", tags=["t1"]),
        Item(content="merged content 2", importance=0.6, id=2, category="general", tags=[]),
    ]
    result = ConsolidationResult(update_in_db=items)
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_get_db.return_value = mock_session
        asyncio.run(svc._sync_consolidation_to_db(result))
        assert len(mock_session.executed) == 2  # one UPDATE per item


def test_sync_delete_and_update():
    """Both delete and update present: DELETE first, then UPDATEs."""
    svc = _make_memory_service()
    items = [Item(content="merged", importance=0.7, id=3, category="fact")]
    result = ConsolidationResult(delete_from_db=[1, 2], update_in_db=items)
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_get_db.return_value = mock_session
        asyncio.run(svc._sync_consolidation_to_db(result))
        # 1 DELETE + 1 UPDATE
        assert len(mock_session.executed) == 2


def test_sync_update_skips_none_id():
    """Items with id=None are skipped during UPDATE."""
    svc = _make_memory_service()
    items = [
        Item(content="has id", importance=0.7, id=10, category="fact"),
        Item(content="no id", importance=0.5, id=None, category="general"),
    ]
    result = ConsolidationResult(update_in_db=items)
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_get_db.return_value = mock_session
        asyncio.run(svc._sync_consolidation_to_db(result))
        # Only 1 UPDATE (item with id=None skipped)
        assert len(mock_session.executed) == 1


def test_sync_delete_exception_does_not_raise():
    """DB exception during DELETE is caught, not raised."""
    svc = _make_memory_service()
    result = ConsolidationResult(delete_from_db=[1, 2])
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_session._should_fail = True
        mock_get_db.return_value = mock_session
        # Should not raise
        asyncio.run(svc._sync_consolidation_to_db(result))


def test_sync_update_exception_does_not_raise():
    """DB exception during UPDATE is caught, not raised."""
    svc = _make_memory_service()
    items = [Item(content="merged", importance=0.7, id=3, category="fact")]
    result = ConsolidationResult(update_in_db=items)
    with patch("app.memory.memory_service.get_db") as mock_get_db:
        mock_session = _MockSession()
        mock_session._should_fail = True
        mock_get_db.return_value = mock_session
        # Should not raise
        asyncio.run(svc._sync_consolidation_to_db(result))
