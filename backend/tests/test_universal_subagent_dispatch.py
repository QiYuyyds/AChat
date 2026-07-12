"""Tests for universal subagent dispatch (add-universal-subagent-dispatch change).

Covers:
- task_dispatch: optional agentId, clone-self, depth check, anti-loop
- dispatch_plan: optional agentId, depth check, anti-loop
- build_history_for: hidden message filtering
- RunArgs: dispatch_depth, dispatch_visibility, dispatch_mode defaults
- ToolContext: dispatch_depth, dispatch_mode defaults
- MAX_DISPATCH_DEPTH constant
- build_solo_system_prompt with dispatch_enabled
- build_subagent_system_prompt
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.agent_loop import (
    MAX_DISPATCH_DEPTH,
    build_solo_system_prompt,
    build_subagent_system_prompt,
)
from app.services.agent_runner import RunArgs
from app.tools.base import ToolContext
from app.tools.dispatch_plan import dispatch_plan_tool
from app.tools.registry import tool_registry
from app.tools.task_dispatch import TASK_DISPATCH_TOOL_NAME, task_dispatch_tool

# ─── Constants & Defaults ────────────────────────────────────────────────────


def test_max_dispatch_depth_is_3():
    assert MAX_DISPATCH_DEPTH == 3


def test_run_args_dispatch_defaults():
    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
    )
    assert args.dispatch_depth == 0
    assert args.dispatch_visibility == "visible"
    assert args.dispatch_mode == "solo"


def test_tool_context_dispatch_defaults():
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
    )
    assert ctx.dispatch_depth == 0
    assert ctx.dispatch_mode == "solo"


# ─── Prompt builders ─────────────────────────────────────────────────────────


def test_solo_prompt_without_dispatch():
    result = build_solo_system_prompt("base")
    assert "task_dispatch" not in result
    assert "派发" not in result


def test_solo_prompt_with_dispatch():
    result = build_solo_system_prompt("base", dispatch_enabled=True)
    assert "task_dispatch" in result
    assert "派发" in result
    assert "子任务" in result


def test_subagent_prompt_contains_dispatch_guidance():
    result = build_subagent_system_prompt("base")
    assert "子 Agent" in result
    assert str(MAX_DISPATCH_DEPTH) in result


def test_subagent_prompt_contains_context_isolation_reminder():
    result = build_subagent_system_prompt("base")
    assert "看不到" in result or "看不到父 Agent" in result


# ─── task_dispatch tool definition ───────────────────────────────────────────


def test_task_dispatch_agentId_is_optional():
    params = task_dispatch_tool.parameters
    required = params.get("required", [])
    assert "agentId" not in required
    assert "taskDescription" in required


def test_task_dispatch_tool_registered():
    tool = tool_registry.get(TASK_DISPATCH_TOOL_NAME)
    assert tool is not None
    assert tool.name == TASK_DISPATCH_TOOL_NAME


# ─── task_dispatch: depth check ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_dispatch_depth_check():
    """At max depth, task_dispatch returns an error."""
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        dispatch_depth=MAX_DISPATCH_DEPTH,
        dispatch_mode="solo",
    )
    result = await task_dispatch_tool.handler(
        {"taskDescription": "do something"}, ctx
    )
    assert result.ok is False
    assert "depth" in result.error.lower()


# ─── task_dispatch: anti-loop check ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_dispatch_antiloop_subagent_cannot_dispatch_to_other():
    """Subagent (non-coordinated) cannot dispatch to a different agent."""
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="subagent",
    )
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_other", "taskDescription": "do something"}, ctx
    )
    assert result.ok is False
    assert "clone itself" in result.error.lower() or "cannot dispatch" in result.error.lower()


# ─── task_dispatch: clone-self logic ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_dispatch_clone_self_no_agentId(db, monkeypatch):
    """When agentId is omitted, the tool clones the calling agent."""
    from app.db.engine import get_db
    from app.db.models import Agent, AgentRun, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_run_id, new_workspace_id

    conv_id = new_conversation_id()
    run_id = new_run_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="single", archived=False,
            agent_ids=["ag_solo"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_solo", name="Solo", avatar="S", description="solo agent",
            system_prompt="solo", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_solo",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    # Mock spawn_subagent_loop to capture the call
    captured = {}

    async def mock_spawn(agent_id, task_description, conversation_id,
                         trigger_message_id, parent_run_id, parent_cancel_event,
                         workspace_path=None, on_start=None,
                         dispatch_depth=0, dispatch_visibility="visible"):
        captured["agent_id"] = agent_id
        captured["dispatch_depth"] = dispatch_depth
        captured["dispatch_visibility"] = dispatch_visibility
        from app.services.agent_loop import LoopRunResult
        return LoopRunResult(status="complete", text="done", run_id="child_1")

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", mock_spawn)

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_solo",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="solo",
    )
    result = await task_dispatch_tool.handler(
        {"taskDescription": "do something"}, ctx
    )

    assert result.ok is True
    assert captured["agent_id"] == "ag_solo"
    assert captured["dispatch_visibility"] == "hidden"
    assert captured["dispatch_depth"] == 1


@pytest.mark.asyncio
async def test_task_dispatch_clone_self_with_own_agentId(db, monkeypatch):
    """When agentId equals caller's own agent_id, it's treated as clone-self."""
    from app.db.engine import get_db
    from app.db.models import Agent, AgentRun, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_run_id, new_workspace_id

    conv_id = new_conversation_id()
    run_id = new_run_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="single", archived=False,
            agent_ids=["ag_solo"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_solo", name="Solo", avatar="S", description="solo agent",
            system_prompt="solo", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_solo",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    captured = {}

    async def mock_spawn(agent_id, task_description, conversation_id,
                         trigger_message_id, parent_run_id, parent_cancel_event,
                         workspace_path=None, on_start=None,
                         dispatch_depth=0, dispatch_visibility="visible"):
        captured["agent_id"] = agent_id
        captured["dispatch_visibility"] = dispatch_visibility
        from app.services.agent_loop import LoopRunResult
        return LoopRunResult(status="complete", text="done", run_id="child_1")

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", mock_spawn)

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_solo",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="solo",
    )
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_solo", "taskDescription": "do something"}, ctx
    )

    assert result.ok is True
    assert captured["agent_id"] == "ag_solo"
    assert captured["dispatch_visibility"] == "hidden"


# ─── task_dispatch: group-member dispatch (coordinated mode) ────────────────


@pytest.mark.asyncio
async def test_task_dispatch_group_member_visible(db, monkeypatch):
    """In coordinated mode, dispatching to a group member uses visibility='visible'."""
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
            agent_ids=["ag_orch", "ag_front"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_orch", name="Orch", avatar="O", description="orchestrator",
            system_prompt="orch", adapter_name="mock",
            is_builtin=True, is_orchestrator=True, supports_vision=False,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_front", name="Front", avatar="F", description="frontend",
            system_prompt="front", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_orch",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    captured = {}

    async def mock_spawn(agent_id, task_description, conversation_id,
                         trigger_message_id, parent_run_id, parent_cancel_event,
                         workspace_path=None, on_start=None,
                         dispatch_depth=0, dispatch_visibility="visible"):
        captured["agent_id"] = agent_id
        captured["dispatch_visibility"] = dispatch_visibility
        captured["dispatch_depth"] = dispatch_depth
        from app.services.agent_loop import LoopRunResult
        return LoopRunResult(status="complete", text="done", run_id="child_1")

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", mock_spawn)

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_orch",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="coordinated",
    )
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_front", "taskDescription": "write UI"}, ctx
    )

    assert result.ok is True
    assert captured["agent_id"] == "ag_front"
    assert captured["dispatch_visibility"] == "visible"
    assert captured["dispatch_depth"] == 1


# ─── dispatch_plan: depth check & anti-loop ─────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_plan_depth_check():
    """At max depth, dispatch_plan returns an error."""
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        dispatch_depth=MAX_DISPATCH_DEPTH,
        dispatch_mode="coordinated",
    )
    args = {
        "tasks": [
            {"id": "t1", "task": "do X"},
        ]
    }
    result = await dispatch_plan_tool.handler(args, ctx)
    assert result.ok is False
    assert "depth" in result.error.lower()


@pytest.mark.asyncio
async def test_dispatch_plan_antiloop_subagent():
    """Subagent (non-coordinated) cannot dispatch_plan to other agents."""
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="subagent",
    )
    args = {
        "tasks": [
            {"id": "t1", "agentId": "ag_other", "task": "do X"},
        ]
    }
    result = await dispatch_plan_tool.handler(args, ctx)
    assert result.ok is False
    assert "clone itself" in result.error.lower() or "cannot dispatch" in result.error.lower()


@pytest.mark.asyncio
async def test_dispatch_plan_clone_self_no_agentId(db, monkeypatch):
    """dispatch_plan with no agentId on tasks resolves to clone-self."""
    from app.db.engine import get_db
    from app.db.models import Agent, AgentRun, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_run_id, new_workspace_id

    conv_id = new_conversation_id()
    run_id = new_run_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="single", archived=False,
            agent_ids=["ag_solo"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_solo", name="Solo", avatar="S", description="solo",
            system_prompt="solo", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        ))
        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id="ag_solo",
            trigger_message_id="msg_1", status="running", started_at=now,
        ))

    captured = {}

    async def mock_execute_dag(tasks, ctx):
        captured["dispatch_depth"] = ctx.dispatch_depth
        captured["dispatch_visibility"] = ctx.dispatch_visibility
        from app.services.dag_executor import NodeResult
        return {
            t.id: NodeResult(task_id=t.id, status="complete", summary="done", child_run_id="r1")
            for t in tasks
        }

    async def mock_approval_disabled():
        return False

    monkeypatch.setattr("app.tools.dispatch_plan.execute_dag", mock_execute_dag)
    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled", mock_approval_disabled
    )

    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path="/tmp/test",
        agent_id="ag_solo",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        dispatch_depth=0,
        dispatch_mode="solo",
    )
    args = {
        "tasks": [
            {"id": "t1", "task": "do X"},
            {"id": "t2", "task": "do Y", "dependsOn": ["t1"]},
        ]
    }
    result = await dispatch_plan_tool.handler(args, ctx)

    assert result.ok is True
    assert captured["dispatch_depth"] == 1
    assert captured["dispatch_visibility"] == "hidden"


# ─── dispatch_plan: agentId optional ────────────────────────────────────────


def test_dispatch_plan_agentId_optional_in_item():
    """dispatch_plan task items should not require agentId."""
    params = dispatch_plan_tool.parameters
    item_required = params["properties"]["tasks"]["items"]["required"]
    assert "agentId" not in item_required


# ─── build_history_for: hidden filter ───────────────────────────────────────


@pytest.mark.asyncio
async def test_build_history_excludes_hidden_messages(db):
    """build_history_for should exclude messages with hidden=True."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Message, Workspace
    from app.services.conversation_context import BuildHistoryOptions, build_history_for
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_message_id, new_workspace_id

    conv_id = new_conversation_id()
    now = now_ms()
    async with get_db() as session:
        session.add(Conversation(
            id=conv_id, title="T", mode="single", archived=False,
            agent_ids=["ag_1"],
            fs_write_approval_mode="auto", created_at=now, updated_at=now,
        ))
        session.add(Workspace(
            id=new_workspace_id(), conversation_id=conv_id,
            root_path="/tmp/test", mode="sandbox", bound_path=None,
            created_at=now,
        ))
        session.add(Agent(
            id="ag_1", name="Agent", avatar="A", description="test",
            system_prompt="test", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        ))
        # Visible user message
        msg1 = Message(
            id=new_message_id(), conversation_id=conv_id, role="user",
            status="complete", created_at=now, hidden=False,
        )
        msg1.parts_list = [{"type": "text", "content": "hello"}]
        msg1.mentioned_agent_ids_list = []
        session.add(msg1)
        # Hidden agent message (clone-subagent)
        msg2 = Message(
            id=new_message_id(), conversation_id=conv_id, role="agent",
            agent_id="ag_1", status="complete", created_at=now + 1,
            hidden=True,
        )
        msg2.parts_list = [{"type": "text", "content": "hidden response"}]
        msg2.mentioned_agent_ids_list = []
        session.add(msg2)

    history = await build_history_for(
        "ag_1", conv_id, BuildHistoryOptions(max_turns=10)
    )
    contents = [m.get("content", "") for m in history if isinstance(m.get("content"), str)]
    assert "hello" in "\n".join(contents)
    assert "hidden response" not in "\n".join(contents)
