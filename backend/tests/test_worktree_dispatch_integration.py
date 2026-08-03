"""Integration tests for worktree integration into dispatch flow.

Covers tasks 7.7-7.9 from the fix-worktree-merge-conflict change:
- 7.7: dag_executor._execute_node worktree create/merge-back/cleanup
- 7.8: task_dispatch._handler worktree create/merge-back/cleanup
- 7.9: create_worktree() returning None degrades to shared workspace
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.dispatch import DispatchPlanItem
from app.services.dag_executor import DagExecContext, _execute_node
from app.services.worktree_service import MergeResult, WorktreeRef


def _make_mock_db(agent_name="TestAgent", trigger_msg_id="msg_test"):
    """Create a mock async DB session that returns a canned agent + parent run.

    Call order: 1st = parent_run lookup, 2nd = agent name lookup.
    """
    mock_agent = MagicMock()
    mock_agent.name = agent_name

    mock_parent_run = MagicMock()
    mock_parent_run.trigger_message_id = trigger_msg_id

    call_count = [0]

    @asynccontextmanager
    async def _mock_get_local_db():
        mock_db = AsyncMock()
        mock_result = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            mock_result.scalar_one_or_none.return_value = mock_parent_run
        else:
            mock_result.scalar_one_or_none.return_value = mock_agent
        mock_db.execute = AsyncMock(return_value=mock_result)
        yield mock_db

    return _mock_get_local_db


# ─── 7.7: dag_executor._execute_node worktree flow ──────────────────────────

@pytest.mark.asyncio
async def test_dag_execute_node_worktree_full_flow(monkeypatch):
    """When workspace_path is set and agent exists, worktree is created,
    merged back, and cleaned up."""
    task = DispatchPlanItem(id="t1", agentId="ag_test", task="do work")

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        workspace_path="/fake/workspace",
        user_id="user1",
    )

    monkeypatch.setattr("app.db.engine.get_local_db", _make_mock_db())

    # Mock worktree functions
    wt_ref = WorktreeRef(
        task_id="t1",
        branch_name="agent/test-agent/t1",
        path="/fake/wt",
        main_workspace_path="/fake/workspace",
        is_git=True,
        conversation_id="conv_test",
        user_id="user1",
        agent_id="ag_test",
    )

    created_wt = []
    merged_wt = []
    cleaned_wt = []

    async def _mock_create(*args, **kwargs):
        created_wt.append(kwargs.get("task_id"))
        return wt_ref

    async def _mock_merge(wt):
        merged_wt.append(wt)
        return MergeResult(success=True, resolution_strategy="auto")

    async def _mock_cleanup(wt):
        cleaned_wt.append(wt)

    monkeypatch.setattr("app.services.worktree_service.create_worktree", _mock_create)
    monkeypatch.setattr("app.services.worktree_service.merge_worktree_back", _mock_merge)
    monkeypatch.setattr("app.services.worktree_service.cleanup_worktree", _mock_cleanup)

    # Mock spawn_fn
    async def _mock_spawn(**kwargs):
        from app.services.agent_loop import LoopRunResult
        assert kwargs.get("workspace_path") == "/fake/wt"
        return LoopRunResult(status="complete", text="done", run_id="run_t1")

    result = await _execute_node(task, ctx, _mock_spawn)

    assert result.status == "complete"
    assert len(created_wt) == 1
    assert len(merged_wt) == 1
    assert len(cleaned_wt) == 1
    assert merged_wt[0] is wt_ref
    assert cleaned_wt[0] is wt_ref


# ─── 7.9: create_worktree returns None degrades to shared workspace ──────────

@pytest.mark.asyncio
async def test_dag_execute_node_worktree_none_degrades(monkeypatch):
    """When create_worktree returns None, spawn is called without workspace_path
    and no merge-back/cleanup is performed."""
    task = DispatchPlanItem(id="t1", agentId="ag_test", task="do work")

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        workspace_path="/fake/workspace",
    )

    monkeypatch.setattr("app.db.engine.get_local_db", _make_mock_db())

    async def _mock_create_none(*args, **kwargs):
        return None

    merge_called = []
    cleanup_called = []

    async def _mock_merge(wt):
        merge_called.append(wt)

    async def _mock_cleanup(wt):
        cleanup_called.append(wt)

    monkeypatch.setattr("app.services.worktree_service.create_worktree", _mock_create_none)
    monkeypatch.setattr("app.services.worktree_service.merge_worktree_back", _mock_merge)
    monkeypatch.setattr("app.services.worktree_service.cleanup_worktree", _mock_cleanup)

    async def _mock_spawn(**kwargs):
        from app.services.agent_loop import LoopRunResult
        assert kwargs.get("workspace_path") is None
        return LoopRunResult(status="complete", text="done", run_id="run_t1")

    result = await _execute_node(task, ctx, _mock_spawn)

    assert result.status == "complete"
    assert len(merge_called) == 0
    assert len(cleanup_called) == 0


@pytest.mark.asyncio
async def test_dag_execute_node_no_workspace_path_skips_worktree(monkeypatch):
    """When workspace_path is empty, no worktree is created at all."""
    task = DispatchPlanItem(id="t1", agentId="ag_test", task="do work")

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        workspace_path="",
    )

    create_called = []

    async def _mock_create(*args, **kwargs):
        create_called.append(True)
        return None

    monkeypatch.setattr("app.services.worktree_service.create_worktree", _mock_create)

    async def _mock_spawn(**kwargs):
        from app.services.agent_loop import LoopRunResult
        assert kwargs.get("workspace_path") is None
        return LoopRunResult(status="complete", text="done", run_id="run_t1")

    result = await _execute_node(task, ctx, _mock_spawn)

    assert result.status == "complete"
    assert len(create_called) == 0


# ─── 7.8: task_dispatch._handler worktree flow ───────────────────────────────

@pytest.mark.asyncio
async def test_task_dispatch_handler_worktree_flow(monkeypatch):
    """task_dispatch._handler creates worktree, passes path, merges back, cleans up."""
    from app.tools.base import ToolContext
    from app.tools.task_dispatch import _handler

    ctx = ToolContext(
        conversation_id="conv_test",
        workspace_path="/fake/workspace",
        agent_id="ag_caller",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="solo",
    )

    args = {"taskDescription": "do subtask"}

    from app.services.agent_loop import LoopRunResult

    spawn_workspace_path = []

    async def _mock_spawn(**kwargs):
        spawn_workspace_path.append(kwargs.get("workspace_path"))
        return LoopRunResult(status="complete", text="subtask done", run_id="run_sub")

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", _mock_spawn)
    monkeypatch.setattr("app.services.agent_loop.MAX_DISPATCH_DEPTH", 3)

    monkeypatch.setattr("app.tools.task_dispatch.get_local_db", _make_mock_db("CallerAgent"))

    # Mock worktree functions
    wt_ref = WorktreeRef(
        task_id="call_test",
        branch_name="agent/caller-agent/call_test",
        path="/fake/wt",
        main_workspace_path="/fake/workspace",
        is_git=True,
        conversation_id="conv_test",
        agent_id="ag_caller",
    )

    merged = []
    cleaned = []

    async def _mock_create(*args, **kwargs):
        return wt_ref

    async def _mock_merge(wt):
        merged.append(wt)
        return MergeResult(success=True)

    async def _mock_cleanup(wt):
        cleaned.append(wt)

    monkeypatch.setattr("app.services.worktree_service.create_worktree", _mock_create)
    monkeypatch.setattr("app.services.worktree_service.merge_worktree_back", _mock_merge)
    monkeypatch.setattr("app.services.worktree_service.cleanup_worktree", _mock_cleanup)

    result = await _handler(args, ctx)

    assert result.ok is True
    assert spawn_workspace_path[0] == "/fake/wt"
    assert len(merged) == 1
    assert len(cleaned) == 1


@pytest.mark.asyncio
async def test_task_dispatch_handler_worktree_none_degrades(monkeypatch):
    """When create_worktree returns None, task_dispatch proceeds without worktree."""
    from app.tools.base import ToolContext
    from app.tools.task_dispatch import _handler

    ctx = ToolContext(
        conversation_id="conv_test",
        workspace_path="/fake/workspace",
        agent_id="ag_caller",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="solo",
    )

    args = {"taskDescription": "do subtask"}

    from app.services.agent_loop import LoopRunResult

    spawn_workspace_path = []

    async def _mock_spawn(**kwargs):
        spawn_workspace_path.append(kwargs.get("workspace_path"))
        return LoopRunResult(status="complete", text="done", run_id="run_sub")

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", _mock_spawn)
    monkeypatch.setattr("app.services.agent_loop.MAX_DISPATCH_DEPTH", 3)

    monkeypatch.setattr("app.tools.task_dispatch.get_local_db", _make_mock_db("CallerAgent"))

    async def _mock_create_none(*args, **kwargs):
        return None

    merged = []
    cleaned = []

    async def _mock_merge(wt):
        merged.append(wt)

    async def _mock_cleanup(wt):
        cleaned.append(wt)

    monkeypatch.setattr("app.services.worktree_service.create_worktree", _mock_create_none)
    monkeypatch.setattr("app.services.worktree_service.merge_worktree_back", _mock_merge)
    monkeypatch.setattr("app.services.worktree_service.cleanup_worktree", _mock_cleanup)

    result = await _handler(args, ctx)

    assert result.ok is True
    assert spawn_workspace_path[0] is None
    assert len(merged) == 0
    assert len(cleaned) == 0
