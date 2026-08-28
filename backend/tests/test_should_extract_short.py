"""Tests for SessionMemory.should_extract_short — short-conversation first extraction.

Verifies:
- 2 tool turns + no existing Note + natural breakpoint → True
- 1 tool turn → False (below threshold)
- Existing Note → False (first extraction only)
- No generate_fn → False (degradation)
- Unresolved tool_use → False (not at natural breakpoint)
- No messages → False
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.memory.session_memory import SessionMemory, SessionMemoryRecord
from tests.test_session_memory import _make_msg

# ─── 2 tool turns → True ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_true_with_2_tool_turns():
    """2 tool_use parts + no existing Note + natural breakpoint → True."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "short msg"}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                    {"type": "tool_use", "callId": "c2", "toolName": "fs_read"},
                ]),
                _make_msg("m3", 300, [
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                    {"type": "tool_result", "callId": "c2", "result": "ok", "isError": False},
                ]),
            ]
            assert await sm.should_extract_short("conv1") is True


# ─── 1 tool turn → False ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_with_1_tool_turn():
    """1 tool_use part → below TOOL_CALLS_BETWEEN_UPDATES (2) → False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "short msg"}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                ]),
                _make_msg("m3", 300, [
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                ]),
            ]
            assert await sm.should_extract_short("conv1") is False


# ─── existing Note → False ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_with_existing_note():
    """Existing session Note → should_extract_short returns False (first extraction only)."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    existing = SessionMemoryRecord(summary="existing summary", covers_up_to=100.0)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = existing
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "msg"}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                    {"type": "tool_use", "callId": "c2", "toolName": "fs_read"},
                ]),
                _make_msg("m3", 300, [
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                    {"type": "tool_result", "callId": "c2", "result": "ok", "isError": False},
                ]),
            ]
            assert await sm.should_extract_short("conv1") is False


# ─── no generate_fn → False ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_without_generate_fn():
    """No _generate_fn → should_extract_short always returns False."""
    sm = SessionMemory(generate_fn=None)
    assert await sm.should_extract_short("conv1") is False


# ─── unresolved tool_use → False ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_during_tool_chain():
    """Last message has unresolved tool_use → not at natural breakpoint → False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "msg"}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                    {"type": "tool_use", "callId": "c2", "toolName": "fs_read"},
                ]),
                # c2 has no matching tool_result in any message
            ]
            assert await sm.should_extract_short("conv1") is False


# ─── no messages → False ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_no_messages():
    """No messages loaded → False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = []
            assert await sm.should_extract_short("conv1") is False


# ─── 0 tool turns → False ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_short_false_no_tool_turns():
    """Text-only conversation, no tool_use parts → False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "hello"}]),
                _make_msg("m2", 200, [{"type": "text", "content": "world"}]),
            ]
            assert await sm.should_extract_short("conv1") is False


# ─── loads messages with None covers_up_to (first extraction) ─────────────────


@pytest.mark.asyncio
async def test_should_extract_short_loads_all_messages():
    """should_extract_short loads messages since None (all messages, first extraction)."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "msg"}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                    {"type": "tool_use", "callId": "c2", "toolName": "fs_read"},
                ]),
                _make_msg("m3", 300, [
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                    {"type": "tool_result", "callId": "c2", "result": "ok", "isError": False},
                ]),
            ]
            await sm.should_extract_short("conv_test")

            # Verify _load_messages_since was called with covers_up_to=None
            call_args = mock_load.call_args
            assert call_args.args[1] is None
