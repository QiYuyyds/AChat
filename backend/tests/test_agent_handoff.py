"""Tests for the agent handoff verb (add-agent-handoff).

Covers:
- terminal tool semantics: calling handoff records the payload and terminates
  the run; validation errors (self / whitelist / busy / chain cap / cycle)
  leave the run alive
- catch point ①: responder handoff → visible system message + target responder
  run via the existing startup path; post-handoff failure visibility
- catch point ②: task_dispatch executor-swap loop — same worktree / visibility
  / depth, handoff summary appended, merge-back once, executedBy/handoffChain
  metadata, busy fallback, chain cap, abort
- spawn_subagent_loop payload extraction + chain registration
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.tools.base import ToolContext
from app.tools.handoff import (
    MAX_HANDOFF_CHAIN,
    HandoffPayload,
    pop_handoff,
    pop_handoff_chain,
    register_handoff_chain,
)
from app.tools.registry import tool_registry

# ─── Seed helpers ─────────────────────────────────────────────────────────────


async def _seed_group_conversation(
    agent_ids: list[str], *, conversation_id: str | None = None
) -> str:
    from app.db.engine import get_db
    from app.db.models import Conversation
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id

    conv_id = conversation_id or new_conversation_id()
    now = now_ms()
    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="T",
            mode="group",
            archived=False,
            agent_ids=agent_ids,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)
    return conv_id


async def _seed_agent(agent_id: str, name: str) -> None:
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.utils.clock import now_ms

    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name=name,
            avatar="X",
            description="test agent",
            system_prompt="p",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now_ms(),
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)


async def _seed_active_run(agent_id: str, conversation_id: str, status: str = "running") -> str:
    from app.db.engine import get_db
    from app.db.models import AgentRun
    from app.utils.clock import now_ms
    from app.utils.ids import new_run_id

    run_id = new_run_id()
    async with get_db() as session:
        session.add(AgentRun(
            id=run_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            trigger_message_id="msg_x",
            status=status,
            started_at=now_ms(),
        ))
    return run_id


def _handoff_ctx(conversation_id: str, agent_id: str, run_id: str = "run_h1") -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        workspace_path="/tmp/test",
        agent_id=agent_id,
        run_id=run_id,
        cancel_event=asyncio.Event(),
        tool_names=["handoff"],
    )


@pytest.fixture
def handoff_tool():
    return tool_registry.get("handoff")


@pytest.fixture
def recorded_events(monkeypatch):
    from app.services.event_bus import event_bus

    events: list = []

    def _recorder(event, user_id=None):
        events.append(event)

    monkeypatch.setattr(event_bus, "publish", _recorder)
    return events


# ─── 5.1 终态工具语义 ──────────────────────────────────────────────────────────


def test_handoff_tool_registered_and_terminal(handoff_tool):
    assert handoff_tool is not None
    from app.services.agent_runner import TERMINAL_TOOLS

    assert "handoff" in TERMINAL_TOOLS
    assert "handoff" not in (
        "read_attachment", "ask_user", "fs_list", "fs_read", "fs_write",
        "fs_edit", "fs_grep", "fs_glob", "bash",
    )


@pytest.mark.asyncio
async def test_handoff_ok_records_payload(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])

    ctx = _handoff_ctx(conv_id, "ag_a")
    result = await handoff_tool.handler({
        "agentId": "ag_b",
        "reason": "前端任务",
        "summary": "已完成设计，剩余工作是实现组件，未决问题：配色",
        "filesChanged": ["src/a.ts"],
        "artifacts": ["art_1"],
        "keyDecisions": ["用 React"],
    }, ctx)

    assert result.ok is True
    payload = pop_handoff(ctx.run_id)
    assert payload is not None
    assert payload.agent_id == "ag_b"
    assert payload.reason == "前端任务"
    assert payload.files_changed == ["src/a.ts"]
    assert payload.artifacts == ["art_1"]
    assert payload.key_decisions == ["用 React"]


@pytest.mark.asyncio
async def test_handoff_missing_reason_or_summary_does_not_terminate(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])

    ctx = _handoff_ctx(conv_id, "ag_a")
    for bad in (
        {"agentId": "ag_b", "summary": "s"},
        {"agentId": "ag_b", "reason": "r"},
        {"summary": "s", "reason": "r"},
    ):
        result = await handoff_tool.handler(bad, ctx)
        assert result.ok is False
    assert pop_handoff(ctx.run_id) is None


@pytest.mark.asyncio
async def test_handoff_rejects_target_outside_conversation(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a"])  # ag_b 未入会

    ctx = _handoff_ctx(conv_id, "ag_a")
    result = await handoff_tool.handler(
        {"agentId": "ag_b", "reason": "r", "summary": "s"}, ctx
    )
    assert result.ok is False
    assert "会话成员" in result.error
    assert pop_handoff(ctx.run_id) is None


@pytest.mark.asyncio
async def test_handoff_rejects_busy_target_with_alternatives(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])
    await _seed_active_run("ag_b", conv_id, status="running")

    ctx = _handoff_ctx(conv_id, "ag_a")
    result = await handoff_tool.handler(
        {"agentId": "ag_b", "reason": "r", "summary": "s"}, ctx
    )
    assert result.ok is False
    assert "正忙" in result.error
    assert "ask_peer" in result.error
    assert "report_result" in result.error
    assert pop_handoff(ctx.run_id) is None


@pytest.mark.asyncio
async def test_handoff_rejects_self(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    conv_id = await _seed_group_conversation(["ag_a"])

    ctx = _handoff_ctx(conv_id, "ag_a")
    result = await handoff_tool.handler(
        {"agentId": "ag_a", "reason": "r", "summary": "s"}, ctx
    )
    assert result.ok is False
    assert pop_handoff(ctx.run_id) is None


@pytest.mark.asyncio
async def test_handoff_chain_cap_and_cycle_rejected(db, handoff_tool):
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    await _seed_agent("ag_c", "Carol")
    await _seed_agent("ag_d", "Dave")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b", "ag_c", "ag_d"])

    # 链 [a, b, c] 已满（3 个执行者）：d 拒绝，run 不终结
    ctx = _handoff_ctx(conv_id, "ag_c", run_id="run_c")
    register_handoff_chain(ctx.run_id, ["ag_a", "ag_b", "ag_c"])
    result = await handoff_tool.handler(
        {"agentId": "ag_d", "reason": "r", "summary": "s"}, ctx
    )
    assert result.ok is False
    assert "上限" in result.error
    assert pop_handoff(ctx.run_id) is None
    pop_handoff_chain(ctx.run_id)

    # 防环：目标已在链中
    ctx2 = _handoff_ctx(conv_id, "ag_c", run_id="run_c2")
    register_handoff_chain(ctx2.run_id, ["ag_a", "ag_c"])
    result2 = await handoff_tool.handler(
        {"agentId": "ag_a", "reason": "r", "summary": "s"}, ctx2
    )
    assert result2.ok is False
    assert "成环" in result2.error
    assert pop_handoff(ctx2.run_id) is None
    pop_handoff_chain(ctx2.run_id)


@pytest.mark.asyncio
async def test_handoff_chain_not_counted_against_dispatch_depth(db, handoff_tool):
    """dispatch_depth = 3（嵌套上限）的派发 run 仍可移交：链计数独立于派发深度。"""
    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])

    ctx = replace(_handoff_ctx(conv_id, "ag_a"), dispatch_depth=3)
    result = await handoff_tool.handler(
        {"agentId": "ag_b", "reason": "r", "summary": "s"}, ctx
    )
    assert result.ok is True
    assert pop_handoff(ctx.run_id) is not None


# ─── spawn_subagent_loop：payload 提取 + 链注册 ───────────────────────────────


@pytest.mark.asyncio
async def test_spawn_subagent_loop_extracts_handoff_payload(monkeypatch):
    from app.services import agent_loop
    from app.services.agent_runner import RunResult

    child_run_id = "run_child_h"

    def _fake_run_with_args(args):
        async def _child():
            return RunResult(
                run_id=child_run_id, status="complete", output_message_ids=[]
            )

        return child_run_id, asyncio.create_task(_child()), asyncio.Event()

    registered: dict[str, list[str]] = {}

    def _fake_register(run_id: str, chain: list[str]) -> None:
        registered[run_id] = chain

    monkeypatch.setattr(agent_loop, "run_with_args", _fake_run_with_args)
    monkeypatch.setattr("app.tools.handoff.register_handoff_chain", _fake_register)

    payload = HandoffPayload(agent_id="ag_c", reason="r", summary="移交摘要")
    from app.tools.handoff import _handoff_cache

    _handoff_cache[child_run_id] = payload

    result = await agent_loop.spawn_subagent_loop(
        agent_id="ag_b",
        task_description="task",
        conversation_id="conv_x",
        trigger_message_id="msg_x",
        parent_run_id="run_parent",
        parent_cancel_event=asyncio.Event(),
        allow_handoff=True,
        handoff_chain=["ag_b"],
    )

    assert result.status == "complete"
    assert result.handoff is payload
    assert result.text == "移交摘要"
    assert registered[child_run_id] == ["ag_b"]
    # 消费后清理
    assert pop_handoff(child_run_id) is None
    assert pop_handoff_chain(child_run_id) is None


@pytest.mark.asyncio
async def test_spawn_subagent_loop_cleanup_on_cancel(monkeypatch):
    from app.services import agent_loop

    child_run_id = "run_child_cancel"

    def _fake_run_with_args(args):
        async def _child():
            raise asyncio.CancelledError()

        return child_run_id, asyncio.create_task(_child()), asyncio.Event()

    monkeypatch.setattr(agent_loop, "run_with_args", _fake_run_with_args)

    from app.tools.handoff import _handoff_cache

    _handoff_cache[child_run_id] = HandoffPayload(agent_id="ag_c", reason="r", summary="s")

    result = await agent_loop.spawn_subagent_loop(
        agent_id="ag_b",
        task_description="task",
        conversation_id="conv_x",
        trigger_message_id="msg_x",
        parent_run_id="run_parent",
        parent_cancel_event=asyncio.Event(),
    )

    assert result.status == "aborted"
    assert result.handoff is None
    assert pop_handoff(child_run_id) is None


# ─── 5.2 响应者移交（catch ①） ────────────────────────────────────────────────


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs):
        from app.services.runner_registry import RunHandle

        self.calls.append(kwargs)
        return RunHandle(run_id=f"run_target_{len(self.calls)}")


@pytest.mark.asyncio
async def test_responder_handoff_starts_target_with_visible_message(
    db, monkeypatch, recorded_events
):
    from app.services.conversation_service import handle_responder_handoff

    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])

    fake = _FakeRunner()
    monkeypatch.setattr(
        "app.services.conversation_service.get_agent_runner", lambda: fake
    )

    payload = HandoffPayload(
        agent_id="ag_b",
        reason="前端任务",
        summary="已完成设计；剩余工作：实现组件；未决问题：配色",
    )
    await handle_responder_handoff(
        conversation_id=conv_id,
        from_agent_id="ag_a",
        from_run_id="run_a",
        trigger_message_id="msg_user",
        payload=payload,
        chain=["ag_a"],
        user_id=None,
    )

    # 可见系统消息（role=system）落库，内容含 from/to/理由/说明
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Message

    async with get_db() as session:
        msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
    assert len(msgs) == 1
    text = "\n".join(
        p.get("content", "") for p in msgs[0].parts_list if p.get("type") == "text"
    )
    assert "Alice" in text and "Bob" in text
    assert "前端任务" in text
    assert "剩余工作" in text

    # agent.handoff 事件发布，status=transferred
    handoff_events = [e for e in recorded_events if getattr(e, "type", "") == "agent.handoff"]
    assert len(handoff_events) == 1
    ev = handoff_events[0]
    assert ev.status == "transferred"
    assert ev.from_agent_id == "ag_a"
    assert ev.to_agent_id == "ag_b"
    assert ev.message.id == msgs[0].id

    # 目标 run 通过响应者启动路径触发，trigger = 系统消息
    assert len(fake.calls) == 1
    assert fake.calls[0]["agent_id"] == "ag_b"
    assert fake.calls[0]["trigger_message_id"] == msgs[0].id


@pytest.mark.asyncio
async def test_responder_handoff_rejected_when_target_busy(
    db, monkeypatch, recorded_events
):
    from app.services.conversation_service import handle_responder_handoff

    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])
    await _seed_active_run("ag_b", conv_id, status="running")

    fake = _FakeRunner()
    monkeypatch.setattr(
        "app.services.conversation_service.get_agent_runner", lambda: fake
    )

    payload = HandoffPayload(agent_id="ag_b", reason="r", summary="s")
    await handle_responder_handoff(
        conversation_id=conv_id,
        from_agent_id="ag_a",
        from_run_id="run_a",
        trigger_message_id="msg_user",
        payload=payload,
        chain=["ag_a"],
    )

    # 不启动目标 run；发布 failed 系统消息
    assert fake.calls == []
    handoff_events = [e for e in recorded_events if getattr(e, "type", "") == "agent.handoff"]
    assert len(handoff_events) == 1
    assert handoff_events[0].status == "failed"


@pytest.mark.asyncio
async def test_handoff_failure_visibility(db, recorded_events):
    from app.services.conversation_service import handle_handoff_failure

    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_a", "ag_b"])

    await handle_handoff_failure(
        conversation_id=conv_id,
        from_agent_id="ag_a",
        to_agent_id="ag_b",
        status="failed",
        run_id="run_b",
        error="boom",
    )

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Message

    async with get_db() as session:
        msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
    assert len(msgs) == 1
    text = "\n".join(
        p.get("content", "") for p in msgs[0].parts_list if p.get("type") == "text"
    )
    assert "Bob" in text and "Alice" in text
    assert "失败" in text

    handoff_events = [e for e in recorded_events if getattr(e, "type", "") == "agent.handoff"]
    assert len(handoff_events) == 1
    assert handoff_events[0].status == "failed"


def test_discard_run_handoff_on_cancel_paths():
    from app.services.agent_runner import RunArgs, _discard_run_handoff
    from app.tools.handoff import _handoff_cache

    _handoff_cache["run_discard"] = HandoffPayload(agent_id="ag_b", reason="r", summary="s")

    top_args = RunArgs(
        agent_id="ag_a",
        conversation_id="conv_x",
        trigger_message_id="msg_x",
    )
    _discard_run_handoff("run_discard", top_args)
    assert pop_handoff("run_discard") is None

    # 子 agent run（override_prompt）的 payload 归 spawn_subagent_loop 所有
    _handoff_cache["run_child"] = HandoffPayload(agent_id="ag_b", reason="r", summary="s")
    child_args = RunArgs(
        agent_id="ag_a",
        conversation_id="conv_x",
        trigger_message_id="msg_x",
        override_prompt="task",
    )
    _discard_run_handoff("run_child", child_args)
    assert pop_handoff("run_child") is not None


# ─── 5.3 派发移交（catch ②） ──────────────────────────────────────────────────


def _dispatch_ctx(conv_id: str, agent_id: str) -> ToolContext:
    return ToolContext(
        conversation_id=conv_id,
        workspace_path=None,
        agent_id=agent_id,
        run_id="run_orch",
        cancel_event=asyncio.Event(),
        dispatch_mode="coordinated",
    )


def _spawn_script(results: list):
    """Build a spawn mock replaying LoopRunResults in order, capturing calls."""
    calls: list[dict] = []

    async def _spawn(**kwargs):
        calls.append(kwargs)
        idx = min(len(calls) - 1, len(results) - 1)
        return results[idx]

    return calls, _spawn


@pytest.mark.asyncio
async def test_dispatch_handoff_redispatches_inplace(db, monkeypatch):
    """B 移交给 C：同一 worktree path / visibility / depth 原地重派，携带移交摘要。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_orch", "Orch")
    await _seed_agent("ag_b", "Bob")
    await _seed_agent("ag_c", "Carol")
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b", "ag_c"])

    payload = HandoffPayload(
        agent_id="ag_c",
        reason="后端任务",
        summary="已完成模型层；剩余工作：写 API 路由；未决问题：分页参数",
        files_changed=["backend/models.py"],
    )
    calls, spawn = _spawn_script([
        LoopRunResult(status="complete", text="B 的移交摘要", run_id="child_1", handoff=payload),
        LoopRunResult(status="complete", text="C 完成了", run_id="child_2"),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = _dispatch_ctx(conv_id, "ag_orch")
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "实现用户 API"}, ctx
    )

    assert result.ok is True
    assert len(calls) == 2

    first, second = calls
    assert second["agent_id"] == "ag_c"
    # 同一 worktree path / 同一 visibility / dispatch_depth 不变（换人不是嵌套）
    assert second["workspace_path"] == first["workspace_path"]
    assert second["dispatch_visibility"] == first["dispatch_visibility"]
    assert second["dispatch_depth"] == first["dispatch_depth"] == 1
    # 任务描述 = 原任务 + 前任移交摘要（含 reason 与剩余工作）
    assert second["task_description"].startswith("实现用户 API")
    assert "Bob" in second["task_description"] or "ag_b" in second["task_description"]
    assert "后端任务" in second["task_description"]
    assert "剩余工作" in second["task_description"]
    assert second["allow_handoff"] is True
    assert second["handoff_chain"] == ["ag_b", "ag_c"]

    # 最终 tool result 携带 executedBy / handoffChain
    assert result.value["status"] == "complete"
    assert result.value["summary"] == "C 完成了"
    assert result.value["executedBy"] == "ag_c"
    assert result.value["handoffChain"] == ["ag_b", "ag_c"]

    # run 记录透出移交元数据
    from app.db.engine import get_db
    from app.db.models import AgentRun

    async with get_db() as session:
        run_row = await session.get(AgentRun, "child_2")
    # child run 行可能不存在（mock spawn 未落库）；存在时必须带元数据
    if run_row is not None:
        assert run_row.dispatch_results == {
            "executedBy": "ag_c",
            "handoffChain": ["ag_b", "ag_c"],
        }


@pytest.mark.asyncio
async def test_dispatch_handoff_busy_target_falls_back(db, monkeypatch):
    """重派前二次校验失败（目标忙）→ 回退为正常 tool result，不中断派发。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_orch", "Orch")
    await _seed_agent("ag_b", "Bob")
    await _seed_agent("ag_c", "Carol")
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b", "ag_c"])
    await _seed_active_run("ag_c", conv_id, status="running")

    payload = HandoffPayload(agent_id="ag_c", reason="r", summary="B 的移交摘要")
    calls, spawn = _spawn_script([
        LoopRunResult(status="complete", text="B 的移交摘要", run_id="child_1", handoff=payload),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = _dispatch_ctx(conv_id, "ag_orch")
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "任务"}, ctx
    )

    assert result.ok is True
    assert len(calls) == 1  # 不重派
    assert result.value["status"] == "failed"
    assert "正忙" in result.value["summary"]
    assert result.value["executedBy"] == "ag_b"
    assert result.value["handoffChain"] == ["ag_b"]


@pytest.mark.asyncio
async def test_dispatch_handoff_chain_cap_stops_loop(db, monkeypatch):
    """链长达到上限（3 个执行者）后不再重派，tool result 标明上限。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    for aid, name in (("ag_orch", "Orch"), ("ag_b", "Bob"), ("ag_c", "Carol"), ("ag_d", "Dave")):
        await _seed_agent(aid, name)
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b", "ag_c", "ag_d"])

    calls, spawn = _spawn_script([
        LoopRunResult(
            status="complete", text="b→c", run_id="child_1",
            handoff=HandoffPayload(agent_id="ag_c", reason="r1", summary="s1"),
        ),
        LoopRunResult(
            status="complete", text="c→d", run_id="child_2",
            handoff=HandoffPayload(agent_id="ag_d", reason="r2", summary="s2"),
        ),
        LoopRunResult(
            status="complete", text="d→e", run_id="child_3",
            handoff=HandoffPayload(agent_id="ag_orch", reason="r3", summary="s3"),
        ),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = _dispatch_ctx(conv_id, "ag_orch")
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "任务"}, ctx
    )

    assert result.ok is True
    assert len(calls) == MAX_HANDOFF_CHAIN  # b、c、d 三个执行者，d 的移交不再执行
    assert result.value["status"] == "failed"
    assert "上限" in result.value["summary"]
    assert result.value["executedBy"] == "ag_d"
    assert result.value["handoffChain"] == ["ag_b", "ag_c", "ag_d"]


@pytest.mark.asyncio
async def test_dispatch_handoff_hidden_visibility_inherited(db, monkeypatch):
    """hidden 克隆派发移交保持 hidden；visible 群成员派发移交保持 visible。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_solo", "Solo")
    await _seed_agent("ag_peer", "Peer")
    conv_id = await _seed_group_conversation(["ag_solo", "ag_peer"])

    payload = HandoffPayload(agent_id="ag_peer", reason="r", summary="s")
    calls, spawn = _spawn_script([
        LoopRunResult(status="complete", text="移交", run_id="child_1", handoff=payload),
        LoopRunResult(status="complete", text="done", run_id="child_2"),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    # clone-self 派发（无 agentId）→ hidden
    ctx = ToolContext(
        conversation_id=conv_id,
        workspace_path=None,
        agent_id="ag_solo",
        run_id="run_solo",
        cancel_event=asyncio.Event(),
        dispatch_mode="solo",
    )
    result = await task_dispatch_tool.handler({"taskDescription": "任务"}, ctx)

    assert result.ok is True
    assert calls[0]["dispatch_visibility"] == "hidden"
    assert calls[1]["dispatch_visibility"] == "hidden"
    assert result.value["executedBy"] == "ag_peer"
    assert result.value["handoffChain"] == ["ag_solo", "ag_peer"]


@pytest.mark.asyncio
async def test_dispatch_handoff_cancelled_parent_stops_chain(db, monkeypatch):
    """用户停止父 run：cancel_event 置位后不再重派，派发方收到中断说明。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_orch", "Orch")
    await _seed_agent("ag_b", "Bob")
    await _seed_agent("ag_c", "Carol")
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b", "ag_c"])

    payload = HandoffPayload(agent_id="ag_c", reason="r", summary="s")
    calls, spawn = _spawn_script([
        LoopRunResult(status="complete", text="移交", run_id="child_1", handoff=payload),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = _dispatch_ctx(conv_id, "ag_orch")
    ctx.cancel_event.set()  # 捕获 handoff 前父 run 已被停止
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "任务"}, ctx
    )

    assert result.ok is True
    assert len(calls) == 1
    assert result.value["status"] == "failed"
    assert "停止" in result.value["summary"]


@pytest.mark.asyncio
async def test_dispatch_aborted_run_returns_error(db, monkeypatch):
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_orch", "Orch")
    await _seed_agent("ag_b", "Bob")
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b"])

    calls, spawn = _spawn_script([
        LoopRunResult(status="aborted", text="cancelled", run_id="child_1"),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = _dispatch_ctx(conv_id, "ag_orch")
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "任务"}, ctx
    )

    assert result.ok is False
    assert "aborted" in result.error


@pytest.mark.asyncio
async def test_dispatch_merge_back_once_after_swap_loop(db, monkeypatch):
    """worktree 的 merge-back / cleanup 在换人循环之后只执行一次。"""
    from app.services.agent_loop import LoopRunResult
    from app.tools.task_dispatch import task_dispatch_tool

    await _seed_agent("ag_orch", "Orch")
    await _seed_agent("ag_b", "Bob")
    await _seed_agent("ag_c", "Carol")
    conv_id = await _seed_group_conversation(["ag_orch", "ag_b", "ag_c"])

    merge_calls: list = []
    cleanup_calls: list = []

    class _FakeWt:
        path = "wt_path_1"

    async def _fake_create(*args, **kwargs):
        return _FakeWt()

    async def _fake_merge(wt):
        merge_calls.append(wt)

    async def _fake_cleanup(wt):
        cleanup_calls.append(wt)

    monkeypatch.setattr(
        "app.services.worktree_service.create_worktree", _fake_create
    )
    monkeypatch.setattr(
        "app.services.worktree_service.merge_worktree_back", _fake_merge
    )
    monkeypatch.setattr(
        "app.services.worktree_service.cleanup_worktree", _fake_cleanup
    )

    payload = HandoffPayload(agent_id="ag_c", reason="r", summary="s")
    calls, spawn = _spawn_script([
        LoopRunResult(status="complete", text="移交", run_id="child_1", handoff=payload),
        LoopRunResult(status="complete", text="done", run_id="child_2"),
    ])
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    ctx = replace(_dispatch_ctx(conv_id, "ag_orch"), workspace_path="/tmp/test")
    result = await task_dispatch_tool.handler(
        {"agentId": "ag_b", "taskDescription": "任务"}, ctx
    )

    assert result.ok is True
    assert len(calls) == 2
    # 同一 worktree path 延续 + 仅一次 merge-back / cleanup
    assert calls[0]["workspace_path"] == "wt_path_1"
    assert calls[1]["workspace_path"] == "wt_path_1"
    assert len(merge_calls) == 1
    assert len(cleanup_calls) == 1


# ─── 4.x 注入规则与 prompt 指导 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_injection_rules(db, monkeypatch):
    """dispatch run 注入；DAG / 单人会话 / 未声明允许位不注入。"""
    from app.services import agent_loop
    from app.services.agent_runner import RunArgs, RunExecutionResult

    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")

    captured: dict[str, list[str] | None] = {}

    async def _fake_execute_simple_run(run_id, cancel_event, args, prompt, attachments):
        captured["tool_names"] = args.override_tool_names
        return RunExecutionResult()

    monkeypatch.setattr(
        agent_loop, "execute_simple_run", _fake_execute_simple_run
    )

    def _args(conv_id: str, allow_handoff: bool) -> RunArgs:
        return RunArgs(
            agent_id="ag_a",
            conversation_id=conv_id,
            trigger_message_id="msg_x",
            override_prompt="task",
            allow_handoff=allow_handoff,
        )

    # 双人会话 + dispatch run（allow_handoff=True）→ 注入
    conv2 = await _seed_group_conversation(["ag_a", "ag_b"])
    await agent_loop._run_subagent_loop(
        "run_1", asyncio.Event(), _args(conv2, True), "task", []
    )
    assert "handoff" in captured["tool_names"]

    # 未声明允许位（DAG 节点 / ask_peer mini-run）→ 不注入
    await agent_loop._run_subagent_loop(
        "run_2", asyncio.Event(), _args(conv2, False), "task", []
    )
    assert "handoff" not in captured["tool_names"]

    # 单人会话（成员 < 2）→ 不注入
    conv1 = await _seed_group_conversation(["ag_a"])
    await agent_loop._run_subagent_loop(
        "run_3", asyncio.Event(), _args(conv1, True), "task", []
    )
    assert "handoff" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_solo_responder_injection_needs_two_members(db, monkeypatch):
    """direct responder run：群聊（成员 ≥ 2）注入，单人不注入。"""
    from app.services import agent_loop
    from app.services.agent_runner import RunArgs, RunExecutionResult

    await _seed_agent("ag_a", "Alice")
    await _seed_agent("ag_b", "Bob")

    captured: dict[str, list[str] | None] = {}

    async def _fake_execute_simple_run(run_id, cancel_event, args, prompt, attachments):
        captured["tool_names"] = args.override_tool_names
        return RunExecutionResult()

    monkeypatch.setattr(
        agent_loop, "execute_simple_run", _fake_execute_simple_run
    )

    conv2 = await _seed_group_conversation(["ag_a", "ag_b"])
    await agent_loop._run_solo_loop(
        "run_s1",
        asyncio.Event(),
        RunArgs(agent_id="ag_a", conversation_id=conv2, trigger_message_id="msg_x"),
        "hi",
        [],
    )
    assert "handoff" in captured["tool_names"]

    conv1 = await _seed_group_conversation(["ag_a"])
    await agent_loop._run_solo_loop(
        "run_s2",
        asyncio.Event(),
        RunArgs(agent_id="ag_a", conversation_id=conv1, trigger_message_id="msg_x"),
        "hi",
        [],
    )
    assert "handoff" not in captured["tool_names"]


def test_handoff_prompt_suffix_only_when_enabled():
    from app.services.agent_loop import (
        build_coordinated_system_prompt,
        build_solo_system_prompt,
        build_subagent_system_prompt,
    )

    assert "handoff" not in build_solo_system_prompt("base")
    assert "handoff" in build_solo_system_prompt("base", handoff_enabled=True)
    assert "handoff" not in build_subagent_system_prompt("base")
    assert "handoff" in build_subagent_system_prompt("base", handoff_enabled=True)
    assert "handoff" not in build_coordinated_system_prompt("base", "", plan_enabled=True)
    assert "handoff" in build_coordinated_system_prompt(
        "base", "", plan_enabled=True, handoff_enabled=True
    )
    # summary 质量要求（剩余工作与未决问题）进 prompt
    assert "剩余工作" in build_solo_system_prompt("base", handoff_enabled=True)
