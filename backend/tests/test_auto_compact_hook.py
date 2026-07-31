"""Unit tests for _maybe_auto_compact_hook.

Verifies:
- token usage > 87% of model limit triggers compact_conversation(silent=True)
- token usage <= 87% does not trigger
- CompactionSkipped is swallowed silently
- override_prompt non-empty exempts (sub-agent runs)
- hook exception does not propagate (best-effort)
- agent_id=None does NOT trigger compaction (safe degradation)
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import agent_runner
from app.services.agent_runner import _maybe_auto_compact_hook
from app.services.context_compaction_service import CompactionSkipped


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
    """Token usage > 87% of model limit triggers compaction."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_limit.return_value = 1_000_000  # deepseek V4 context window
        mock_tokens.return_value = 900_000  # > 87% of 1M = 870K
        mock_compact.return_value = _mock_compact_result()

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_neither_threshold_met_does_not_compact():
    """When token usage is below 87%, no compaction."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_limit.return_value = 1_000_000
        mock_tokens.return_value = 100_000  # well below 87% of 1M

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_agent_id_does_not_trigger():
    """Without agent_id, auto-compact does not trigger (safe degradation)."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id=None)

        mock_tokens.assert_not_awaited()
        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_override_prompt_non_empty_exempts():
    """override_prompt non-empty (sub-agent run) skips auto-compaction."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        await _maybe_auto_compact_hook(
            "conv_test", override_prompt="orchestrator sub-task prompt"
        )

        # estimate_uncompacted_tokens should NOT be called (guard exits early)
        mock_tokens.assert_not_awaited()
        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_compaction_skipped_is_swallowed():
    """CompactionSkipped exception is caught and does not propagate."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_limit.return_value = 1_000_000
        mock_tokens.return_value = 900_000  # > 87% threshold
        mock_compact.side_effect = CompactionSkipped("compactable_too_small", None)

        # Should not raise
        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_general_exception_does_not_propagate():
    """General exception inside hook is caught and does not propagate."""
    with patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_limit.return_value = 1_000_000
        mock_tokens.return_value = 900_000  # > 87% threshold
        mock_compact.side_effect = RuntimeError("Unexpected DB error")

        # Should not raise
        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_compact.assert_awaited_once_with("conv_test", silent=True)


@pytest.mark.asyncio
async def test_model_limit_unknown_does_not_trigger():
    """When model limit cannot be resolved, no compaction triggers."""
    with patch.object(
        agent_runner, "_get_agent_model_limit", new_callable=AsyncMock
    ) as mock_limit, patch.object(
        agent_runner, "estimate_uncompacted_tokens", new_callable=AsyncMock
    ) as mock_tokens, patch.object(
        agent_runner, "compact_conversation", new_callable=AsyncMock
    ) as mock_compact:
        mock_limit.return_value = None  # model limit unknown

        await _maybe_auto_compact_hook("conv_test", override_prompt=None, agent_id="ag_1")

        mock_tokens.assert_not_awaited()
        mock_compact.assert_not_awaited()
