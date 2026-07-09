"""Tests for the task_dispatch tool.

Verifies:
- Tool is registered in the global tool registry
- Tool definition has correct name, parameters, and description
- Tool handler returns error for non-existent agent (not exception)
"""

from __future__ import annotations

import pytest

from app.tools.base import ToolContext
from app.tools.registry import tool_registry
from app.tools.task_dispatch import TASK_DISPATCH_TOOL_NAME, task_dispatch_tool

# ─── Tool registration ────────────────────────────────────────────────────────


def test_task_dispatch_tool_name():
    assert TASK_DISPATCH_TOOL_NAME == "task_dispatch"


def test_task_dispatch_tool_registered():
    tool = tool_registry.get("task_dispatch")
    assert tool is not None
    assert tool.name == "task_dispatch"


def test_task_dispatch_tool_has_required_parameters():
    params = task_dispatch_tool.parameters
    props = params.get("properties", {})
    required = params.get("required", [])

    assert "agentId" in props
    assert "taskDescription" in props
    assert "agentId" in required
    assert "taskDescription" in required
    # dependsOn is optional
    assert "dependsOn" in props
    assert "dependsOn" not in required


def test_task_dispatch_tool_description_mentions_dispatch():
    assert "dispatch" in task_dispatch_tool.description.lower()


# ─── Tool handler error handling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_dispatch_nonexistent_agent_returns_error(db):
    """Handler returns error text (not exception) for unknown agent."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    conv_id = new_conversation_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="single", archived=False,
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))

    import asyncio

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        tool_names=[],
    )
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_nonexistent", "taskDescription": "do something"},
        ctx,
    )
    assert result.ok is False
    assert "not found" in result.error.lower() or "unknown" in result.error.lower()
