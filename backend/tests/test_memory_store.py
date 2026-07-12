"""Unit tests for memory_store tool and SimpleRateLimiter.

Covers:
- Normal write (stores successfully, returns count)
- Category whitelist rejection
- Importance floor rejection
- Content length validation
- Per-run rate limiting (max 3 per run)
- Dedup hit (store_classified returns False)
- SimpleRateLimiter TTL and counting
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.base import ToolContext
from app.tools.memory_store import MAX_WRITES_PER_RUN, memory_store_handler
from app.tools.rate_limiter import SimpleRateLimiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the module-level rate limiter before each test."""
    from app.tools import memory_store as ms_module
    original = ms_module._rate_limiter
    ms_module._rate_limiter = SimpleRateLimiter()
    yield
    ms_module._rate_limiter = original


def _make_ctx(agent_id: str = "agent_1", run_id: str = "run_1") -> ToolContext:
    """Create a minimal ToolContext for testing."""
    return ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp/ws",
        agent_id=agent_id,
        run_id=run_id,
        cancel_event=asyncio.Event(),
    )


def _make_mock_memory_service(ltm_items=None, store_result=True):
    """Create a mock _memory_service with an LTM that has items and store_classified."""
    ltm = MagicMock()
    ltm.items = ltm_items or []
    ltm.store_classified = AsyncMock(return_value=store_result)

    svc = MagicMock()
    svc.ltm = ltm
    svc._embed_fn = None
    return svc


class TestMemoryStoreHandler:
    """Tests for memory_store_handler validation and storage."""

    @pytest.mark.asyncio
    async def test_normal_write(self):
        """A valid call should store successfully and return count."""
        svc = _make_mock_memory_service(ltm_items=[], store_result=True)
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "User project uses React 19",
                    "category": "fact",
                    "importance": 0.7,
                    "tags": ["tech_stack"],
                },
                ctx,
            )
        assert result.ok
        assert result.value["stored"] is True
        assert result.value["agent_memory_count"] == 0

    @pytest.mark.asyncio
    async def test_normal_write_with_existing_memories(self):
        """agent_memory_count should reflect existing agent-scoped items."""
        from app.memory.consolidation import Item

        items = [
            Item(content="old memory 1", scope="agent", agent_id="agent_1"),
            Item(content="old memory 2", scope="agent", agent_id="agent_1"),
            Item(content="global memory", scope="global", agent_id=""),
        ]
        svc = _make_mock_memory_service(ltm_items=items, store_result=True)
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "New fact",
                    "category": "fact",
                    "importance": 0.5,
                },
                ctx,
            )
        assert result.ok
        assert result.value["agent_memory_count"] == 2

    @pytest.mark.asyncio
    async def test_invalid_category(self):
        """Invalid category should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Some content",
                    "category": "general",
                    "importance": 0.5,
                },
                ctx,
            )
        assert not result.ok
        assert "category must be one of" in result.error

    @pytest.mark.asyncio
    async def test_missing_category(self):
        """Missing category should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Some content",
                    "importance": 0.5,
                },
                ctx,
            )
        assert not result.ok
        assert "category must be one of" in result.error

    @pytest.mark.asyncio
    async def test_importance_too_low(self):
        """importance < 0.3 should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Some content",
                    "category": "fact",
                    "importance": 0.2,
                },
                ctx,
            )
        assert not result.ok
        assert "importance must be >= 0.3" in result.error

    @pytest.mark.asyncio
    async def test_importance_too_high(self):
        """importance > 1.0 should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Some content",
                    "category": "fact",
                    "importance": 1.5,
                },
                ctx,
            )
        assert not result.ok
        assert "importance must be <= 1.0" in result.error

    @pytest.mark.asyncio
    async def test_empty_content(self):
        """Empty content should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "   ",
                    "category": "fact",
                    "importance": 0.5,
                },
                ctx,
            )
        assert not result.ok
        assert "content must be 1-" in result.error

    @pytest.mark.asyncio
    async def test_content_too_long(self):
        """Content exceeding 500 chars should be rejected."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "x" * 501,
                    "category": "fact",
                    "importance": 0.5,
                },
                ctx,
            )
        assert not result.ok
        assert "content must be 1-" in result.error

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        """4th call within the same run should be rate-limited."""
        # Use a fresh rate limiter to avoid interference from other tests
        with patch("app.tools.memory_store._rate_limiter") as mock_rl:
            call_count = 0

            async def mock_incr(key, ttl=300):
                nonlocal call_count
                call_count += 1
                return call_count

            mock_incr.return_value = 0
            mock_rl.incr = mock_incr

            svc = _make_mock_memory_service()

            with patch("app.main._memory_service", svc):
                ctx = _make_ctx()

                # First 3 calls should succeed
                for i in range(MAX_WRITES_PER_RUN):
                    result = await memory_store_handler(
                        {
                            "content": f"Memory {i}",
                            "category": "fact",
                            "importance": 0.5,
                        },
                        ctx,
                    )
                    assert result.ok, f"Call {i+1} should succeed"

                # 4th call should be rejected
                result = await memory_store_handler(
                    {
                        "content": "Memory 4",
                        "category": "fact",
                        "importance": 0.5,
                    },
                    ctx,
                )
                assert not result.ok
                assert "rate limit" in result.error

    @pytest.mark.asyncio
    async def test_dedup_hit(self):
        """When store_classified returns False (dedup), stored should be False."""
        svc = _make_mock_memory_service(store_result=False)
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Duplicate fact",
                    "category": "fact",
                    "importance": 0.7,
                },
                ctx,
            )
        assert result.ok
        assert result.value["stored"] is False

    @pytest.mark.asyncio
    async def test_store_classified_called_with_agent_scope(self):
        """store_classified should be called with scope='agent' and correct agent_id."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx(agent_id="my_agent", run_id="my_run")
            await memory_store_handler(
                {
                    "content": "Important fact",
                    "category": "policy",
                    "importance": 0.8,
                    "tags": ["constraint"],
                },
                ctx,
            )
        svc.ltm.store_classified.assert_called_once()
        call_kwargs = svc.ltm.store_classified.call_args.kwargs
        assert call_kwargs["scope"] == "agent"
        assert call_kwargs["agent_id"] == "my_agent"
        assert call_kwargs["category"] == "policy"
        assert call_kwargs["importance"] == 0.8
        assert call_kwargs["slot_hint"] == "constraints"

    @pytest.mark.asyncio
    async def test_memory_service_not_initialized(self):
        """When _memory_service is None, should return error."""
        with patch("app.main._memory_service", None):
            ctx = _make_ctx()
            result = await memory_store_handler(
                {
                    "content": "Fact",
                    "category": "fact",
                    "importance": 0.5,
                },
                ctx,
            )
        assert not result.ok
        assert "not initialized" in result.error

    @pytest.mark.asyncio
    async def test_all_valid_categories(self):
        """All three valid categories should be accepted."""
        svc = _make_mock_memory_service()
        with patch("app.main._memory_service", svc):
            for cat in ["fact", "policy", "tool_failure"]:
                svc.ltm.store_classified.reset_mock()
                svc.ltm.store_classified.return_value = True
                ctx = _make_ctx(run_id=f"run_{cat}")
                result = await memory_store_handler(
                    {
                        "content": f"Test {cat}",
                        "category": cat,
                        "importance": 0.5,
                    },
                    ctx,
                )
                assert result.ok, f"Category '{cat}' should be accepted"
                svc.ltm.store_classified.assert_called_once()


class TestSimpleRateLimiter:
    """Tests for the SimpleRateLimiter class."""

    @pytest.mark.asyncio
    async def test_basic_increment(self):
        """Counter should increment by 1 each call."""
        rl = SimpleRateLimiter()
        assert await rl.incr("key1") == 1
        assert await rl.incr("key1") == 2
        assert await rl.incr("key1") == 3

    @pytest.mark.asyncio
    async def test_separate_keys(self):
        """Different keys should have independent counters."""
        rl = SimpleRateLimiter()
        assert await rl.incr("key1") == 1
        assert await rl.incr("key2") == 1
        assert await rl.incr("key1") == 2

    @pytest.mark.asyncio
    async def test_get_returns_current_count(self):
        """get() should return current count without incrementing."""
        rl = SimpleRateLimiter()
        await rl.incr("key1")
        await rl.incr("key1")
        assert await rl.get("key1") == 2
        # get should not increment
        assert await rl.get("key1") == 2

    @pytest.mark.asyncio
    async def test_get_missing_key(self):
        """get() for missing key should return 0."""
        rl = SimpleRateLimiter()
        assert await rl.get("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        """Counter should reset after TTL expires."""
        rl = SimpleRateLimiter()
        # Use very short TTL
        await rl.incr("key1", ttl=0)
        # Wait a tiny bit for expiry
        await asyncio.sleep(0.05)
        # After expiry, counter should reset to 1
        assert await rl.incr("key1", ttl=0) == 1

    @pytest.mark.asyncio
    async def test_concurrent_increments(self):
        """Concurrent increments should all be counted (lock safety)."""
        rl = SimpleRateLimiter()

        async def incr_10_times():
            for _ in range(10):
                await rl.incr("shared_key")

        await asyncio.gather(*(incr_10_times() for _ in range(5)))
        assert await rl.get("shared_key") == 50
