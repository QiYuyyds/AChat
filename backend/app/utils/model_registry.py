"""Model context windows and output reserves.

Port of src/shared/model-registry.ts. Used by the agent-runner token budget
(historyBudget = contextWindow - outputReserve - estimate(prompts) - margin).

Maintenance note: tables hold conservative lower bounds. Unknown model ids fall
back to the provider default (and finally a global default), so they still run —
just with a more conservative budget. Add an id to KNOWN_MODELS for accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ModelProvider literals from src/shared/types.ts.
ModelProvider = str

# Per-provider total context window (tokens) used when a model id is unknown.
PROVIDER_FALLBACK_CONTEXT: dict[ModelProvider, int] = {
    "anthropic": 200_000,
    "openai": 128_000,
    "deepseek": 1_000_000,
    "volcano-ark": 32_000,
    "openai-compatible": 128_000,
}

# Default output reserve; 4K bottom line covers reasoning thinking tokens too.
DEFAULT_OUTPUT_RESERVE = 4096

KNOWN_MODELS: dict[str, dict[str, int]] = {
    # DeepSeek V4 — deepseek-chat/reasoner map to v4-flash non-thinking/thinking modes
    # Source: https://api-docs.deepseek.com/quick_start/pricing (July 2026)
    "deepseek-chat": {"context": 1_000_000, "maxOutputTokens": 384_000},
    "deepseek-v4-flash": {"context": 1_000_000, "maxOutputTokens": 384_000},
    "deepseek-v4": {"context": 1_000_000, "maxOutputTokens": 384_000},
    "deepseek-v4-pro": {"context": 1_000_000, "maxOutputTokens": 384_000},
    "deepseek-reasoner": {"context": 1_000_000, "outputReserve": 16_384, "maxOutputTokens": 384_000},
    "deepseek-r1": {"context": 1_000_000, "outputReserve": 16_384, "maxOutputTokens": 384_000},
    # OpenAI
    "gpt-4o": {"context": 128_000, "maxOutputTokens": 16_384},
    "gpt-4o-mini": {"context": 128_000, "maxOutputTokens": 16_384},
    "gpt-4-turbo": {"context": 128_000, "maxOutputTokens": 4_096},
    "gpt-4": {"context": 8192, "maxOutputTokens": 4_096},
    "gpt-3.5-turbo": {"context": 16_385, "maxOutputTokens": 4_096},
    "o1": {"context": 200_000, "outputReserve": 32_768, "maxOutputTokens": 100_000},
    "o1-mini": {"context": 128_000, "outputReserve": 16_384, "maxOutputTokens": 65_536},
    # Anthropic
    "claude-opus-4-7": {"context": 200_000},
    "claude-opus-4-7[1m]": {"context": 1_000_000},
    "claude-sonnet-4-6": {"context": 200_000},
    "claude-opus-4-6": {"context": 200_000},
    "claude-opus-4-5": {"context": 200_000},
    "claude-sonnet-4-5": {"context": 200_000},
    "claude-3-5-sonnet-latest": {"context": 200_000},
    "claude-haiku-4-5-20251001": {"context": 200_000},
    # Volcano Ark / 豆包
    "doubao-seed-2-0-lite-260428": {"context": 32_000},
    "doubao-1-5-pro-256k": {"context": 256_000},
    "doubao-pro-128k": {"context": 128_000},
}


@dataclass(frozen=True)
class ModelLimits:
    context_window: int  # total context window (tokens)
    output_reserve: int  # tokens reserved for output; input + output <= context_window
    max_output_tokens: int | None = None  # provider hard cap on output tokens, if known


def get_model_limits(
    provider: ModelProvider | None,
    model_id: str | None,
) -> ModelLimits:
    if model_id and model_id in KNOWN_MODELS:
        m = KNOWN_MODELS[model_id]
        return ModelLimits(
            context_window=m["context"],
            output_reserve=m.get("outputReserve", DEFAULT_OUTPUT_RESERVE),
            max_output_tokens=m.get("maxOutputTokens"),
        )
    # Provider fallback.
    if provider and provider in PROVIDER_FALLBACK_CONTEXT:
        return ModelLimits(
            context_window=PROVIDER_FALLBACK_CONTEXT[provider],
            output_reserve=DEFAULT_OUTPUT_RESERVE,
            max_output_tokens=None,
        )
    # Final fallback (also used by ClaudeCode adapter — it has no modelProvider field).
    return ModelLimits(context_window=200_000, output_reserve=DEFAULT_OUTPUT_RESERVE)


def estimate_tokens(text: str) -> int:
    """Coarse token estimate: 4 chars ≈ 1 token (10-20% error, fine for budgeting)."""
    return math.ceil(len(text) / 4)
