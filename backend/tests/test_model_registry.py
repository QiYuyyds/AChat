"""Tests for app.utils.model_registry (port of src/shared/model-registry.ts)."""

from app.utils.model_registry import (
    DEFAULT_OUTPUT_RESERVE,
    PROVIDER_FALLBACK_CONTEXT,
    estimate_tokens,
    get_model_limits,
)


def test_known_model_uses_table_context_and_default_reserve():
    limits = get_model_limits("openai", "gpt-4o")
    assert limits.context_window == 128_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE


def test_known_model_with_explicit_output_reserve():
    limits = get_model_limits("deepseek", "deepseek-reasoner")
    assert limits.context_window == 128_000
    assert limits.output_reserve == 16_384


def test_known_model_id_wins_over_provider():
    # claude-opus-4-7[1m] has 1M context regardless of provider fallback.
    limits = get_model_limits("anthropic", "claude-opus-4-7[1m]")
    assert limits.context_window == 1_000_000


def test_unknown_model_falls_back_to_provider():
    limits = get_model_limits("deepseek", "some-unlisted-model")
    assert limits.context_window == PROVIDER_FALLBACK_CONTEXT["deepseek"]
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE


def test_no_provider_no_model_uses_global_default():
    limits = get_model_limits(None, None)
    assert limits.context_window == 200_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE


def test_unknown_provider_uses_global_default():
    limits = get_model_limits("nonexistent", "also-unknown")
    assert limits.context_window == 200_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE


def test_estimate_tokens_ceils_quarter_length():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1  # ceil(1/4)
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # ceil(5/4)
    assert estimate_tokens("x" * 40) == 10
