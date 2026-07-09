"""Unified Agent Loop — single while-loop abstraction for solo / coordinated / subagent.

This module provides the ``run_agent_loop`` entry point that unifies three modes:
  - ``solo``: single agent, Claude Code style (end_turn stops, model self-verifies)
  - ``coordinated``: orchestrator agent with TaskDispatch tool
  - ``subagent``: dispatched child agent (spawned by TaskDispatch tool)

All modes delegate to ``execute_simple_run`` — the existing while-loop that handles
model calls, tool execution, stream consumption, and event publishing. The only
difference between modes is the tool list and system prompt.

For subagent dispatch, ``run_agent_loop`` spawns a new run via ``run_with_args``
and awaits its completion, returning the sub-agent's final text.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Literal

from sqlalchemy import select

from app.adapters.base import AdapterAttachment
from app.db.engine import get_db
from app.db.models import Agent, Conversation
from app.services.agent_runner import (
    RunArgs,
    RunExecutionResult,
    execute_simple_run,
    run_with_args,
)

logger = logging.getLogger(__name__)


LoopMode = Literal["solo", "coordinated", "subagent"]


@dataclass
class AgentLoopConfig:
    """Configuration for a unified agent loop run."""

    mode: LoopMode
    conversation_id: str
    trigger_message_id: str
    cancel_event: asyncio.Event | None = None
    parent_run_id: str | None = None
    override_prompt: str | None = None
    override_workspace_path: str | None = None


@dataclass
class LoopRunResult:
    """Result of a subagent loop run (returned by TaskDispatch tool)."""

    status: str  # 'complete' | 'failed' | 'aborted'
    text: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)


# ─── Coordinated mode system prompt ───────────────────────────────────────────
_COORDINATED_PROMPT_SUFFIX = """

## 协调者模式（Unified Agent Loop）

你是一个群聊中的协调者。你可以：
1. 通过 task_dispatch 工具将任务派给群里的其他 Agent
2. 自己直接干活（使用 fs_write / bash / read_artifact 等标准工具）

### 核心原则：优先派发
你是协调者，不是执行者。你的首要职责是把任务**分配给合适的 Agent**，而不是自己包揽。
群里存在专门的前端、后端、设计等 Agent，他们比自己更适合做对应的工作。

### 使用 task_dispatch 的时机（应主动派发）
- 任务涉及前端 UI、后端 API、数据库等不同领域 → 按领域拆分派发
- 任务有前后依赖但可以分步并行 → 先派发独立的，再处理依赖的
- 群里有匹配该任务能力的 Agent → 优先派发给他
- 用户明确要求生成完整项目（含多个模块） → 拆分后派发

### 自己干的时机（仅在以下情况）
- 只有信息查询、简单回答、读文件等轻量操作
- 没有任何 Agent 能处理该子任务
- 派发开销大于任务本身（如改一行代码）

### 派发规则
- 派发时提供清晰、自包含的任务描述（子 Agent 看不到群聊上下文）
- **并行派发**：在同一个回复中同时发起多个 task_dispatch 工具调用，系统会并行执行它们。
  例如，要同时让两个 Agent 分别写报告，请在一次回复中同时发出两个 task_dispatch 调用，
  它们将并行运行，而不是一个完成后才开始另一个。
- 收到子 Agent 返回后审查结果，不满意就重新派发并附上修正说明
- 所有工作完成后，给用户一条自然语言总结

### 群成员列表
{agent_roster}
"""


def build_coordinated_system_prompt(
    base_system_prompt: str, agent_roster: str = ""
) -> str:
    """Build the system prompt for coordinated (orchestrator) mode.

    Args:
        base_system_prompt: The orchestrator agent's own system prompt.
        agent_roster: Formatted list of available agents in the conversation.
    """
    suffix = _COORDINATED_PROMPT_SUFFIX.replace("{agent_roster}", agent_roster)
    return base_system_prompt + suffix


def _format_agent_roster(agents: list[Agent], orchestrator_id: str) -> str:
    """Format the list of conversation agents for injection into the prompt.

    Excludes the orchestrator itself (it doesn't need to dispatch to itself).
    """
    lines: list[str] = []
    for ag in agents:
        if ag.id == orchestrator_id:
            continue
        caps = ", ".join(ag.capabilities) if ag.capabilities else "无"
        lines.append(
            f"- agentId: `{ag.id}` | 名称: {ag.name} | "
            f"描述: {ag.description} | 能力: {caps}"
        )
    if not lines:
        return "（群里没有其他可派发的 Agent）"
    return "\n".join(lines)


# ─── Solo mode soft self-verify prompt ────────────────────────────────────────
_SOLO_VERIFY_SUFFIX = """

## 完成前自检（建议但不强制）
- 建议在完成前跑一遍 typecheck / lint / tests（如果项目有的话）
- 检查文件是否正确写入
- 但你可以自行判断是否需要验证——这不是强制 gate
"""


def build_solo_system_prompt(base_system_prompt: str) -> str:
    """Build the system prompt for solo mode with soft self-verify reminder."""
    return base_system_prompt + _SOLO_VERIFY_SUFFIX


# ─── Unified loop entry for solo / coordinated (called from execute_run) ──────
async def run_agent_loop(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
    mode: LoopMode,
) -> RunExecutionResult:
    """Run an agent loop in the specified mode.

    For solo mode: delegates to execute_simple_run with agent's own tools
    + soft self-verify prompt.

    For coordinated mode: delegates to execute_simple_run with agent's tools
    + task_dispatch tool + coordinated system prompt.

    This function is called from execute_run for top-level runs.
    Subagent dispatch uses spawn_subagent_loop instead.
    """
    if mode == "solo":
        return await _run_solo_loop(
            run_id, cancel_event, args, prompt, attachments
        )

    if mode == "coordinated":
        return await _run_coordinated_loop(
            run_id, cancel_event, args, prompt, attachments
        )

    # subagent mode at top level should not happen; fall back to solo
    logger.warning(
        "[agent_loop] subagent mode requested at top level for run %s; "
        "falling back to solo",
        run_id,
    )
    return await _run_solo_loop(
        run_id, cancel_event, args, prompt, attachments
    )


async def _run_solo_loop(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    """Solo mode: inject soft self-verify system prompt."""
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == args.agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise RuntimeError(f"Agent not found: {args.agent_id}")
        db.expunge(agent)

    solo_prompt = build_solo_system_prompt(agent.system_prompt)
    solo_args = replace(
        args,
        override_system_prompt=solo_prompt,
    )

    return await execute_simple_run(
        run_id, cancel_event, solo_args, prompt, attachments
    )


async def _run_coordinated_loop(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    """Coordinated mode: inject TaskDispatch tool + coordinated system prompt.

    Loads the conversation's agent roster and injects it into the prompt so
    the orchestrator knows who it can dispatch to.
    """
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == args.agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise RuntimeError(f"Agent not found: {args.agent_id}")
        db.expunge(agent)

        # Load conversation to get agent_ids
        conv = (
            await db.execute(
                select(Conversation).where(
                    Conversation.id == args.conversation_id
                )
            )
        ).scalar_one_or_none()
        agent_ids = conv.agent_ids_list if conv else []

        # Load all agents in the conversation (for roster injection)
        roster_agents: list[Agent] = []
        if agent_ids:
            result = await db.execute(
                select(Agent).where(Agent.id.in_(agent_ids))
            )
            roster_agents = list(result.scalars().all())
            for ra in roster_agents:
                db.expunge(ra)

    # Build tool list: agent's own tools + task_dispatch
    tool_names = list(agent.tool_names_list)
    if "task_dispatch" not in tool_names:
        tool_names.append("task_dispatch")

    roster = _format_agent_roster(roster_agents, agent.id)
    coordinated_prompt = build_coordinated_system_prompt(
        agent.system_prompt, roster
    )

    logger.info(
        "[agent_loop] coordinated mode run=%s orchestrator=%s roster=%d agents",
        run_id,
        agent.id,
        len(roster_agents) - 1 if roster_agents else 0,
    )

    coordinated_args = replace(
        args,
        override_tool_names=tool_names,
        override_system_prompt=coordinated_prompt,
    )

    return await execute_simple_run(
        run_id, cancel_event, coordinated_args, prompt, attachments
    )


# ─── Subagent dispatch (called from TaskDispatch tool) ────────────────────────
async def spawn_subagent_loop(
    agent_id: str,
    task_description: str,
    conversation_id: str,
    trigger_message_id: str,
    parent_run_id: str,
    parent_cancel_event: asyncio.Event,
    workspace_path: str | None = None,
) -> LoopRunResult:
    """Spawn a subagent loop for a dispatched task.

    Used by the TaskDispatch tool. Creates a new run via run_with_args
    and awaits its completion. Returns the sub-agent's final text.
    """
    args = RunArgs(
        agent_id=agent_id,
        conversation_id=conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=parent_run_id,
        override_prompt=task_description,
        parent_cancel_event=parent_cancel_event,
        override_workspace_path=workspace_path,
    )

    child_run_id, child_task, _child_cancel = run_with_args(args)

    try:
        run_result = await child_task
    except asyncio.CancelledError:
        return LoopRunResult(status="aborted", text="Subagent run was cancelled")
    except Exception as err:  # noqa: BLE001 - surface error to orchestrator
        logger.exception("[agent_loop] subagent run failed: %s", err)
        return LoopRunResult(
            status="failed",
            text=f"Subagent run failed: {err}",
        )

    # Extract the final text from the run's output messages
    text = await _extract_run_final_text(
        child_run_id, conversation_id, run_result.output_message_ids
    )

    return LoopRunResult(
        status=run_result.status,
        text=text or "(subagent produced no text output)",
        artifact_ids=run_result.artifact_ids,
        output_message_ids=run_result.output_message_ids,
    )


async def _extract_run_final_text(
    run_id: str,
    conversation_id: str,
    output_message_ids: list[str],
) -> str:
    """Extract the final text output from a completed run's messages."""
    if not output_message_ids:
        return ""

    from app.db.models import Message

    async with get_db() as db:
        msgs = (
            await db.execute(
                select(Message)
                .where(Message.id.in_(output_message_ids))
                .order_by(Message.created_at)
            )
        ).scalars().all()

    if not msgs:
        return ""

    # Concatenate text parts from all output messages
    from app.services.orchestrator_prompts import extract_text_from_parts

    texts: list[str] = []
    for msg in msgs:
        text = extract_text_from_parts(msg.parts_list).strip()
        if text:
            texts.append(text)

    return "\n\n".join(texts)


# ─── Dispatch mode helper ─────────────────────────────────────────────────────
def get_dispatch_mode(conversation: Conversation | None) -> LoopMode:
    """Get the effective dispatch mode from a conversation, defaulting to 'solo'.

    Uses defensive getattr for backward compatibility with pre-migration data.
    """
    if conversation is None:
        return "solo"
    mode = getattr(conversation, "dispatch_mode", None) or "solo"
    if mode == "orchestrated":
        return "coordinated"
    return "solo"
