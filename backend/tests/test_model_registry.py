"""Tests for app.utils.model_registry (port of src/shared/model-registry.ts)."""

from app.utils.model_registry import (
    DEFAULT_OUTPUT_RESERVE,
    EFFECTIVE_CONTEXT_CAP,
    PROVIDER_FALLBACK_CONTEXT,
    estimate_tokens,
    get_model_limits,
)


def test_known_model_uses_table_context_and_default_reserve():
    limits = get_model_limits("openai", "gpt-4o")
    assert limits.context_window == 128_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE
    assert limits.effective_context_window == 128_000  # below cap, no change


def test_known_model_with_explicit_output_reserve():
    limits = get_model_limits("deepseek", "deepseek-reasoner")
    assert limits.context_window == 1_000_000
    assert limits.output_reserve == 13_000
    assert limits.effective_context_window == EFFECTIVE_CONTEXT_CAP  # capped to 200K


def test_known_model_id_wins_over_provider():
    # claude-opus-4-7[1m] has 1M context regardless of provider fallback.
    limits = get_model_limits("anthropic", "claude-opus-4-7[1m]")
    assert limits.context_window == 1_000_000
    assert limits.effective_context_window == EFFECTIVE_CONTEXT_CAP  # capped to 200K


def test_unknown_model_falls_back_to_provider():
    limits = get_model_limits("deepseek", "some-unlisted-model")
    assert limits.context_window == PROVIDER_FALLBACK_CONTEXT["deepseek"]
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE
    assert limits.effective_context_window == EFFECTIVE_CONTEXT_CAP  # deepseek fallback is 1M, capped


def test_no_provider_no_model_uses_global_default():
    limits = get_model_limits(None, None)
    assert limits.context_window == 200_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE
    assert limits.effective_context_window == 200_000  # at cap, no change


def test_unknown_provider_uses_global_default():
    limits = get_model_limits("nonexistent", "also-unknown")
    assert limits.context_window == 200_000
    assert limits.output_reserve == DEFAULT_OUTPUT_RESERVE
    assert limits.effective_context_window == 200_000  # at cap, no change


def test_estimate_tokens_ceils_quarter_length():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1  # ceil(1/4)
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # ceil(5/4)
    assert estimate_tokens("x" * 40) == 10


def test_deepseek_models_have_13k_output_reserve():
    for model_id in (
        "deepseek-chat",
        "deepseek-v4-flash",
        "deepseek-v4",
        "deepseek-v4-pro",
        "deepseek-reasoner",
        "deepseek-r1",
    ):
        limits = get_model_limits("deepseek", model_id)
        assert limits.output_reserve == 13_000, f"{model_id} should have 13_000 output reserve"
        assert limits.effective_context_window == EFFECTIVE_CONTEXT_CAP
