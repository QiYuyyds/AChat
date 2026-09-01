"""Tests for cacheStyle resolution and detection.

Verifies:
- 11.1: resolve_cache_style() priority chain (known provider → user-declared → detected → default)
- 11.2: detect_cache_style_from_usage() for various usage field combinations
- 11.3: _to_run_usage() fills cache_style correctly
- 11.4: Old agent_runs.usage JSON (no cacheStyle) deserializes with default 'deepseek'
"""

from types import SimpleNamespace

from app.adapters.custom_adapter import (
    _RunUsage,
    _to_run_usage,
    detect_cache_style_from_usage,
    resolve_cache_style,
)
from app.schemas.messages import RunUsage


class TestResolveCacheStyle:
    """11.1: resolve_cache_style() priority chain."""

    def test_known_provider_deepseek(self):
        assert resolve_cache_style('deepseek', None, None) == 'deepseek'

    def test_known_provider_anthropic(self):
        assert resolve_cache_style('anthropic', None, None) == 'anthropic'

    def test_known_provider_openai(self):
        assert resolve_cache_style('openai', None, None) == 'deepseek'

    def test_known_provider_volcano_ark(self):
        assert resolve_cache_style('volcano-ark', None, None) == 'deepseek'

    def test_known_provider_ignores_user_declaration(self):
        """Known providers are hardcoded; user declaration is ignored."""
        assert resolve_cache_style('deepseek', 'anthropic', None) == 'deepseek'

    def test_openai_compatible_user_declared(self):
        """openai-compatible with user-declared cache_style returns it."""
        assert resolve_cache_style('openai-compatible', 'anthropic', None) == 'anthropic'

    def test_openai_compatible_detected(self):
        """openai-compatible with no user declaration falls back to detected."""
        assert resolve_cache_style('openai-compatible', None, 'deepseek') == 'deepseek'

    def test_openai_compatible_user_overrides_detected(self):
        """User declaration takes priority over detected."""
        assert resolve_cache_style('openai-compatible', 'none', 'deepseek') == 'none'

    def test_openai_compatible_no_data_defaults_deepseek(self):
        """No user declaration, no detection → conservative default 'deepseek'."""
        assert resolve_cache_style('openai-compatible', None, None) == 'deepseek'

    def test_unknown_provider_defaults_deepseek(self):
        assert resolve_cache_style('some-unknown-provider', None, None) == 'deepseek'


class TestDetectCacheStyleFromUsage:
    """11.2: detect_cache_style_from_usage() for various usage field combinations."""

    def test_none_usage_returns_none(self):
        assert detect_cache_style_from_usage(None) is None

    def test_anthropic_via_cache_creation_input_tokens(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            cache_creation_input_tokens=500,
        )
        assert detect_cache_style_from_usage(usage) == 'anthropic'

    def test_anthropic_via_cache_creation_tokens(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            cache_creation_tokens=500,
        )
        assert detect_cache_style_from_usage(usage) == 'anthropic'

    def test_deepseek_via_prompt_cache_hit_tokens(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_cache_hit_tokens=500,
        )
        assert detect_cache_style_from_usage(usage) == 'deepseek'

    def test_deepseek_via_cached_tokens(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            cached_tokens=500,
        )
        assert detect_cache_style_from_usage(usage) == 'deepseek'

    def test_no_cache_fields_returns_none_style(self):
        """Usage present but no cache fields → 'none' (supports cache but none detected)."""
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
        )
        assert detect_cache_style_from_usage(usage) == 'none'

    def test_anthropic_takes_priority_over_deepseek(self):
        """If both cache_creation and cached_tokens present, anthropic wins."""
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            cache_creation_input_tokens=100,
            prompt_cache_hit_tokens=500,
        )
        assert detect_cache_style_from_usage(usage) == 'anthropic'

    def test_zero_cache_creation_field_is_anthropic_by_presence(self):
        """Presence-based detection (fix-openai-compat-cache-usage): a top-level
        cache_creation field classifies 'anthropic' even at 0 — value thresholds
        no longer apply, only field presence."""
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            cache_creation_input_tokens=0,
            cached_tokens=300,
        )
        assert detect_cache_style_from_usage(usage) == 'anthropic'

    def test_dict_usage_works(self):
        """detect_cache_style_from_usage also accepts dict-like objects (via getattr)."""
        usage = SimpleNamespace(
            prompt_cache_hit_tokens=200,
        )
        assert detect_cache_style_from_usage(usage) == 'deepseek'


class TestToRunUsageCacheStyle:
    """11.3: _to_run_usage() fills cache_style correctly."""

    def test_to_run_usage_fills_cache_style(self):
        u = _RunUsage(
            input_tokens=1000,
            output_tokens=200,
            cache_creation_tokens=0,
            cache_read_tokens=500,
            cache_style='anthropic',
        )
        ru = _to_run_usage(u, 'claude-sonnet-4', turn_count=3)
        assert ru.cache_style == 'anthropic'

    def test_to_run_usage_defaults_deepseek(self):
        """_RunUsage.cache_style defaults to 'deepseek'."""
        u = _RunUsage(input_tokens=100, output_tokens=20)
        ru = _to_run_usage(u, 'deepseek-chat')
        assert ru.cache_style == 'deepseek'

    def test_to_run_usage_serializes_camel_case(self):
        """Pydantic alias → JSON uses camelCase for frontend."""
        u = _RunUsage(
            input_tokens=100,
            output_tokens=20,
            cache_style='none',
        )
        ru = _to_run_usage(u, 'test-model')
        data = ru.model_dump(by_alias=True)
        assert data['cacheStyle'] == 'none'


class TestRunUsageBackwardCompat:
    """11.4: Old agent_runs.usage JSON (no cacheStyle) deserializes with default 'deepseek'."""

    def test_old_json_without_cache_style_defaults_deepseek(self):
        old_json = {
            'inputTokens': 100,
            'outputTokens': 20,
            'cacheCreationTokens': 0,
            'cacheReadTokens': 50,
        }
        ru = RunUsage(**old_json)
        assert ru.cache_style == 'deepseek'

    def test_old_json_with_cache_creation_infers_anthropic_via_default(self):
        """Old JSON without cacheStyle but with cacheCreationTokens > 0 still defaults
        to 'deepseek' on the backend (frontend inferCacheStyle handles the fallback)."""
        old_json = {
            'inputTokens': 100,
            'outputTokens': 20,
            'cacheCreationTokens': 50,
            'cacheReadTokens': 30,
        }
        ru = RunUsage(**old_json)
        # Backend default is 'deepseek' regardless of cacheCreationTokens
        assert ru.cache_style == 'deepseek'

    def test_new_json_with_cache_style_preserved(self):
        new_json = {
            'inputTokens': 100,
            'outputTokens': 20,
            'cacheCreationTokens': 50,
            'cacheReadTokens': 30,
            'cacheStyle': 'anthropic',
        }
        ru = RunUsage(**new_json)
        assert ru.cache_style == 'anthropic'
