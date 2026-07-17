"""Unit tests for _maybe_auto_compact_hook.

Verifies:
- watermark >= 10 triggers compact_conversation(silent=True)
- watermark < 10 does not trigger
- CompactionSkipped is swallowed silently
- override_prompt non-empty exempts (sub-agent runs)
- hook exception does not propagate (best-effort)
- O1: token-based trigger fires when estimated tokens > 87% of model limit
- O1: neither threshold met → no compaction
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import agent_runner
from app.services.agent_runner import _maybe_auto_compact_hook
from app.services.context_compaction_service import CompactionSkipped


@pytest.mark.asyncio
async def test_watermark_reached_triggers_silent_compact():
    """Watermark >= 10 triggers compact_conversation(silent=True)."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 35  # above AUTO_COMPACT_WATERMARK (30)
        mock_compact.return_value = type("R", (), {
            "summary": type("S", (), {
                "id": "cs_1",
                "source_message_count": 9,
            })(),
            "ctx_before": 5000,
            "ctx_after": 2000,
            "message": None,
        })()

        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_count.assert_awaited_once_with("conv_test")
        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_watermark_below_threshold_does_not_trigger():
    """Watermark < 10 does not trigger compact_conversation."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 5  # below AUTO_COMPACT_WATERMARK (30)

        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_count.assert_awaited_once_with("conv_test")
        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_watermark_exactly_at_threshold_triggers():
    """Watermark == 10 (boundary) triggers compact_conversation."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 30  # exactly at AUTO_COMPACT_WATERMARK (30)
        mock_compact.return_value = type("R", (), {
            "summary": type("S", (), {
                "id": "cs_1",
                "source_message_count": 4,
            })(),
            "ctx_before": 5000,
            "ctx_after": 2000,
            "message": None,
        })()

        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_compaction_skipped_is_swallowed():
    """CompactionSkipped exception is caught and does not propagate."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 35  # above AUTO_COMPACT_WATERMARK (30)
        mock_compact.side_effect = CompactionSkipped("compactable_too_small", None)

        # Should not raise
        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_override_prompt_non_empty_exempts():
    """override_prompt non-empty (sub-agent run) skips auto-compaction."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        # Even with high watermark, should not trigger
        mock_count.return_value = 100

        await _maybe_auto_compact_hook(
            "conv_test", override_prompt="orchestrator sub-task prompt"
        )

        # count_uncompacted_messages should NOT be called (guard exits early)
        mock_count.assert_not_awaited()
        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_exception_does_not_propagate():
    """General exception inside hook is caught and does not propagate."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 35  # above AUTO_COMPACT_WATERMARK (30)
        mock_compact.side_effect = RuntimeError("Unexpected DB error")

        # Should not raise
        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


# ─── O1: token-based trigger tests ──────────────────────────────────────────


def _mock_compact_result():
    return type("R", (), {
        "summary": type("S", (), {
            "id": "cs_tok",
            "source_message_count": 5,
        })(),
        "ctx_before": 60000,
        "ctx_after": 20000,
        "message": None,
    })()


@pytest.mark.asyncio
async def test_token_threshold_triggers_compaction():
    """Token usage > 87% of model limit triggers compaction even with low watermark."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 5  # below watermark threshold
        mock_limit.return_value = 1_000_000  # deepseek V4 context window
        mock_tokens.return_value = 900_000  # > 87% of 1M = 870K
        mock_compact.return_value = _mock_compact_result()

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_neither_threshold_met_does_not_compact():
    """When both watermark and token usage are below thresholds, no compaction."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 5  # below watermark
        mock_limit.return_value = 1_000_000
        mock_tokens.return_value = 100_000  # well below 87% of 1M

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_agent_id_falls_back_to_watermark_only():
    """Without agent_id, only the watermark check applies (no token estimation)."""
    with patch.object(
        agent_runner, "count_uncompacted_messages", new_callable=AsyncMock
    ) as mock_count, patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_count.return_value = 5  # below watermark

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id=None)

        mock_tokens.assert_not_awaited()
        mock_compact.assert_not_awaited()
