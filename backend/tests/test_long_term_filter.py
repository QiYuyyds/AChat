"""Unit tests for LongTerm.filter_by_category — pure in-memory, no DB."""

import asyncio
import time

import pytest

from app.config import Settings
from app.memory.consolidation import Item
from app.memory.long_term import LongTerm


def _make_settings(**overrides) -> Settings:
    defaults = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "memory_short_term_max_turns": 10,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_item(content: str, importance: float, category: str = "general") -> Item:
    return Item(
        content=content,
        importance=importance,
        created_at=time.time(),
        last_accessed=time.time(),
        category=category,
    )


@pytest.fixture
def ltm():
    """Create a LongTerm instance with pre-populated items (no DB needed)."""
    inst = LongTerm(_make_settings())
    inst.items = [
        _make_item("用户姓名: Alice", 0.9, "identity"),
        _make_item("用户偏好: 深色模式", 0.7, "preference"),
        _make_item("项目使用微服务架构", 0.5, "fact"),
        _make_item("用户姓名备份", 0.3, "identity"),
        _make_item("昨天讨论了部署方案", 0.6, "episodic"),
        _make_item("常规备注", 0.1, "general"),
    ]
    return inst


def test_filter_by_category_normal(ltm):
    """Filtering by identity and preference returns matching items ordered by importance."""
    result = asyncio.run(ltm.filter_by_category(["identity", "preference"], limit=10))
    assert len(result) == 3
    # Ordered by importance descending
    assert result[0].importance >= result[1].importance >= result[2].importance
    cats = {r.category for r in result}
    assert cats == {"identity", "preference"}


def test_filter_by_category_empty_result(ltm):
    """Filtering by nonexistent category returns empty list."""
    result = asyncio.run(ltm.filter_by_category(["nonexistent"], limit=10))
    assert result == []


def test_filter_by_category_limit_truncation(ltm):
    """Limit truncation keeps the highest-importance items."""
    result = asyncio.run(ltm.filter_by_category(["identity"], limit=1))
    assert len(result) == 1
    assert result[0].importance == 0.9  # highest importance identity item


def test_filter_by_category_importance_ordering(ltm):
    """Results are ordered by importance descending within matching categories."""
    result = asyncio.run(ltm.filter_by_category(["identity"], limit=10))
    assert len(result) == 2
    assert result[0].importance >= result[1].importance
    assert result[0].content == "用户姓名: Alice"
    assert result[1].content == "用户姓名备份"


def test_filter_by_category_empty_categories(ltm):
    """Empty categories list returns empty."""
    result = asyncio.run(ltm.filter_by_category([], limit=10))
    assert result == []


def test_filter_by_category_empty_items():
    """No items returns empty."""
    inst = LongTerm(_make_settings())
    inst.items = []
    result = asyncio.run(inst.filter_by_category(["identity"], limit=10))
    assert result == []
