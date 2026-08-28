"""Tests for the ask_peer horizontal communication tool.

Covers:
- 7.1 ask_peer with peerTaskId: creates mini-run, returns answer
- 7.2 ask_peer without peerTaskId: message stored in mailbox, returns pending
- 7.3 ask_peer with non-existent peerTaskId: returns unavailable
- 7.4 ask_peer ask_count reaches 3: returns limit_reached
- 7.5 mini-run messages are hidden=True (not published to SSE)
- 7.6 mini-run dispatch_depth is caller depth + 1
- 7.7 mini-run tool_names only has report_result
- 7.8 mini-run system_prompt is from AgentSession cache
- 7.9 build_run_messages(include_hidden=True) includes hidden messages
- 7.10 mailbox messages are included in dispatch_plan tool_result
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent_session_registry import (
    AgentSession,
    AgentSessionRegistry,
)
from app.tools.ask_peer import ask_peer_tool
from app.tools.base import ToolContext


def _make_ctx(
    run_id: str = "run_caller",
    parent_run_id: str = "run_parent",
    user_id: str = "user_1",
) -> ToolContext:
    return ToolContext(
        conversation_id="conv_test",
        workspace_path="/tmp/test",
        agent_id="ag_caller",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        tool_names=["ask_peer"],
        dispatch_mode="subagent",
        dispatch_depth=1,
        user_id=user_id,
        parent_run_id=parent_run_id,
    )


def _make_session(
    task_id: str = "t1",
    run_id: str = "run_t1",
    agent_id: str = "ag_target",
    dispatch_depth: int = 1,
    status: str = "completed",
    system_prompt: str | None = "You are a helpful assistant.",
    ask_count: int = 0,
) -> AgentSession:
    return AgentSession(
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        conversation_id="conv_test",
        parent_run_id="run_parent",
        dispatch_depth=dispatch_depth,
        status=status,
        system_prompt=system_prompt,
        ask_count=ask_count,
    )


def _capture_run_with_args(captured: dict):
    """Create a mock run_with_args that captures RunArgs into `captured` dict."""
    def _mock(args):
        captured["args"] = args
        fake_task = asyncio.Future()
        fake_task.set_result(MagicMock(output_message_ids=[]))
        return "run_child", fake_task, asyncio.Event()
    return _mock


@asynccontextmanager
async def _fake_db_cm():
    """Fake DB context manager that returns a mock run with trigger_message_id."""
    class FakeResult:
        def scalar_one_or_none(self):
            class FakeRun:
                trigger_message_id = "msg_trigger"
            return FakeRun()

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult()
    yield FakeDB()


# ─── 7.2: ask_peer without peerTaskId → mailbox, returns pending ────────────


@pytest.mark.asyncio
async def test_ask_peer_no_peerTaskId_stores_in_mailbox():
    reg = AgentSessionRegistry()
    with patch(
        "app.services.agent_session_registry.agent_session_registry", reg
    ):
        ctx = _make_ctx()
        result = await ask_peer_tool.handler(
            {"question": "What went wrong?"}, ctx
        )
    assert result.ok is True
    assert result.value["status"] == "pending"
    mailbox = reg.drain_mailbox("run_parent")
    assert len(mailbox) == 1
    assert "What went wrong?" in mailbox[0]


# ─── 7.3: ask_peer with non-existent peerTaskId → unavailable ───────────────


@pytest.mark.asyncio
async def test_ask_peer_nonexistent_peer_returns_unavailable():
    reg = AgentSessionRegistry()
    with patch(
        "app.services.agent_session_registry.agent_session_registry", reg
    ):
        ctx = _make_ctx()
        result = await ask_peer_tool.handler(
            {"question": "help?", "peerTaskId": "nonexistent"}, ctx
        )
    assert result.ok is True
    assert result.value["status"] == "unavailable"


@pytest.mark.asyncio
async def test_ask_peer_expired_session_returns_unavailable():
    reg = AgentSessionRegistry()
    session = _make_session(status="expired")
    reg.register_with_dag("dag_1", "t1", session)
    with patch(
        "app.services.agent_session_registry.agent_session_registry", reg
    ):
        ctx = _make_ctx()
        result = await ask_peer_tool.handler(
            {"question": "help?", "peerTaskId": "t1"}, ctx
        )
    assert result.value["status"] == "unavailable"


# ─── 7.4: ask_count reaches 3 → limit_reached ───────────────────────────────


@pytest.mark.asyncio
async def test_ask_peer_ask_count_limit():
    reg = AgentSessionRegistry()
    session = _make_session(ask_count=3)
    reg.register_with_dag("dag_1", "t1", session)
    with patch(
        "app.services.agent_session_registry.agent_session_registry", reg
    ):
        ctx = _make_ctx()
        result = await ask_peer_tool.handler(
            {"question": "help?", "peerTaskId": "t1"}, ctx
        )
    assert result.value["status"] == "limit_reached"


# ─── 7.1: ask_peer with peerTaskId → creates mini-run, returns answer ────────


@pytest.mark.asyncio
async def test_ask_peer_with_peerTaskId_returns_answer():
    reg = AgentSessionRegistry()
    session = _make_session()
    reg.register_with_dag("dag_1", "t1", session)

    mock_child_result = MagicMock()
    mock_child_result.output_message_ids = ["msg_child_1"]

    fake_child_task = asyncio.Future()
    fake_child_task.set_result(mock_child_result)

    with (
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch(
            "app.services.conversation_context.build_run_messages",
            new=AsyncMock(return_value=[{"role": "user", "content": "prev"}]),
        ),
        patch("app.services.agent_runner.run_with_args") as mock_run,
        patch(
            "app.tools.report_result._report_result_cache",
            {"run_child_1": MagicMock(summary="The answer is 42")},
        ),
        patch("app.tools.ask_peer.get_local_db", _fake_db_cm),
    ):
        mock_run.return_value = ("run_child_1", fake_child_task, asyncio.Event())

        ctx = _make_ctx()
        result = await ask_peer_tool.handler(
            {"question": "What is the answer?", "peerTaskId": "t1"}, ctx
        )

    assert result.ok is True
    assert result.value["status"] == "answered"
    assert result.value["answer"] == "The answer is 42"


# ─── 7.5: mini-run dispatch_visibility is "hidden" ──────────────────────────


@pytest.mark.asyncio
async def test_mini_run_dispatch_visibility_hidden():
    reg = AgentSessionRegistry()
    session = _make_session()
    reg.register_with_dag("dag_1", "t1", session)

    captured: dict = {}
    with (
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch(
            "app.services.conversation_context.build_run_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_runner.run_with_args",
            side_effect=_capture_run_with_args(captured),
        ),
        patch("app.tools.report_result._report_result_cache", {}),
        patch(
            "app.services.agent_loop._extract_run_final_text",
            new=AsyncMock(return_value="fallback answer"),
        ),
        patch("app.tools.ask_peer.get_local_db", _fake_db_cm),
    ):
        ctx = _make_ctx()
        await ask_peer_tool.handler(
            {"question": "test", "peerTaskId": "t1"}, ctx
        )

    assert captured["args"].dispatch_visibility == "hidden"


# ─── 7.6: mini-run dispatch_depth is session depth + 1 ───────────────────────


@pytest.mark.asyncio
async def test_mini_run_depth_is_session_depth_plus_one():
    reg = AgentSessionRegistry()
    session = _make_session(dispatch_depth=1)
    reg.register_with_dag("dag_1", "t1", session)

    captured: dict = {}
    with (
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch(
            "app.services.conversation_context.build_run_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_runner.run_with_args",
            side_effect=_capture_run_with_args(captured),
        ),
        patch("app.tools.report_result._report_result_cache", {}),
        patch(
            "app.services.agent_loop._extract_run_final_text",
            new=AsyncMock(return_value="answer"),
        ),
        patch("app.tools.ask_peer.get_local_db", _fake_db_cm),
    ):
        ctx = _make_ctx()
        await ask_peer_tool.handler(
            {"question": "test", "peerTaskId": "t1"}, ctx
        )

    assert captured["args"].dispatch_depth == 2


# ─── 7.7: mini-run tool_names only has report_result ─────────────────────────


@pytest.mark.asyncio
async def test_mini_run_tool_names_only_report_result():
    reg = AgentSessionRegistry()
    session = _make_session()
    reg.register_with_dag("dag_1", "t1", session)

    captured: dict = {}
    with (
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch(
            "app.services.conversation_context.build_run_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_runner.run_with_args",
            side_effect=_capture_run_with_args(captured),
        ),
        patch("app.tools.report_result._report_result_cache", {}),
        patch(
            "app.services.agent_loop._extract_run_final_text",
            new=AsyncMock(return_value="answer"),
        ),
        patch("app.tools.ask_peer.get_local_db", _fake_db_cm),
    ):
        ctx = _make_ctx()
        await ask_peer_tool.handler(
            {"question": "test", "peerTaskId": "t1"}, ctx
        )

    assert captured["args"].override_tool_names == ["report_result"]


# ─── 7.8: mini-run system_prompt is from AgentSession cache ──────────────────


@pytest.mark.asyncio
async def test_mini_run_system_prompt_from_session():
    reg = AgentSessionRegistry()
    session = _make_session(system_prompt="You are a coding expert.")
    reg.register_with_dag("dag_1", "t1", session)

    captured: dict = {}
    with (
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch(
            "app.services.conversation_context.build_run_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_runner.run_with_args",
            side_effect=_capture_run_with_args(captured),
        ),
        patch("app.tools.report_result._report_result_cache", {}),
        patch(
            "app.services.agent_loop._extract_run_final_text",
            new=AsyncMock(return_value="answer"),
        ),
        patch("app.tools.ask_peer.get_local_db", _fake_db_cm),
    ):
        ctx = _make_ctx()
        await ask_peer_tool.handler(
            {"question": "test", "peerTaskId": "t1"}, ctx
        )

    assert captured["args"].override_system_prompt == "You are a coding expert."


# ─── 7.9: build_run_messages exists with correct signature ─────────────────


def test_build_run_messages_signature():
    """Verify build_run_messages has the correct signature with include_hidden."""
    import inspect

    from app.services.conversation_context import build_run_messages

    sig = inspect.signature(build_run_messages)
    params = sig.parameters
    assert "run_id" in params
    assert "include_hidden" in params
    assert params["include_hidden"].default is False


def test_build_run_messages_exists():
    from app.services.conversation_context import build_run_messages

    assert callable(build_run_messages)


# ─── 7.10: mailbox messages in dispatch_plan tool_result ────────────────────


@pytest.mark.asyncio
async def test_dispatch_plan_drains_mailbox():
    """Verify dispatch_plan handler drains mailbox after execute_dag."""
    from app.services.dag_executor import NodeResult
    from app.tools.dispatch_plan import _handler

    reg = AgentSessionRegistry()
    reg.add_to_mailbox("run_test", "Need more context on task A")

    async def mock_execute_dag(tasks, ctx):
        return {"t1": NodeResult(task_id="t1", status="complete", summary="done")}

    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_mode="coordinated",
        dispatch_depth=0,
        user_id="user_1",
    )

    with (
        patch("app.tools.dispatch_plan.execute_dag", new=mock_execute_dag),
        patch("app.tools.dispatch_plan.validate_dag", return_value=[]),
        patch(
            "app.tools.dispatch_plan._verify_agents_in_conversation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.tools.dispatch_plan._is_plan_approval_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.agent_session_registry.agent_session_registry", reg
        ),
        patch("app.tools.dispatch_plan.event_bus"),
        patch("app.tools.dispatch_plan.get_local_db", _fake_db_cm),
    ):
        result = await _handler(
            {"tasks": [{"id": "t1", "task": "do work"}]}, ctx
        )

    assert result.ok is True
    assert "mailbox" in result.value
    assert "Need more context on task A" in result.value["mailbox"]


# ─── Tool registration ──────────────────────────────────────────────────────


def test_ask_peer_tool_registered():
    from app.tools.registry import tool_registry

    tool = tool_registry.get("ask_peer")
    assert tool is not None
    assert tool.name == "ask_peer"


# ─── ask_peer parameters ─────────────────────────────────────────────────────


def test_ask_peer_parameters():
    params = ask_peer_tool.parameters
    assert params["type"] == "object"
    assert "question" in params["required"]
    assert "question" in params["properties"]
    assert "peerTaskId" in params["properties"]
    assert "peerTaskId" not in params.get("required", [])


# ─── Subagent prompt contains ask_peer guidance ─────────────────────────────


def test_subagent_prompt_contains_ask_peer_guidance():
    from app.services.agent_loop import build_subagent_system_prompt

    prompt = build_subagent_system_prompt("base prompt")
    assert "ask_peer" in prompt
    assert "横向通信" in prompt


def test_subagent_suffix_has_ask_peer_section():
    from app.services.agent_loop import _SUBAGENT_SUFFIX

    assert "ask_peer" in _SUBAGENT_SUFFIX
    assert "peerTaskId" in _SUBAGENT_SUFFIX
