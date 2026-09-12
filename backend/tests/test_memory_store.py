"""Unit tests for memory_store tool and SimpleRateLimiter.

Covers:
- Normal write (stores a digest Markdown file, returns path + bucket)
- name / content / bucket / importance validation
- Per-run rate limiting (max writes per agent run)
- SimpleRateLimiter TTL and counting

判定注记（A 类，rewrite-memory-file-native）：旧版 LTM store_classified /
category 白名单 / app.memory.consolidation / 去重返回 False 的断言随记忆系统
文件化重写一并失效，对应用例已删除；文件写入路径由本文件的 tmp_path 用例覆盖。
"""

import asyncio
from unittest.mock import MagicMock, patch

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


def _make_mock_memory_service(tmp_path):
    """Create a mock _memory_service backed by a tmp digest root."""
    digest_path = tmp_path / "digest"
    svc = MagicMock()
    svc.workspace.root = str(tmp_path)
    svc.workspace.digest_path.return_value = digest_path / "procedure" / "n.md"
    svc.auto_index.index_file = MagicMock()
    return svc


def _valid_args(**overrides):
    args = {
        "name": "react-version",
        "content": "User project uses React 19",
        "bucket": "procedure",
        "importance": 0.7,
        "tags": ["tech_stack"],
    }
    args.update(overrides)
    return args


class TestMemoryStoreHandler:
    """Tests for memory_store_handler validation and file storage."""

    @pytest.mark.asyncio
    async def test_normal_write(self, tmp_path):
        """A valid call writes the digest file and returns path + bucket."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(_valid_args(), _make_ctx())
        assert result.ok, result.error
        assert result.value["stored"] is True
        assert result.value["bucket"] == "procedure"
        assert "digest" in result.value["path"]
        svc.auto_index.index_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_name(self, tmp_path):
        """Missing / blank name should be rejected."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(
                _valid_args(name="   "), _make_ctx())
        assert not result.ok
        assert "name is required" in result.error

    @pytest.mark.asyncio
    async def test_invalid_bucket(self, tmp_path):
        """bucket must be 'procedure' or 'wiki'."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(
                _valid_args(bucket="general"), _make_ctx())
        assert not result.ok
        assert "bucket must be" in result.error

    @pytest.mark.asyncio
    async def test_importance_out_of_range(self, tmp_path):
        """importance outside 0..1.0 should be rejected."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            low = await memory_store_handler(
                _valid_args(importance=-0.1), _make_ctx())
            high = await memory_store_handler(
                _valid_args(importance=1.5), _make_ctx())
        assert not low.ok and not high.ok
        assert "importance must be" in low.error
        assert "importance must be" in high.error

    @pytest.mark.asyncio
    async def test_empty_content(self, tmp_path):
        """Empty content should be rejected."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(
                _valid_args(content="   "), _make_ctx())
        assert not result.ok
        assert "content must be 1-" in result.error

    @pytest.mark.asyncio
    async def test_content_too_long(self, tmp_path):
        """Content over the limit should be rejected."""
        from app.tools.memory_store import MAX_CONTENT_LENGTH

        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(
                _valid_args(content="x" * (MAX_CONTENT_LENGTH + 1)), _make_ctx())
        assert not result.ok
        assert "content must be 1-" in result.error

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, tmp_path):
        """The (N+1)th call within the same run should be rate-limited."""
        svc = _make_mock_memory_service(tmp_path)
        with patch("app.main._memory_service", svc):
            ctx = _make_ctx()
            for i in range(MAX_WRITES_PER_RUN):
                result = await memory_store_handler(
                    _valid_args(name=f"mem-{i}"), ctx)
                assert result.ok, f"Call {i+1} should succeed"

            result = await memory_store_handler(
                _valid_args(name="one-too-many"), ctx)
            assert not result.ok
            assert "rate limit" in result.error

    @pytest.mark.asyncio
    async def test_memory_service_not_initialized(self, tmp_path):
        """When _memory_service is None, should return error."""
        with patch("app.main._memory_service", None):
            result = await memory_store_handler(_valid_args(), _make_ctx())
        assert not result.ok
        assert "Memory service not initialized" in result.error


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
