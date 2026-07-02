"""Unit tests for consume_stream terminal-event behavior (report_task_result)."""

from __future__ import annotations

import asyncio
from typing import AsyncIterable
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.agent_runner import consume_stream


async def _async_iter(events: list) -> AsyncIterable:
    for ev in events:
        yield ev


async def _mock_persist(event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids):
    """Lightweight stand-in for persist_event that only tracks output_message_ids."""
    if getattr(event, "type", None) == "message.start":
        output_message_ids.append(event.message_id)


@pytest.mark.asyncio
async def test_report_task_result_terminal_stops_stream():
    """consume_stream with require_task_report=True stops after report_task_result."""
    conv_id = "conv_test"
    msg_id = "msg_test"
    run_id = "run_test"
    agent_id = "agent_test"
    call_id = "call_1"

    events = [
        MessageStartEvent(
            conversationId=conv_id,
            timestamp=1000,
            messageId=msg_id,
            agentId=agent_id,
            runId=run_id,
        ),
        ToolCallEvent(
            conversationId=conv_id,
            timestamp=2000,
            messageId=msg_id,
            callId=call_id,
            toolName="report_task_result",
            args={
                "status": "complete",
                "summary": "Task done",
                "acceptanceResults": [
                    {"criterion": "builds", "passed": True, "evidence": "exit 0"}
                ],
            },
        ),
        # This event should NOT be consumed because the stream breaks after
        # the report_task_result tool.call.
        ToolCallEvent(
            conversationId=conv_id,
            timestamp=3000,
            messageId=msg_id,
            callId="call_2",
            toolName="some_other_tool",
            args={},
        ),
    ]

    # Mock persist_event to avoid database writes but still track message ids
    with patch(
        "app.services.agent_runner.persist_event", new=_mock_persist
    ), patch("app.services.agent_runner.publish"):
        result = await consume_stream(
            _async_iter(events),
            agent_id,
            run_id,
            require_task_report=True,
        )

    # task_report should be set from the tool.call args
    assert result.task_report is not None
    assert result.task_report["status"] == "complete"
    assert result.task_report["summary"] == "Task done"

    # Only one output message (the stream stopped before a second message)
    assert len(result.output_message_ids) == 1


@pytest.mark.asyncio
async def test_report_task_result_terminal_with_mcp_prefixed_name():
    """consume_stream detects MCP-prefixed report_task_result tool names."""
    conv_id = "conv_test"
    msg_id = "msg_test"
    run_id = "run_test"
    agent_id = "agent_test"
    call_id = "call_1"

    events = [
        MessageStartEvent(
            conversationId=conv_id,
            timestamp=1000,
            messageId=msg_id,
            agentId=agent_id,
            runId=run_id,
        ),
        ToolCallEvent(
            conversationId=conv_id,
            timestamp=2000,
            messageId=msg_id,
            callId=call_id,
            toolName="mcp__achat-tools__report_task_result",
            args={
                "status": "complete",
                "summary": "Done via MCP",
            },
        ),
    ]

    with patch(
        "app.services.agent_runner.persist_event", new=AsyncMock()
    ), patch("app.services.agent_runner.publish"):
        result = await consume_stream(
            _async_iter(events),
            agent_id,
            run_id,
            require_task_report=True,
        )

    assert result.task_report is not None
    assert result.task_report["status"] == "complete"
    assert result.task_report["summary"] == "Done via MCP"


@pytest.mark.asyncio
async def test_non_child_run_unaffected_by_terminal_logic():
    """consume_stream without require_task_report does NOT stop on report_task_result."""
    conv_id = "conv_test"
    msg_id = "msg_test"
    run_id = "run_test"
    agent_id = "agent_test"
    call_id = "call_1"

    extra_msg_id = "msg_2"
    events = [
        MessageStartEvent(
            conversationId=conv_id,
            timestamp=1000,
            messageId=msg_id,
            agentId=agent_id,
            runId=run_id,
        ),
        ToolCallEvent(
            conversationId=conv_id,
            timestamp=2000,
            messageId=msg_id,
            callId=call_id,
            toolName="report_task_result",
            args={"status": "complete", "summary": "Done"},
        ),
        ToolResultEvent(
            conversationId=conv_id,
            timestamp=2500,
            messageId=msg_id,
            callId=call_id,
            result={"status": "complete", "summary": "Done"},
            isError=False,
        ),
        MessageEndEvent(
            conversationId=conv_id,
            timestamp=3000,
            messageId=msg_id,
        ),
        # A second message — proves the stream was NOT terminated
        MessageStartEvent(
            conversationId=conv_id,
            timestamp=4000,
            messageId=extra_msg_id,
            agentId=agent_id,
            runId=run_id,
        ),
        MessageEndEvent(
            conversationId=conv_id,
            timestamp=5000,
            messageId=extra_msg_id,
        ),
    ]

    with patch(
        "app.services.agent_runner.persist_event", new=_mock_persist
    ), patch("app.services.agent_runner.publish"):
        result = await consume_stream(
            _async_iter(events),
            agent_id,
            run_id,
            require_task_report=False,
        )

    # Stream consumed ALL events (2 messages)
    assert len(result.output_message_ids) == 2
    # task_report captured from tool.result (normal path)
    assert result.task_report is not None
    assert result.task_report["status"] == "complete"
