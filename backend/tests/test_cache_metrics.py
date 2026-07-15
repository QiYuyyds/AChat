"""Tests for CacheMetrics aggregator and cache_creation_tokens extraction.

Covers:
- Task 9.6: CacheMetrics.record(), recent_hit_rate(), should_alert() with mock data
- Task 9.8: cache_creation_tokens populated from cache_creation_input_tokens field
"""

from __future__ import annotations

from dataclasses import dataclass

# ─── Task 9.6: CacheMetrics unit tests ──────────────────────────────────────


class TestCacheMetricsRecord:
    def test_record_single_call(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        cm.record(cache_read=800, cache_creation=100, input_tokens=1000)
        assert cm.recent_count == 1

    def test_record_multiple_calls(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        cm.record(cache_read=800, input_tokens=1000)
        cm.record(cache_read=600, input_tokens=1000)
        assert cm.recent_count == 2


class TestCacheMetricsHitRate:
    def test_hit_rate_single_call(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        cm.record(cache_read=800, cache_creation=0, input_tokens=1000)
        assert cm.recent_hit_rate() == 0.8

    def test_hit_rate_multiple_calls(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        cm.record(cache_read=800, input_tokens=1000)
        cm.record(cache_read=600, input_tokens=1000)
        # (800 + 600) / (1000 + 1000) = 0.7
        assert cm.recent_hit_rate() == 0.7

    def test_hit_rate_empty(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics()
        assert cm.recent_hit_rate() == 0.0

    def test_hit_rate_zero_input(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        cm.record(cache_read=100, input_tokens=0)
        assert cm.recent_hit_rate() == 0.0


class TestCacheMetricsShouldAlert:
    def test_should_alert_below_threshold(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5, alert_threshold=0.5)
        cm.record(cache_read=0, input_tokens=1000)   # first call: always 0%
        cm.record(cache_read=100, input_tokens=1000)  # 10% hit rate
        assert cm.should_alert() is True

    def test_should_not_alert_single_record(self):
        """First call is always a cache miss; don't alert on a single record."""
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5, alert_threshold=0.5)
        cm.record(cache_read=0, input_tokens=1000)  # first call: 0%
        assert cm.should_alert() is False

    def test_should_not_alert_above_threshold(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=3, alert_threshold=0.5)
        cm.record(cache_read=800, input_tokens=1000)  # 80% hit rate
        assert cm.should_alert() is False

    def test_should_not_alert_empty(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics()
        assert cm.should_alert() is False

    def test_should_not_alert_zero_input(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=3, alert_threshold=0.5)
        cm.record(cache_read=0, input_tokens=0)
        assert cm.should_alert() is False

    def test_alert_threshold_boundary(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=3, alert_threshold=0.5)
        # Exactly 50% — should NOT alert (condition is < threshold, not <=)
        cm.record(cache_read=500, input_tokens=1000)
        assert cm.should_alert() is False


class TestCacheMetricsSlidingWindow:
    def test_sliding_window_evicts_old(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=3)
        cm.record(cache_read=1000, input_tokens=1000)  # 100%
        cm.record(cache_read=1000, input_tokens=1000)  # 100%
        cm.record(cache_read=0, input_tokens=1000)     # 0%
        cm.record(cache_read=0, input_tokens=1000)     # 0% — evicts first 100%
        # Window: [100%, 0%, 0%] → hit rate = 1000 / 3000 ≈ 0.333
        assert cm.recent_count == 3
        assert cm.recent_hit_rate() < 0.5

    def test_recent_count_property(self):
        from app.infra.cache_metrics import CacheMetrics

        cm = CacheMetrics(window_size=5)
        assert cm.recent_count == 0
        cm.record(cache_read=100, input_tokens=200)
        assert cm.recent_count == 1
        cm.record(cache_read=100, input_tokens=200)
        assert cm.recent_count == 2


# ─── Task 9.8: cache_creation_tokens extraction ─────────────────────────────


class TestCacheCreationTokensExtraction:
    def test_usage_field_extracts_cache_creation_input_tokens(self):
        """_usage_field correctly extracts cache_creation_input_tokens."""
        from app.adapters.custom_adapter import _usage_field

        @dataclass
        class FakeUsage:
            prompt_tokens: int = 1000
            completion_tokens: int = 100
            cache_creation_input_tokens: int = 500

        usage = FakeUsage()
        result = _usage_field(usage, "cache_creation_input_tokens")
        assert result == 500

    def test_usage_field_extracts_cache_creation_tokens(self):
        """_usage_field also supports the shorter 'cache_creation_tokens' name."""
        from app.adapters.custom_adapter import _usage_field

        @dataclass
        class FakeUsage:
            prompt_tokens: int = 1000
            completion_tokens: int = 100
            cache_creation_tokens: int = 300

        usage = FakeUsage()
        result = _usage_field(usage, "cache_creation_tokens")
        assert result == 300

    def test_usage_field_returns_zero_for_missing(self):
        """_usage_field returns 0 when the field doesn't exist."""
        from app.adapters.custom_adapter import _usage_field

        @dataclass
        class FakeUsage:
            prompt_tokens: int = 1000

        usage = FakeUsage()
        result = _usage_field(usage, "cache_creation_input_tokens")
        assert result == 0

    def test_run_usage_has_cache_creation_tokens_field(self):
        """_RunUsage dataclass has cache_creation_tokens field (initially 0)."""
        from app.adapters.custom_adapter import _RunUsage

        ru = _RunUsage()
        assert ru.cache_creation_tokens == 0

    def test_cache_creation_tokens_accumulated(self):
        """Simulate accumulation of cache_creation_tokens across multiple chunks."""
        from app.adapters.custom_adapter import _RunUsage, _usage_field

        @dataclass
        class FakeUsage:
            prompt_tokens: int = 1000
            completion_tokens: int = 100
            cache_creation_input_tokens: int = 500

        run_usage = _RunUsage()
        usage = FakeUsage()

        cache_created = _usage_field(usage, "cache_creation_input_tokens") or _usage_field(
            usage, "cache_creation_tokens"
        )
        run_usage.cache_creation_tokens += cache_created

        assert run_usage.cache_creation_tokens == 500
