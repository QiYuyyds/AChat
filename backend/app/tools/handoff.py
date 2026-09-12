"""handoff terminal tool — one-way transfer of the current run to another member.

Calling ``handoff`` terminates the run (see TERMINAL_TOOLS in agent_runner.py),
same mechanism as ``report_result``. The structured payload is cached in-process
and translated by the catch layers:

  - catch ① (agent_runner → conversation_service): responder run hands off to
    another conversation member, which is started as the new responder.
  - catch ② (task_dispatch): dispatched run hands off mid-task; the dispatch
    handler re-spawns the target on the same worktree / visibility.

Guardrails enforced here (tool returns err → run does NOT terminate):
  - target must be a member of the current conversation (agent_ids whitelist)
  - target must not be busy (active/queued run in the conversation)
  - handoff chain length cap (MAX_HANDOFF_CHAIN), cycle avoidance (target must
    not already be in the chain)

The chain is a local, per-trigger counter (D4): it never enters ToolContext and
is carried in a process-local registry keyed by run id, populated by the catch
layers when they (re)start a run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import AgentRun, Conversation
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

logger = logging.getLogger(__name__)

HANDOFF_TOOL_NAME = "handoff"

# Max executors per trigger source (original executor + handoff targets).
# Independent of dispatch_depth — changing executor is not nesting (D4).
MAX_HANDOFF_CHAIN = 3


@dataclass
class HandoffPayload:
    """Structured handoff submitted via the handoff terminal tool."""

    agent_id: str  # handoff target
    reason: str
    summary: str
    key_decisions: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


# run_id → pending handoff payload (consumed by the catch layers)
_handoff_cache: dict[str, HandoffPayload] = {}

# run_id → executor chain including the run's own agent, e.g. [B, C] when the
# task was dispatched to B and B handed off to C. Written by the catch layers
# when they start a run; read by the tool handler for the chain cap / anti-loop.
_handoff_chain_registry: dict[str, list[str]] = {}


def register_handoff_chain(run_id: str, chain: list[str]) -> None:
    """Record the handoff chain for a run about to start (catch layers only)."""
    _handoff_chain_registry[run_id] = list(chain)


def get_handoff_chain(run_id: str) -> list[str] | None:
    return _handoff_chain_registry.get(run_id)


def pop_handoff_chain(run_id: str) -> list[str] | None:
    return _handoff_chain_registry.pop(run_id, None)


def pop_handoff(run_id: str) -> HandoffPayload | None:
    return _handoff_cache.pop(run_id, None)


def _chain_for_run(ctx: ToolContext) -> list[str]:
    """Current chain for the calling run; a fresh run starts with itself."""
    return get_handoff_chain(ctx.run_id) or [ctx.agent_id]


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["agentId", "reason", "summary"],
    "properties": {
        "agentId": {
            "type": "string",
            "description": "移交目标的 agent ID（必须是当前会话成员，且不在当前移交链中）。",
        },
        "reason": {
            "type": "string",
            "description": "移交理由（必填）：为什么任务应该由对方继续。",
        },
        "summary": {
            "type": "string",
            "description": (
                "移交说明（必填）：让接手者无需追问即可继续——必须包含"
                "已完成的工作、剩余工作与未决问题。控制在 500 token 以内。"
            ),
        },
        "keyDecisions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "关键决策或发现列表（可选）。",
        },
        "filesChanged": {
            "type": "array",
            "items": {"type": "string"},
            "description": "已新增或修改的文件路径列表（可选）。",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "已产出的 artifact ID 列表（可选）。",
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    if not isinstance(args, dict):
        return err("handoff requires an object payload")

    target = args.get("agentId")
    reason = args.get("reason")
    summary = args.get("summary")
    if not target or not isinstance(target, str):
        return err("handoff requires 'agentId' (string)")
    if not reason or not isinstance(reason, str):
        return err("handoff requires 'reason' (string)")
    if not summary or not isinstance(summary, str):
        return err("handoff requires 'summary' (string)")

    if target == ctx.agent_id:
        return err("不能将任务移交给自己")

    chain = _chain_for_run(ctx)
    if target in chain:
        return err(
            f"移交目标 {target} 已在当前移交链中（{' → '.join(chain)}），"
            "禁止成环；请改用 report_result 汇报。"
        )
    if len(chain) >= MAX_HANDOFF_CHAIN:
        return err(
            f"移交链已达上限（{MAX_HANDOFF_CHAIN}：{' → '.join(chain)}），"
            "无法继续移交；请改用 report_result 汇报当前进展。"
        )

    async with get_local_db() as db:
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == ctx.conversation_id)
            )
        ).scalar_one_or_none()
        if conv is None or target not in conv.agent_ids_list:
            return err(
                f"移交目标 {target} 不在会话成员中，无法移交；"
                "请从会话成员中选择目标。"
            )

        busy = (
            await db.execute(
                select(AgentRun.id).where(
                    AgentRun.conversation_id == ctx.conversation_id,
                    AgentRun.agent_id == target,
                    AgentRun.status.in_(["running", "queued"]),
                )
            )
        ).first()
    if busy is not None:
        return err(
            f"移交目标 {target} 正忙（已有进行中的运行）。"
            "可改用 ask_peer 等待其产出，或用 report_result 将任务交回派发方。"
        )

    _handoff_cache[ctx.run_id] = HandoffPayload(
        agent_id=target,
        reason=reason,
        summary=summary,
        key_decisions=args.get("keyDecisions", []) or [],
        files_changed=args.get("filesChanged", []) or [],
        artifacts=args.get("artifacts", []) or [],
    )
    logger.info(
        "[handoff] run=%s agent=%s -> %s (chain=%s)",
        ctx.run_id,
        ctx.agent_id,
        target,
        chain + [target],
    )
    return ok({"status": "handoff-recorded", "agentId": target})


handoff_tool = ToolDef(
    name=HANDOFF_TOOL_NAME,
    description=(
        "将当前任务单向移交给会话内的另一位成员，由对方接手继续。这是终态工具——"
        "调用后你的执行立即结束。仅当任务明确落在其他成员的能力域时使用"
        "（不要接到任务就反射性甩锅）；如果你需要对方完成后拿回结果继续迭代，"
        "应使用 task_dispatch 而不是 handoff。移交前必须先完成手头工作并妥善保存，"
        "summary 必须让接手者无需追问即可继续：写清已完成的工作、剩余工作与未决问题。"
        "调用 handoff 的同一轮不要再调用其他工具。"
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
