"""Timeout + cancel-cleanup tests for approval waits.

Covers the add-approval-timeout-visibility change:
- ``await_pending_decision``: timeout sentinel, entry teardown via ``cancel``,
  resolver-still-wins, and the legacy unbounded wait (``timeout_s=None``).
- Per-kind timeout wiring: fs_write / ask_user / bash via the tool registry,
  the MCP-call wait shape, dispatch plan review (timeout = rejected), and the
  worktree merge-conflict wait (timeout preserves conflict markers).
- Design D3 unification: cancel on all six stores fires the resolver AND
  publishes exactly one resolved SSE event each.
- Env override of the APPROVAL_TIMEOUT_* settings.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

from app.config import get_settings
from app.services import worktree_service as wt
from app.services.event_bus import event_bus
from app.services.pending_bash_commands import pending_bash_commands
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.pending_mcp_calls import pending_mcp_calls
from app.services.pending_merge_conflicts import pending_merge_conflicts
from app.services.pending_questions import pending_questions
from app.services.pending_writes import pending_writes
from app.tools.base import ToolContext
from app.utils.approval import APPROVAL_TIMEOUT, await_pending_decision


@pytest.fixture(autouse=True)
def _reset_pending_stores():
    for store in (
        pending_writes,
        pending_questions,
        pending_bash_commands,
        pending_dispatch_plans,
        pending_mcp_calls,
        pending_merge_conflicts,
    ):
        store._map.clear()
    yield
    for store in (
        pending_writes,
        pending_questions,
        pending_bash_commands,
        pending_dispatch_plans,
        pending_mcp_calls,
        pending_merge_conflicts,
    ):
        store._map.clear()


# ─── await_pending_decision primitive ────────────────────────────────────────


async def test_wait_timeout_returns_sentinel_and_cancels_entry():
    question = pending_questions.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        questions=[],
        user_id=None,
    )
    cancel_calls: list[bool] = []

    def _cancel() -> None:
        pending_questions.cancel(question.id)
        cancel_calls.append(True)

    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_questions.attach_resolver(question.id, r),
        cancel=_cancel,
        cancel_event=asyncio.Event(),
        cancelled_value=None,
        timeout_s=0.05,
    )
    assert decision is APPROVAL_TIMEOUT
    # Timeout must tear down the pending entry (never leave it parked).
    assert pending_questions.get(question.id) is None
    assert cancel_calls == [True]


async def test_wait_resolver_still_wins_over_timeout():
    question = pending_questions.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        questions=[],
        user_id=None,
    )

    async def _answer_later():
        await asyncio.sleep(0.01)
        pending_questions.answer(question.id, {"Q?": None})

    task = asyncio.ensure_future(_answer_later())
    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_questions.attach_resolver(question.id, r),
        cancel=lambda: pending_questions.cancel(question.id),
        cancel_event=asyncio.Event(),
        cancelled_value=None,
        timeout_s=5,
    )
    await task
    assert decision == {"Q?": None}


async def test_wait_without_timeout_keeps_legacy_abort_value():
    cancel_event = asyncio.Event()
    question = pending_questions.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        questions=[],
        user_id=None,
    )

    async def _abort_later():
        await asyncio.sleep(0.01)
        cancel_event.set()

    task = asyncio.ensure_future(_abort_later())
    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_questions.attach_resolver(question.id, r),
        cancel=lambda: pending_questions.cancel(question.id),
        cancel_event=cancel_event,
        cancelled_value=None,
    )
    await task
    assert decision is None  # legacy cancelled_value, not the timeout sentinel


# ─── per-kind wiring: fs_write / ask_user / bash via the registry ────────────


@pytest_asyncio.fixture
async def review_conversation(db, agents, tmp_path):
    """Conversation + sandbox workspace with fs_write review mode enabled."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="T",
            mode="single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agents["alice"]]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )

    return {
        "conversation_id": conv_id,
        "agent_id": agents["alice"],
        "workspace_root": str(ws_root),
    }


def _ctx(conversation) -> ToolContext:
    return ToolContext(
        conversation_id=conversation["conversation_id"],
        workspace_path=conversation["workspace_root"],
        agent_id=conversation["agent_id"],
        run_id="run_test",
        cancel_event=asyncio.Event(),
    )


async def test_fs_write_timeout_returns_error_and_never_writes(
    review_conversation, monkeypatch
):
    from app.tools.registry import tool_registry

    monkeypatch.setattr(get_settings(), "approval_timeout_write", 0.05)

    result = await tool_registry.execute(
        "fs_write", {"path": "t.txt", "content": "x"}, _ctx(review_conversation)
    )
    assert result.ok is False
    assert "审批超时" in result.error
    # No approve side effect: entry gone, file untouched.
    assert (
        pending_writes.list_by_conversation(review_conversation["conversation_id"])
        == []
    )
    assert not os.path.isfile(
        os.path.join(review_conversation["workspace_root"], "t.txt")
    )


async def test_ask_user_timeout_returns_error(review_conversation, monkeypatch):
    from app.tools.registry import tool_registry

    monkeypatch.setattr(get_settings(), "approval_timeout_ask_user", 0.05)

    result = await tool_registry.execute(
        "ask_user",
        {
            "questions": [
                {
                    "question": "Proceed?",
                    "header": "Confirm",
                    "options": [{"label": "Yes"}, {"label": "No"}],
                }
            ]
        },
        _ctx(review_conversation),
    )
    assert result.ok is False
    assert "用户暂未回答（超时）" in result.error
    assert (
        pending_questions.list_by_conversation(review_conversation["conversation_id"])
        == []
    )


async def test_bash_approval_timeout_returns_error_and_skips_run(
    review_conversation, monkeypatch
):
    from app.tools.registry import tool_registry

    monkeypatch.setattr(get_settings(), "approval_timeout_bash", 0.05)

    result = await tool_registry.execute(
        "bash", {"command": "rm -rf some_dir"}, _ctx(review_conversation)
    )
    assert result.ok is False
    assert "命令执行审批超时" in result.error
    assert (
        pending_bash_commands.list_by_conversation(
            review_conversation["conversation_id"]
        )
        == []
    )


async def test_mcp_call_wait_timeout_cancels_entry_without_side_effect(monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_timeout_mcp", 0.05)

    call = pending_mcp_calls.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        tool_name="mcp__srv__tool",
        args={},
        server_trust="ask",
        user_id=None,
    )
    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_mcp_calls.attach_resolver(call.id, r),
        cancel=lambda: pending_mcp_calls.cancel(call.id),
        cancel_event=asyncio.Event(),
        cancelled_value={"approved": False},
        timeout_s=get_settings().approval_timeout_mcp,
    )
    assert decision is APPROVAL_TIMEOUT
    assert pending_mcp_calls.get(call.id) is None
    # Session-level decision memory must stay untouched by the timeout.
    assert not pending_mcp_calls.is_approved("conv_x", "mcp__srv__tool")
    assert not pending_mcp_calls.is_rejected("conv_x", "mcp__srv__tool")


async def test_dispatch_plan_timeout_treated_as_rejected(monkeypatch):
    from app.tools.dispatch_plan import dispatch_plan_tool

    async def _approval_enabled() -> bool:
        return True

    async def _no_agents(items, conversation_id, caller_agent_id):
        return None

    monkeypatch.setattr(
        "app.tools.dispatch_plan._is_plan_approval_enabled", _approval_enabled
    )
    monkeypatch.setattr(
        "app.tools.dispatch_plan._verify_agents_in_conversation", _no_agents
    )
    monkeypatch.setattr(get_settings(), "approval_timeout_dispatch_review", 0.05)

    ctx = ToolContext(
        conversation_id="conv_x",
        workspace_path="/tmp/test",
        agent_id="ag_alice",
        run_id="run_1",
        cancel_event=asyncio.Event(),
        tool_names=[],
        dispatch_mode="coordinated",
    )
    result = await dispatch_plan_tool.handler(
        {"tasks": [{"id": "t1", "task": "do X"}]}, ctx
    )
    assert result.ok is True
    assert result.value["status"] == "rejected"
    assert "超时" in result.value["reason"]
    assert pending_dispatch_plans.list_by_conversation("conv_x") == []


# ─── merge-conflict timeout (real git) ───────────────────────────────────────


def _git_available() -> bool:
    import shutil

    return shutil.which("git") is not None


@pytest_asyncio.fixture
async def worktree_env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path and reset settings cache."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    get_settings.cache_clear()
    os.makedirs(tmp_path / "data", exist_ok=True)
    os.makedirs(tmp_path / "workspaces", exist_ok=True)
    yield tmp_path
    get_settings.cache_clear()


async def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    await wt._run_git(path, "init")
    await wt._run_git(path, "config", "user.email", "test@local")
    await wt._run_git(path, "config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
        f.write("init\n")
    await wt._run_git(path, "add", "-A")
    await wt._run_git(path, "commit", "-m", "init")


@pytest.mark.skipif(not _git_available(), reason="git not installed")
async def test_merge_conflict_timeout_preserves_conflict_markers(
    worktree_env, db, monkeypatch
):
    ws = str(worktree_env / "timeout_ws")
    await _init_repo(ws)
    ref1 = await wt.create_worktree(ws, "t1", "A", "conv_m")
    ref2 = await wt.create_worktree(ws, "t2", "B", "conv_m")
    assert ref1 and ref2

    with open(os.path.join(ref1.path, "README.md"), "w") as f:
        f.write("from t1\n")
    r1 = await wt.merge_worktree_back(ref1)
    assert r1.success
    await wt.cleanup_worktree(ref1)

    with open(os.path.join(ref2.path, "README.md"), "w") as f:
        f.write("from t2\n")

    async def _llm_fail(*args, **kwargs):
        return False, []

    monkeypatch.setattr(wt, "_llm_resolve_conflicts", _llm_fail)
    monkeypatch.setattr(get_settings(), "approval_timeout_merge_conflict", 0.05)

    result = await wt.merge_worktree_back(ref2)
    assert result.success is False
    assert result.resolution_strategy == "timeout"
    assert any("README.md" in f for f in result.conflict_files)
    # Timeout never auto-resolves: conflict markers stay, nothing merged back.
    with open(os.path.join(ws, "README.md"), encoding="utf-8") as f:
        content = f.read()
    assert "<<<<<<<" in content
    # The pending conflict entry was torn down by the timeout.
    assert pending_merge_conflicts.list_by_conversation("conv_m") == []


# ─── cancel unification (design D3): teardown publishes resolved events ──────


async def test_cancel_clears_all_six_kinds_and_publishes_resolved_events():
    from app.schemas.events import (
        AskUserResolvedEvent,
        BashCommandResolvedEvent,
        DispatchPlanResolvedEvent,
        FsWriteResolvedEvent,
        McpCallResolvedEvent,
        MergeConflictResolvedEvent,
    )

    write = pending_writes.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        path="a.txt",
        absolute_path="/tmp/ws/a.txt",
        old_content=None,
        new_content="hello",
        workspace=None,
        skip_write=True,
    )
    question = pending_questions.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        questions=[],
    )
    command = pending_bash_commands.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        command="ls",
        cwd="/tmp",
        reason="test",
    )
    mcp_call = pending_mcp_calls.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        tool_name="mcp__srv__tool",
        args={},
        server_trust="ask",
    )
    plan = pending_dispatch_plans.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        plan=[],
        validator=lambda p: p,
    )
    conflict = pending_merge_conflicts.register(
        conversation_id="conv_x",
        task_id="t1",
        conflict_files=["a.txt"],
        workspace_path="/tmp/ws",
    )

    resolver_payloads: dict[str, object] = {}
    pending_writes.attach_resolver(write.id, lambda v: resolver_payloads.__setitem__("write", v))
    pending_questions.attach_resolver(question.id, lambda v: resolver_payloads.__setitem__("question", v))
    pending_bash_commands.attach_resolver(command.id, lambda v: resolver_payloads.__setitem__("bash", v))
    pending_mcp_calls.attach_resolver(mcp_call.id, lambda v: resolver_payloads.__setitem__("mcp", v))
    pending_dispatch_plans.attach_resolver(plan.id, lambda v: resolver_payloads.__setitem__("dispatch", v))
    pending_merge_conflicts.attach_resolver(conflict.id, lambda v: resolver_payloads.__setitem__("merge", v))

    async with event_bus.subscribe() as sub:
        pending_writes.cancel(write.id)
        pending_questions.cancel(question.id)
        pending_bash_commands.cancel(command.id)
        pending_mcp_calls.cancel(mcp_call.id)
        pending_dispatch_plans.cancel(plan.id)
        pending_merge_conflicts.cancel(conflict.id)

    for store in (
        pending_writes,
        pending_questions,
        pending_bash_commands,
        pending_mcp_calls,
        pending_dispatch_plans,
        pending_merge_conflicts,
    ):
        assert store.list_by_conversation("conv_x") == []

    events = []
    for _ in range(6):
        _, ev = await asyncio.wait_for(sub.queue.get(), timeout=1)
        events.append(ev)
    assert {type(e) for e in events} == {
        FsWriteResolvedEvent,
        AskUserResolvedEvent,
        BashCommandResolvedEvent,
        McpCallResolvedEvent,
        DispatchPlanResolvedEvent,
        MergeConflictResolvedEvent,
    }

    # Each waiting tool got its teardown decision (never an approval).
    assert resolver_payloads["write"] == {"applied": False}
    assert resolver_payloads["question"] is None
    assert resolver_payloads["bash"] == {"approved": False}
    assert resolver_payloads["mcp"] == {"approved": False}
    assert resolver_payloads["dispatch"].kind == "reject"
    assert resolver_payloads["merge"] == {
        "action": "abandon",
        "resolution_strategy": "abandoned",
    }


# ─── configuration (spec: Timeout configuration) ─────────────────────────────


async def test_approval_timeout_env_override(monkeypatch):
    monkeypatch.setenv("APPROVAL_TIMEOUT_BASH", "3.5")
    get_settings.cache_clear()
    try:
        assert get_settings().approval_timeout_bash == 3.5
    finally:
        get_settings.cache_clear()
