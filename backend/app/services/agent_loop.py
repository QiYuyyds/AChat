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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from sqlalchemy import select

from app.adapters.base import AdapterAttachment
from app.db.engine import get_db
from app.db.models import Agent, Conversation
from app.infra.cache_helpers import get_agent_cached
from app.services.agent_runner import (
    RunArgs,
    RunExecutionResult,
    execute_simple_run,
    run_with_args,
)

logger = logging.getLogger(__name__)


LoopMode = Literal["solo", "coordinated", "subagent"]

MAX_DISPATCH_DEPTH = 3


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
    """Result of a subagent loop run (returned by TaskDispatch / dispatch_plan)."""

    status: str  # 'complete' | 'failed' | 'aborted'
    text: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    run_id: str | None = None  # child run ID (for dispatch events)


# ─── Coordinated mode system prompt ───────────────────────────────────────────
_COORDINATED_PROMPT_SUFFIX = """

## 协调者模式（Unified Agent Loop）

你是一个群聊中的协调者。你可以：
1. 通过 task_dispatch 工具将单个任务派给群里的其他 Agent
2. 通过 dispatch_plan 工具一次性声明一个结构化 DAG（含依赖关系的多任务计划）
3. 自己直接干活（使用 fs_write / bash / read_artifact 等标准工具）

### 核心原则：优先派发
你是协调者，不是执行者。你的首要职责是把任务**分配给合适的 Agent**，而不是自己包揽。
群里存在专门的前端、后端、设计等 Agent，他们比自己更适合做对应的工作。

### 使用 dispatch_plan 的时机（结构化多任务 DAG）
- 任务有明确的依赖关系图（DAG）
- 需要 3+ 个子任务，且部分可并行
- 用户要求生成完整项目（PRD → 设计 → 前端+后端 → 集成测试）
- 能并行的不写 dependsOn，有依赖的明确写 dependsOn

### 使用 task_dispatch 的时机（即时单任务）
- 只需派发一个任务
- 需要根据上一个结果决定下一步（探索性）
- 快速试错
- 任务涉及前端 UI、后端 API、数据库等不同领域 → 按领域拆分派发
- 群里有匹配该任务能力的 Agent → 优先派发给他

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

# ─── Solo mode dispatch guidance (injected when task_dispatch is available) ───
_SOLO_DISPATCH_SUFFIX = """

## 子任务派发（task_dispatch）

你可以通过 task_dispatch 工具克隆自己来处理子任务。当你面对一个可以拆分的复杂任务时，
考虑是否将部分工作派发给子 Agent。

### 何时使用
- 任务可以拆分为独立的子任务并行处理
- 某个子任务需要大量独立的研究或文件操作
- 你想在处理主任务的同时让子 Agent 处理辅助工作

### 何时不用
- 简单任务（改一行代码、读一个文件）
- 子任务之间有强依赖、无法并行
- 派发开销大于任务本身

### 注意事项
- 子 Agent 看不到当前对话上下文，任务描述必须自包含
- 子 Agent 共享你的工作空间，注意文件写入冲突
- 递归深度有限（最多 3 层），深层子 Agent 无法继续派发
"""


# ─── Subagent mode prompt suffix ──────────────────────────────────────────────
_SUBAGENT_SUFFIX = """

## 子 Agent 模式

你是一个被派发的子 Agent。你的任务是完成给你指定的任务描述。

### 注意事项
- 你看不到父 Agent 的对话上下文，只看到任务描述
- 你共享父 Agent 的工作空间
- 你可以克隆自己进一步派发子任务（最多递归 {max_depth} 层）
- 完成后返回清晰的结果摘要，父 Agent 会收到你的输出

### 何时派发子任务
- 任务可拆分为独立子任务并行处理
- 子任务需要大量独立操作

### 何时不用
- 简单任务直接做
- 子任务间有强依赖
"""


def build_solo_system_prompt(base_system_prompt: str, dispatch_enabled: bool = False) -> str:
    """Build the system prompt for solo mode with soft self-verify reminder.

    When dispatch_enabled, also appends dispatch guidance.
    """
    prompt = base_system_prompt + _SOLO_VERIFY_SUFFIX
    if dispatch_enabled:
        prompt += _SOLO_DISPATCH_SUFFIX
    return prompt


def build_subagent_system_prompt(base_system_prompt: str) -> str:
    """Build the system prompt for subagent mode."""
    return base_system_prompt + _SUBAGENT_SUFFIX.replace("{max_depth}", str(MAX_DISPATCH_DEPTH))


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

    if mode == "subagent":
        return await _run_subagent_loop(
            run_id, cancel_event, args, prompt, attachments
        )

    # unknown mode; fall back to solo
    logger.warning(
        "[agent_loop] unknown mode '%s' for run %s; falling back to solo",
        mode,
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
    """Solo mode: inject soft self-verify system prompt + optional task_dispatch."""
    agent = await get_agent_cached(args.agent_id)
    if agent is None:
        raise RuntimeError(f"Agent not found: {args.agent_id}")

    # Inject task_dispatch when below max depth
    dispatch_enabled = args.dispatch_depth < MAX_DISPATCH_DEPTH
    tool_names = list(args.override_tool_names or agent.tool_names_list)
    if dispatch_enabled and "task_dispatch" not in tool_names:
        tool_names.append("task_dispatch")

    solo_prompt = build_solo_system_prompt(agent.system_prompt, dispatch_enabled)
    solo_args = replace(
        args,
        override_tool_names=tool_names,
        override_system_prompt=solo_prompt,
    )

    logger.info(
        "[agent_loop] solo mode run=%s agent=%s dispatch=%s depth=%d",
        run_id,
        agent.id,
        "on" if dispatch_enabled else "off",
        args.dispatch_depth,
    )

    return await execute_simple_run(
        run_id, cancel_event, solo_args, prompt, attachments
    )


async def _run_subagent_loop(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    """Subagent mode: inject task_dispatch (when depth < MAX) + subagent prompt.

    Delegates to execute_simple_run with overridden tools and system prompt.
    """
    agent = await get_agent_cached(args.agent_id)
    if agent is None:
        raise RuntimeError(f"Agent not found: {args.agent_id}")

    dispatch_enabled = args.dispatch_depth < MAX_DISPATCH_DEPTH
    tool_names = list(args.override_tool_names or agent.tool_names_list)
    if dispatch_enabled and "task_dispatch" not in tool_names:
        tool_names.append("task_dispatch")

    subagent_prompt = build_subagent_system_prompt(agent.system_prompt)
    subagent_args = replace(
        args,
        override_tool_names=tool_names,
        override_system_prompt=subagent_prompt,
    )

    logger.info(
        "[agent_loop] subagent mode run=%s agent=%s dispatch=%s depth=%d",
        run_id,
        agent.id,
        "on" if dispatch_enabled else "off",
        args.dispatch_depth,
    )

    return await execute_simple_run(
        run_id, cancel_event, subagent_args, prompt, attachments
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
    agent = await get_agent_cached(args.agent_id)
    if agent is None:
        raise RuntimeError(f"Agent not found: {args.agent_id}")

    async with get_db() as db:
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

    # Build tool list: agent's own tools + task_dispatch + dispatch_plan
    tool_names = list(agent.tool_names_list)
    if "task_dispatch" not in tool_names:
        tool_names.append("task_dispatch")
    if "dispatch_plan" not in tool_names:
        tool_names.append("dispatch_plan")

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
    on_start: Callable[[str], None] | None = None,
    dispatch_depth: int = 0,
    dispatch_visibility: str = "visible",
    user_id: str | None = None,
) -> LoopRunResult:
    """Spawn a subagent loop for a dispatched task.

    Used by the TaskDispatch and dispatch_plan tools. Creates a new run via
    run_with_args and awaits its completion. Returns the sub-agent's final text.

    Args:
        on_start: Optional callback invoked with the child run ID immediately
            after the run is created (before awaiting). Used by dispatch_plan
            to emit ``dispatch.start`` events.
        dispatch_depth: Recursion depth (parent's depth + 1).
        dispatch_visibility: "visible" for group-member dispatch, "hidden" for
            clone-self dispatch.
    """
    from app.observability import start_span
    args = RunArgs(
        agent_id=agent_id,
        conversation_id=conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=parent_run_id,
        override_prompt=task_description,
        parent_cancel_event=parent_cancel_event,
        override_workspace_path=workspace_path,
        dispatch_depth=dispatch_depth,
        dispatch_visibility=dispatch_visibility,
        user_id=user_id,
    )

    child_run_id, child_task, _child_cancel = run_with_args(args)

    if on_start is not None:
        on_start(child_run_id)

    with start_span(
        "tool.dispatch",
        child_agent_id=agent_id,
        dispatch_depth=dispatch_depth,
        dispatch_visibility=dispatch_visibility,
    ):
        try:
            run_result = await child_task
        except asyncio.CancelledError:
            return LoopRunResult(
                status="aborted",
                text="Subagent run was cancelled",
                run_id=child_run_id,
            )
        except Exception as err:  # noqa: BLE001 - surface error to orchestrator
            logger.exception("[agent_loop] subagent run failed: %s", err)
            return LoopRunResult(
                status="failed",
                text=f"Subagent run failed: {err}",
                run_id=child_run_id,
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
            run_id=child_run_id,
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
