"""ask_peer tool — sub-agent horizontal communication within a DAG.

Two paths:
  - Path A (peerTaskId present): look up the target AgentSession, rebuild its
    conversation history (including hidden messages), create a mini-run with
    override_messages + override_system_prompt, await completion, and return
    the answer.
  - Path B (no peerTaskId): store the question in the parent Agent's mailbox
    for asynchronous review after the DAG finishes.

Anti-loop: per-peer ask_count limited to 3; mini-run dispatch_depth is
caller_depth + 1, capped by MAX_DISPATCH_DEPTH.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import AgentRun
from app.schemas.events import DispatchPeerEvent
from app.services.event_bus import event_bus
from app.tools.base import ToolContext, ToolDef, ToolResult, ok
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

_MAX_ASK_COUNT = 3


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["question"],
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "向目标 Agent 提出的问题。需自包含——目标 Agent 只看到"
                "问题文本，看不到你的对话上下文。"
            ),
        },
        "peerTaskId": {
            "type": "string",
            "description": (
                "目标节点的 task ID（同 DAG 内已完成的节点）。"
                "省略时向主 Agent 异步留言，不期望收到回复。"
            ),
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    if not isinstance(args, dict):
        return ok({"status": "error", "error": "invalid arguments"})

    question = args.get("question", "").strip()
    if not question:
        return ok({"status": "error", "error": "question is required"})

    peer_task_id = args.get("peerTaskId")

    # Path B: no peerTaskId → asynchronous mailbox message to parent Agent
    if not peer_task_id:
        return _handle_mailbox(question, ctx)

    # Path A: peerTaskId present → synchronous mini-run Q&A
    return await _handle_mini_run(question, peer_task_id, ctx)


def _handle_mailbox(question: str, ctx: ToolContext) -> ToolResult:
    from app.services.agent_session_registry import agent_session_registry

    parent_run_id = ctx.parent_run_id or ctx.run_id
    agent_session_registry.add_to_mailbox(parent_run_id, question)
    logger.info(
        "[ask_peer] mailbox message added: parent_run=%s question_len=%d",
        parent_run_id,
        len(question),
    )
    event_bus.publish(
        DispatchPeerEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=parent_run_id,
            from_task_id=ctx.dag_task_id or "",
            to_task_id=None,
            question=question,
            status="mailed",
        ),
        user_id=ctx.user_id,
    )
    return ok({
        "status": "pending",
        "note": "反馈已提交给主 Agent，主 Agent 将在 DAG 结束后查看",
    })


async def _handle_mini_run(
    question: str, peer_task_id: str, ctx: ToolContext
) -> ToolResult:
    from app.services.agent_loop import MAX_DISPATCH_DEPTH, _extract_run_final_text
    from app.services.agent_runner import RunArgs, run_with_args
    from app.services.agent_session_registry import agent_session_registry
    from app.services.conversation_context import build_run_messages
    from app.tools.report_result import _report_result_cache

    parent_run_id = ctx.parent_run_id or ctx.run_id

    session = agent_session_registry.get(peer_task_id)
    if session is None or session.status == "expired":
        event_bus.publish(
            DispatchPeerEvent(
                conversation_id=ctx.conversation_id,
                timestamp=now_ms(),
                parent_run_id=parent_run_id,
                from_task_id=ctx.dag_task_id or "",
                to_task_id=peer_task_id,
                question=question,
                status="unavailable",
            ),
            user_id=ctx.user_id,
        )
        return ok({"status": "unavailable"})

    # Anti-loop: check ask_count limit
    if session.ask_count >= _MAX_ASK_COUNT:
        event_bus.publish(
            DispatchPeerEvent(
                conversation_id=ctx.conversation_id,
                timestamp=now_ms(),
                parent_run_id=parent_run_id,
                from_task_id=ctx.dag_task_id or "",
                to_task_id=peer_task_id,
                question=question,
                status="limit_reached",
                ask_count=session.ask_count,
            ),
            user_id=ctx.user_id,
        )
        return ok({"status": "limit_reached"})

    # Depth check: mini-run depth must not exceed MAX_DISPATCH_DEPTH
    mini_depth = session.dispatch_depth + 1
    if mini_depth >= MAX_DISPATCH_DEPTH:
        event_bus.publish(
            DispatchPeerEvent(
                conversation_id=ctx.conversation_id,
                timestamp=now_ms(),
                parent_run_id=parent_run_id,
                from_task_id=ctx.dag_task_id or "",
                to_task_id=peer_task_id,
                question=question,
                status="limit_reached",
            ),
            user_id=ctx.user_id,
        )
        return ok({
            "status": "limit_reached",
            "note": f"mini-run depth {mini_depth} would exceed MAX_DISPATCH_DEPTH {MAX_DISPATCH_DEPTH}",
        })

    # Increment ask_count before creating mini-run
    session.ask_count += 1

    logger.info(
        "[ask_peer] creating mini-run: peer_task=%s ask_count=%d depth=%d",
        peer_task_id,
        session.ask_count,
        mini_depth,
    )

    event_bus.publish(
        DispatchPeerEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=parent_run_id,
            from_task_id=ctx.dag_task_id or "",
            to_task_id=peer_task_id,
            question=question,
            status="asking",
            ask_count=session.ask_count,
        ),
        user_id=ctx.user_id,
    )

    # Rebuild target agent's conversation history (including hidden messages)
    rebuilt_messages = await build_run_messages(
        run_id=session.run_id,
        conversation_id=session.conversation_id,
        agent_id=session.agent_id,
        include_hidden=True,
    )

    # Build override_messages: system prompt + rebuilt history + question
    override_messages: list[dict] = []
    if session.system_prompt:
        override_messages.append({
            "role": "system",
            "content": session.system_prompt,
        })
    override_messages.extend(rebuilt_messages)
    override_messages.append({"role": "user", "content": question})

    # Get trigger_message_id from the caller's run (like dispatch_plan does)
    async with get_local_db() as db:
        parent_run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == ctx.run_id)
            )
        ).scalar_one_or_none()
        trigger_message_id = (
            parent_run.trigger_message_id
            if parent_run
            else ctx.conversation_id
        )

    mini_args = RunArgs(
        agent_id=session.agent_id,
        conversation_id=session.conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=ctx.run_id,
        override_prompt=question,
        override_system_prompt=session.system_prompt,
        override_messages=override_messages,
        override_tool_names=["report_result"],
        dispatch_visibility="hidden",
        dispatch_depth=mini_depth,
        user_id=ctx.user_id,
    )

    child_run_id, child_task, _child_cancel = run_with_args(mini_args)

    try:
        child_result = await child_task
    except Exception as exc:
        logger.exception("[ask_peer] mini-run failed: %s", exc)
        return ok({"status": "error", "error": f"mini-run failed: {exc}"})

    # Extract answer text: prefer report_result cache, fallback to final text
    payload = _report_result_cache.pop(child_run_id, None)
    if payload is not None:
        answer = payload.summary
    else:
        answer = await _extract_run_final_text(
            child_run_id,
            session.conversation_id,
            child_result.output_message_ids,
        )

    if not answer:
        answer = "(mini-run produced no text output)"

    logger.info(
        "[ask_peer] mini-run completed: peer_task=%s answer_len=%d",
        peer_task_id,
        len(answer),
    )

    event_bus.publish(
        DispatchPeerEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=parent_run_id,
            from_task_id=ctx.dag_task_id or "",
            to_task_id=peer_task_id,
            question=question,
            status="answered",
            answer=answer,
            ask_count=session.ask_count,
        ),
        user_id=ctx.user_id,
    )

    return ok({"status": "answered", "answer": answer})


ask_peer_tool = ToolDef(
    name="ask_peer",
    description=(
        "向同 DAG 内的已完成节点提问，或向主 Agent 异步留言。"
        "当上游产出缺少关键信息且无法自行推断时使用。\n"
        "- peerTaskId：目标节点 ID（省略时向主 Agent 异步留言，不期望收到回复）\n"
        "- question：提问内容，需自包含——目标 Agent 只看到问题文本\n"
        "**注意**：仅当确实需要上游信息才能继续工作时使用。优先自行推断或用工具探索。"
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
