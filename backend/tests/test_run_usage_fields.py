"""Tests for RunUsage new fields: lastCacheReadTokens / lastOutputTokens / turnCount.

Verifies:
- 7.1: Multi-turn ReAct run's RunUsageEvent carries correct snapshot fields + turnCount
- 7.2: Old agent_runs.usage JSON (missing new fields) deserializes with defaults of 0
"""

from app.adapters.custom_adapter import _RunUsage, _to_run_usage
from app.schemas.messages import RunUsage


class TestRunUsageFields:
    """7.1: Verify _to_run_usage fills new snapshot fields + turnCount."""

    def test_to_run_usage_fills_new_fields(self):
        u = _RunUsage(
            input_tokens=1000,
            output_tokens=200,
            cache_creation_tokens=0,
            cache_read_tokens=500,
            last_input_tokens=300,
            last_cache_read_tokens=150,
            last_output_tokens=60,
        )
        ru = _to_run_usage(u, "deepseek-chat", turn_count=7)
        assert ru.last_cache_read_tokens == 150
        assert ru.last_output_tokens == 60
        assert ru.turn_count == 7

    def test_to_run_usage_defaults_turn_count_to_zero(self):
        """Legacy stream() path passes turn_count=0 (no ReAct loop counting)."""
        u = _RunUsage(input_tokens=100, output_tokens=20)
        ru = _to_run_usage(u, "test-model")
        assert ru.turn_count == 0
        assert ru.last_cache_read_tokens == 0
        assert ru.last_output_tokens == 0

    def test_run_usage_serializes_camel_case_aliases(self):
        """Pydantic alias → JSON uses camelCase for frontend compatibility."""
        ru = RunUsage(
            input_tokens=100,
            output_tokens=20,
            cache_creation_tokens=0,
            cache_read_tokens=50,
            last_cache_read_tokens=10,
            last_output_tokens=5,
            turn_count=3,
        )
        data = ru.model_dump(by_alias=True)
        assert data["lastCacheReadTokens"] == 10
        assert data["lastOutputTokens"] == 5
        assert data["turnCount"] == 3

    def test_run_usage_backward_compat_missing_fields(self):
        """7.2: Old JSON lacking new fields deserializes to 0 (not error)."""
        old_json = {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 50,
            "lastInputTokens": 30,
            "model": "deepseek-chat",
        }
        ru = RunUsage.model_validate(old_json)
        assert ru.last_cache_read_tokens == 0
        assert ru.last_output_tokens == 0
        assert ru.turn_count == 0

    def test_run_usage_backward_compat_minimal_json(self):
        """Minimal old JSON with only required fields still works."""
        old_json = {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 0,
        }
        ru = RunUsage.model_validate(old_json)
        assert ru.last_cache_read_tokens == 0
        assert ru.last_output_tokens == 0
        assert ru.turn_count == 0
        assert ru.last_input_tokens is None
