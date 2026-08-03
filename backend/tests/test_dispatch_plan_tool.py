"""Tests for the dispatch_plan tool.

Verifies:
- Tool is registered in the global tool registry
- Tool definition has correct name, parameters, and description
- Handler: valid plan returns results map (mocked execute_dag)
- Handler: invalid plan returns error
- Handler: agent-not-in-conversation rejected
- Plan approval flow: approve executes, reject returns rejected, cancel returns aborted
"""

from __future__ import annotations

import asyncio

import pytest

from app.tools.base import ToolContext
from app.tools.dispatch_plan import DISPATCH_PLAN_TOOL_NAME, dispatch_plan_tool
from app.tools.registry import tool_registry

# ─── Tool registration ────────────────────────────────────────────────────────


def test_dispatch_plan_tool_name():
    assert DISPATCH_PLAN_TOOL_NAME == "dispatch_plan"


def test_dispatch_plan_tool_registered():
    tool = tool_registry.get("dispatch_plan")
    assert tool is not None
    assert tool.name == "dispatch_plan"


def test_dispatch_plan_tool_has_required_parameters():
    params = dispatch_plan_tool.parameters
    props = params.get("properties", {})
    required = params.get("required", [])

    assert "tasks" in props
    assert "tasks" in required
    task_item_props = props["tasks"]["items"]["properties"]
    assert "id" in task_item_props
    assert "agentId" in task_item_props
    assert "task" in task_item_props
    assert "dependsOn" in task_item_props
    item_required = props["tasks"]["items"]["required"]
    assert "id" in item_required
    assert "agentId" not in item_required  # agentId is now optional
    assert "task" in item_required
    assert "dependsOn" not in item_required


def test_dispatch_plan_tool_description_mentions_dag():
    desc = dispatch_plan_tool.description.lower()
    assert "dag" in desc or "depend" in desc


# ─── Helper fixtures ─────────────────────────────────────────────────────────


def _ctx(cancel_event: asyncio.Event | None = None) -> ToolContext:
    return ToolContext(
        conversation_id="conv_test",
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id="run_test",
        cancel_event=cancel_event or asyncio.Event(),
        tool_names=[],
        dispatch_mode="coordinated",
    )


def _valid_plan_args():
    return {
        "tasks": [
            {"id": "t1", "agentId": "ag_alice", "task": "do X"},
            {"id": "t2", "agentId": "ag_alice", "task": "do Y", "dependsOn": ["t1"]},
        ]
    }


# ─── Handler: invalid plan ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_invalid_plan_no_tasks():
    result = await dispatch_plan_tool.handler({"tasks": []}, _ctx())
    assert result.ok is False
    assert "tasks" in result.error.lower()


@pytest.mark.asyncio
async def test_handler_invalid_plan_cycle():
    args = {
        "tasks": [
            {"id": "t1", "agentId": "ag_alice", "task": "X", "dependsOn": ["t2"]},
            {"id": "t2", "agentId": "ag_alice", "task": "Y", "dependsOn": ["t1"]},
        ]
    }
    result = await dispatch_plan_tool.handler(args, _ctx())
    assert result.ok is False
    assert "DAG" in result.error or "Cycle" in result.error


@pytest.mark.asyncio
async def test_handler_invalid_plan_duplicate_id():
    args = {
        "tasks": [
            {"id": "t1", "agentId": "ag_alice", "task": "X"},
            {"id": "t1", "agentId": "ag_alice", "task": "Y"},
        ]
    }
    result = await dispatch_plan_tool.handler(args, _ctx())
    assert result.ok is False
    assert "Duplicate" in result.error


@pytest.mark.asyncio
async def test_handler_invalid_plan_missing_ref():
    args = {
        "tasks": [
            {"id": "t1", "agentId": "ag_alice", "task": "X", "dependsOn": ["t99"]},
        ]
    }
    result = await dispatch_plan_tool.handler(args, _ctx())
    assert result.ok is False
    assert "unknown" in result.error.lower()


# ─── Handler: agent validation (requires DB) ─────────────────────────────────


@pytest.mark.asyncio
async def test_handler_agent_not_in_conversation(db):
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    conv_id = new_conversation_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="group", archived=False,
            agent_ids=["ag_alice"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_alice", name="Alice", avatar="A", description="helper",
            system_prompt="alice", adapter_name="mock",
            is_builtin=False, is_orchestrator=False,
            created_at=now,
        ))

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        tool_names=[],
        dispatch_mode="coordinated",
    )
    args = {
        "tasks": [
            {"id": "t1", "agentId": "ag_ghost", "task": "do X"},
        ]
    }
    result = await dispatch_plan_tool.handler(args, ctx)
    assert result.ok is False
    assert "not found" in result.error.lower() or "not in conversation" in result.error.lower()


# ─── Handler: valid plan with mocked execute_dag ─────────────────────────────


@pytest.mark.asyncio
async def test_handler_valid_plan_returns_results(db, monkeypatch):
    from app.db.engine import get_db
    from app.db.models import Agent, AgentRun, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_run_id, new_workspace_id

    conv_id = new_conversation_id()
    run_id = new_run_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="group", archived=False,
            agent_ids=["ag_alice", "ag_orch"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_alice", name="Alice", avatar="A", description="helper",
            system_prompt="alice", adapter_name="mock",
            is_builtin=False, is_orchestrator=False,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_orch", name="Orch", avatar="O", description="orch",
            system_prompt="orch", adapter_name="mock",
            is_builtin=True, is_orchestrator=True,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_orch",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    from app.services.dag_executor import NodeResult

    async def mock_execute_dag(tasks, ctx):
        return {
            t.id: NodeResult(
                task_id=t.id,
                status="complete",
                summary=f"done {t.id}",
                child_run_id=f"run_{t.id}",
            )
            for t in tasks
        }

    async def mock_approval_disabled():
        return False

    monkeypatch.setattr(
        "app.tools.dispatch_plan.execute_dag", mock_execute_dag
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled",
        mock_approval_disabled,
    )

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        tool_names=[],
        dispatch_mode="coordinated",
    )
    result = await dispatch_plan_tool.handler(_valid_plan_args(), ctx)

    assert result.ok is True
    assert "tasks" in result.value
    assert "t1" in result.value["tasks"]
    assert "t2" in result.value["tasks"]
    assert result.value["tasks"]["t1"]["status"] == "complete"
    assert "done t1" in result.value["tasks"]["t1"]["summary"]


# ─── Plan approval flow (mocked pending_dispatch_plans) ───────────────────────


@pytest.mark.asyncio
async def test_plan_approval_reject(db, monkeypatch):
    from app.services.pending_dispatch_plans import PlanReviewOutcome

    async def mock_approval_enabled():
        return True

    async def mock_await_plan_approval(items, ctx):
        return PlanReviewOutcome(kind="reject")

    async def mock_verify_agents(items, conversation_id, caller_agent_id):
        return None

    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled",
        mock_approval_enabled,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._await_plan_approval",
        mock_await_plan_approval,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._verify_agents_in_conversation",
        mock_verify_agents,
    )

    result = await dispatch_plan_tool.handler(_valid_plan_args(), _ctx())

    assert result.ok is True
    assert result.value.get("status") == "rejected"


@pytest.mark.asyncio
async def test_plan_approval_cancel_returns_aborted(db, monkeypatch):
    async def mock_approval_enabled():
        return True

    async def mock_await_plan_approval(items, ctx):
        return None  # cancelled

    async def mock_verify_agents(items, conversation_id, caller_agent_id):
        return None

    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled",
        mock_approval_enabled,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._await_plan_approval",
        mock_await_plan_approval,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._verify_agents_in_conversation",
        mock_verify_agents,
    )

    result = await dispatch_plan_tool.handler(_valid_plan_args(), _ctx())

    assert result.ok is True
    assert result.value.get("status") == "aborted"


@pytest.mark.asyncio
async def test_plan_approval_approve_executes(db, monkeypatch):
    from app.db.engine import get_db
    from app.db.models import Agent, AgentRun, Conversation, Workspace
    from app.services.dag_executor import NodeResult
    from app.services.pending_dispatch_plans import PlanReviewOutcome
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_run_id, new_workspace_id

    conv_id = new_conversation_id()
    run_id = new_run_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="group", archived=False,
            agent_ids=["ag_alice", "ag_orch"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_alice", name="Alice", avatar="A", description="helper",
            system_prompt="alice", adapter_name="mock",
            is_builtin=False, is_orchestrator=False,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_orch", name="Orch", avatar="O", description="orch",
            system_prompt="orch", adapter_name="mock",
            is_builtin=True, is_orchestrator=True,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_orch",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    async def mock_approval_enabled():
        return True

    async def mock_await_plan_approval(items, ctx):
        return PlanReviewOutcome(kind="approve", plan=items)

    async def mock_execute_dag(tasks, ctx):
        return {
            t.id: NodeResult(
                task_id=t.id,
                status="complete",
                summary=f"done {t.id}",
                child_run_id=f"run_{t.id}",
            )
            for t in tasks
        }

    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled",
        mock_approval_enabled,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._await_plan_approval",
        mock_await_plan_approval,
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan.execute_dag", mock_execute_dag
    )

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        tool_names=[],
        dispatch_mode="coordinated",
    )
    result = await dispatch_plan_tool.handler(_valid_plan_args(), ctx)

    assert result.ok is True
    assert "tasks" in result.value
    assert result.value["tasks"]["t1"]["status"] == "complete"
