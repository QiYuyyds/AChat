"""Tests for override injection point and ToolContext DAG fields.

Covers:
- 8.5 execute_simple_run override_messages injection
- 8.6 execute_simple_run override_system_prompt injection
- 8.7 _run_react_loop creates ToolContext with dag_id/dag_task_id/parent_run_id
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.base import ToolContext

# ─── 8.5/8.6 Test that RunArgs has override_messages field ────────────────────


def test_run_args_has_override_messages_field():
    from app.services.agent_runner import RunArgs

    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
    )
    assert args.override_messages is None
    assert args.dag_id is None
    assert args.dag_task_id is None


def test_run_args_override_messages_set():
    from app.services.agent_runner import RunArgs

    msgs = [{"role": "user", "content": "hello"}]
    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        override_messages=msgs,
        dag_id="dag_1",
        dag_task_id="t1",
    )
    assert args.override_messages is msgs
    assert args.dag_id == "dag_1"
    assert args.dag_task_id == "t1"


# ─── 8.7 Test _run_react_loop signature accepts DAG params ────────────────────


def test_run_react_loop_accepts_dag_params():
    """Verify _run_react_loop signature includes dag_id, dag_task_id, parent_run_id."""
    from app.services.agent_runner import _run_react_loop

    sig = inspect.signature(_run_react_loop)
    params = sig.parameters
    assert "dag_id" in params
    assert "dag_task_id" in params
    assert "parent_run_id" in params
    assert params["dag_id"].default is None
    assert params["dag_task_id"].default is None
    assert params["parent_run_id"].default is None


# ─── 8.7 Test ToolContext has DAG fields ─────────────────────────────────────


def test_tool_context_has_dag_fields():
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
    )
    assert ctx.dag_id is None
    assert ctx.dag_task_id is None
    assert ctx.parent_run_id is None


def test_tool_context_dag_fields_set():
    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        dag_id="dag_1",
        dag_task_id="t1",
        parent_run_id="run_parent",
    )
    assert ctx.dag_id == "dag_1"
    assert ctx.dag_task_id == "t1"
    assert ctx.parent_run_id == "run_parent"


# ─── 8.5/8.6 Test override injection via mocked execute_simple_run ────────────


@pytest.fixture
def mock_sdk_adapter():
    """Mock the minimum objects needed for execute_simple_run."""
    from app.services.agent_runner import RunExecutionResult

    agent = MagicMock()
    agent.id = "ag_1"
    agent.adapter_name = "custom"
    agent.tool_names_list = []
    agent.system_prompt = "base prompt"
    agent.is_guide = False
    agent.memory_enabled = False
    agent.skill_names_list = []

    workspace = MagicMock()
    workspace.bound_path = "/tmp"
    workspace.root_path = "/tmp"

    adapter = MagicMock()
    adapter_input = MagicMock()
    adapter_input.messages = None
    adapter_input.system_prompt = "built prompt"
    adapter_input.prompt = None
    adapter_input.model_id = None
    adapter_input.custom_config = None
    adapter_input.workspace_path = "/tmp"
    adapter_input.attachments = []
    adapter_input.history = []
    adapter_input.tool_names = []
    adapter_input.mcp_tools = None

    run_result = RunExecutionResult(
        artifact_ids=[],
        output_message_ids=[],
    )

    return agent, workspace, adapter, adapter_input, run_result


@pytest.mark.asyncio
async def test_override_messages_injection(mock_sdk_adapter, monkeypatch):
    """8.5: When args.override_messages is set, adapter_input.messages is overwritten."""
    agent, workspace, adapter, adapter_input, run_result = mock_sdk_adapter

    from app.services.agent_runner import RunArgs, execute_simple_run

    override_msgs = [{"role": "user", "content": "mini run"}]
    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        override_messages=override_msgs,
    )

    # Mock the required dependencies
    monkeypatch.setattr(
        "app.infra.cache_helpers.get_agent_cached",
        AsyncMock(return_value=agent),
    )
    monkeypatch.setattr(
        "app.infra.cache_helpers.get_workspace_cached",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.agent_registry",
        MagicMock(get_adapter=MagicMock(return_value=adapter)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.build_adapter_input",
        AsyncMock(return_value=adapter_input),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._run_react_loop",
        MagicMock(return_value=iter([])),  # empty stream
    )
    monkeypatch.setattr(
        "app.services.agent_runner.consume_stream",
        AsyncMock(return_value=run_result),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.get_settings",
        MagicMock(return_value=MagicMock(use_react_loop=True)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.SDK_ADAPTERS",
        {"custom"},
    )
    monkeypatch.setattr(
        "app.services.agent_runner._BASELINE_AGENT_TOOLS",
        [],
    )
    monkeypatch.setattr(
        "app.services.agent_runner._MANAGEMENT_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._TASK_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._get_task_mem_buffer",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._resolve_mcp_configs",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.clear_run_tool_evidence",
        MagicMock(),
    )

    cancel_event = asyncio.Event()
    await execute_simple_run("run_1", cancel_event, args, "test prompt", [])

    assert adapter_input.messages is override_msgs


@pytest.mark.asyncio
async def test_override_system_prompt_injection(mock_sdk_adapter, monkeypatch):
    """8.6: When args.override_system_prompt is set, adapter_input.system_prompt is overwritten."""
    agent, workspace, adapter, adapter_input, run_result = mock_sdk_adapter

    from app.services.agent_runner import RunArgs, execute_simple_run

    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        override_system_prompt="custom system prompt",
    )

    monkeypatch.setattr(
        "app.infra.cache_helpers.get_agent_cached",
        AsyncMock(return_value=agent),
    )
    monkeypatch.setattr(
        "app.infra.cache_helpers.get_workspace_cached",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.agent_registry",
        MagicMock(get_adapter=MagicMock(return_value=adapter)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.build_adapter_input",
        AsyncMock(return_value=adapter_input),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._run_react_loop",
        MagicMock(return_value=iter([])),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.consume_stream",
        AsyncMock(return_value=run_result),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.get_settings",
        MagicMock(return_value=MagicMock(use_react_loop=True)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.SDK_ADAPTERS",
        {"custom"},
    )
    monkeypatch.setattr(
        "app.services.agent_runner._BASELINE_AGENT_TOOLS",
        [],
    )
    monkeypatch.setattr(
        "app.services.agent_runner._MANAGEMENT_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._TASK_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._get_task_mem_buffer",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._resolve_mcp_configs",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.clear_run_tool_evidence",
        MagicMock(),
    )

    cancel_event = asyncio.Event()
    await execute_simple_run("run_1", cancel_event, args, "test prompt", [])

    assert adapter_input.system_prompt == "custom system prompt"


@pytest.mark.asyncio
async def test_no_override_when_none(mock_sdk_adapter, monkeypatch):
    """When override_messages/override_system_prompt are None, no override applied."""
    agent, workspace, adapter, adapter_input, run_result = mock_sdk_adapter

    from app.services.agent_runner import RunArgs, execute_simple_run

    args = RunArgs(
        agent_id="ag_1",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
    )

    monkeypatch.setattr(
        "app.infra.cache_helpers.get_agent_cached",
        AsyncMock(return_value=agent),
    )
    monkeypatch.setattr(
        "app.infra.cache_helpers.get_workspace_cached",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.agent_registry",
        MagicMock(get_adapter=MagicMock(return_value=adapter)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.build_adapter_input",
        AsyncMock(return_value=adapter_input),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._run_react_loop",
        MagicMock(return_value=iter([])),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.consume_stream",
        AsyncMock(return_value=run_result),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.get_settings",
        MagicMock(return_value=MagicMock(use_react_loop=True)),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.SDK_ADAPTERS",
        {"custom"},
    )
    monkeypatch.setattr(
        "app.services.agent_runner._BASELINE_AGENT_TOOLS",
        [],
    )
    monkeypatch.setattr(
        "app.services.agent_runner._MANAGEMENT_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._TASK_TOOL_NAMES",
        set(),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._get_task_mem_buffer",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.agent_runner._resolve_mcp_configs",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.agent_runner.clear_run_tool_evidence",
        MagicMock(),
    )

    cancel_event = asyncio.Event()
    original_messages = adapter_input.messages
    original_prompt = adapter_input.system_prompt
    await execute_simple_run("run_1", cancel_event, args, "test prompt", [])

    # Values should be unchanged (not overwritten by None)
    assert adapter_input.messages is original_messages
    assert adapter_input.system_prompt == original_prompt


# ─── 8.7 Test spawn_subagent_loop accepts dag params ──────────────────────────


def test_spawn_subagent_loop_accepts_dag_params():
    """Verify spawn_subagent_loop signature includes dag_id and dag_task_id."""
    from app.services.agent_loop import spawn_subagent_loop

    sig = inspect.signature(spawn_subagent_loop)
    params = sig.parameters
    assert "dag_id" in params
    assert "dag_task_id" in params
    assert params["dag_id"].default is None
    assert params["dag_task_id"].default is None


# ─── 8.7 Test DagExecContext has dag_id and all_task_ids ──────────────────────


def test_dag_exec_context_has_dag_fields():
    from app.services.dag_executor import DagExecContext

    ctx = DagExecContext(
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        parent_run_id="run_1",
        cancel_event=asyncio.Event(),
        dag_id="dag_1",
        all_task_ids=["t1", "t2"],
    )
    assert ctx.dag_id == "dag_1"
    assert ctx.all_task_ids == ["t1", "t2"]


def test_dag_exec_context_defaults():
    import asyncio

    from app.services.dag_executor import DagExecContext

    ctx = DagExecContext(
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        parent_run_id="run_1",
        cancel_event=asyncio.Event(),
    )
    assert ctx.dag_id == ""
    assert ctx.all_task_ids == []
