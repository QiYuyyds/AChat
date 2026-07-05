"""Unit tests for _maybe_auto_compact_hook (Task 3.5).

Verifies:
- watermark >= 10 triggers compact_conversation(silent=True)
- watermark < 10 does not trigger
- CompactionSkipped is swallowed silently
- override_prompt non-empty exempts (sub-agent runs)
- hook exception does not propagate (best-effort)
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
        mock_count.return_value = 15
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
        mock_count.return_value = 5

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
        mock_count.return_value = 10
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
        mock_count.return_value = 20
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
        mock_count.return_value = 20
        mock_compact.side_effect = RuntimeError("Unexpected DB error")

        # Should not raise
        await _maybe_auto_compact_hook("conv_test", override_prompt=None)

        mock_compact.assert_awaited_once_with("conv_test", silent=True)
