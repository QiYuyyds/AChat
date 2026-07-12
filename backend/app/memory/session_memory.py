"""SessionMemory — incremental conversation summary layer.

Maintains a per-conversation summary that is incrementally updated as the
conversation progresses. When Tier 2/3 compaction triggers, it can reuse
this summary instead of re-summarising the full history.

Storage: ``context_summaries`` table with ``summary_type='session'``.
Each conversation has at most one session record (updated in-place).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import and_, asc, desc, select

from app.db.engine import get_db
from app.db.models import ContextSummary, Message
from app.utils.ids import new_context_summary_id
from app.utils.model_registry import estimate_tokens

logger = logging.getLogger(__name__)

# Thresholds (matching Claude Code's validated values)
MINIMUM_TOKENS_TO_INIT = 10_000
MINIMUM_TOKENS_BETWEEN_UPDATE = 5_000
TOOL_CALLS_BETWEEN_UPDATES = 3


@dataclass
class SessionMemoryRecord:
    """Lightweight DTO for session memory data."""

    summary: str
    covers_up_to: float | None  # created_at timestamp of last covered message


class SessionMemory:
    """Incremental session-level summary extractor.

    Lifecycle:
        1. ``set_generate_fn(fn)`` — inject the LLM generate function.
        2. ``should_extract(conversation_id)`` — check trigger conditions.
        3. ``extract(conversation_id)`` — run incremental extraction (background task).
        4. ``get(conversation_id)`` — read the current session memory.

    Degradation: when ``_generate_fn`` is None, all operations are no-ops.
    """

    def __init__(self, generate_fn: Callable[..., str] | None = None) -> None:
        self._generate_fn = generate_fn

    def set_generate_fn(self, fn: Callable[..., str]) -> None:
        self._generate_fn = fn

    async def should_extract(self, conversation_id: str) -> bool:
        """Check whether incremental extraction should trigger.

        Conditions (all must be true):
        - ``_generate_fn`` is available
        - At a natural breakpoint (no unresolved tool_use in last assistant message)
        - Total estimated tokens >= MINIMUM_TOKENS_TO_INIT
        - Either: incremental tokens >= MINIMUM_TOKENS_BETWEEN_UPDATE
          or: tool calls since last update >= TOOL_CALLS_BETWEEN_UPDATES
        """
        if self._generate_fn is None:
            return False

        existing = await self.get(conversation_id)
        covers_up_to = existing.covers_up_to if existing else None

        messages = await _load_messages_since(conversation_id, covers_up_to)
        if not messages:
            return False

        if not _at_natural_breakpoint(messages):
            return False

        total_tokens = sum(estimate_tokens(_message_text(m)) for m in messages)
        if total_tokens < MINIMUM_TOKENS_TO_INIT:
            return False

        if existing is None:
            return True

        token_since = total_tokens - estimate_tokens(existing.summary)
        tool_calls_since = _count_tool_uses(messages)

        return (
            token_since >= MINIMUM_TOKENS_BETWEEN_UPDATE
            or tool_calls_since >= TOOL_CALLS_BETWEEN_UPDATES
        )

    async def extract(self, conversation_id: str) -> None:
        """Run incremental extraction: recent messages + existing summary → new summary.

        Updates the session memory record in-place. Silently skips on failure.
        """
        if self._generate_fn is None:
            return

        try:
            existing = await self.get(conversation_id)
            covers_up_to = existing.covers_up_to if existing else None

            messages = await _load_messages_since(conversation_id, covers_up_to)
            if not messages:
                return

            recent_transcript = _render_transcript(messages)
            if not recent_transcript.strip():
                return

            prior_summary = existing.summary if existing else None

            system_prompt = (
                "你是会话摘要助手。以下是当前会话的已有摘要和新增对话内容。"
                "请将新增内容整合进已有摘要，保持摘要简洁但信息完整。"
                "务必保留：用户核心目标、关键决策、已产出产物、待跟进事项。"
                "只输出摘要正文。"
            )
            user_msg = (
                f"已有摘要：\n{prior_summary}\n\n"
                if prior_summary
                else "（暂无已有摘要，这是首次提取）\n\n"
            )
            user_msg += f"新增内容：\n{recent_transcript}\n\n输出：更新后的摘要"

            new_summary = await asyncio.to_thread(
                self._generate_fn, system_prompt, user_msg
            )
            new_summary = (new_summary or "").strip()
            if not new_summary:
                logger.warning("[session-memory] LLM returned empty summary, skipping")
                return

            last_msg = messages[-1]
            new_covers_up_to = float(last_msg.created_at)

            await self._upsert(
                conversation_id, new_summary, new_covers_up_to
            )
            logger.info(
                "[session-memory] conv=%s updated summary, covers_up_to=%.0f, msg_count=%d",
                conversation_id,
                new_covers_up_to,
                len(messages),
            )
        except Exception as e:
            logger.warning("[session-memory] extraction failed: %s", e)

    async def get(self, conversation_id: str) -> SessionMemoryRecord | None:
        """Read the current session memory for a conversation."""
        async with get_db() as db:
            result = await db.execute(
                select(ContextSummary)
                .where(
                    and_(
                        ContextSummary.conversation_id == conversation_id,
                        ContextSummary.summary_type == "session",
                    )
                )
                .order_by(desc(ContextSummary.created_at))
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return SessionMemoryRecord(
                summary=row.summary,
                covers_up_to=row.covers_up_to,
            )

    async def _upsert(
        self,
        conversation_id: str,
        summary: str,
        covers_up_to: float,
    ) -> None:
        """Insert or update the session memory record for a conversation."""
        from app.utils.clock import now_ms

        async with get_db() as db:
            result = await db.execute(
                select(ContextSummary)
                .where(
                    and_(
                        ContextSummary.conversation_id == conversation_id,
                        ContextSummary.summary_type == "session",
                    )
                )
                .order_by(desc(ContextSummary.created_at))
                .limit(1)
            )
            existing = result.scalars().first()

            if existing:
                existing.summary = summary
                existing.covers_up_to = covers_up_to
            else:
                row = ContextSummary(
                    id=new_context_summary_id(),
                    conversation_id=conversation_id,
                    summary=summary,
                    covered_until_message_id="session",
                    covered_until_created_at=int(covers_up_to),
                    source_message_count=0,
                    token_estimate=estimate_tokens(summary),
                    model_provider=None,
                    model_id=None,
                    summary_type="session",
                    covers_up_to=covers_up_to,
                    created_at=now_ms(),
                )
                db.add(row)


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _load_messages_since(
    conversation_id: str,
    since_created_at: float | None,
) -> list[Message]:
    """Load completed messages after the given timestamp, oldest first."""
    async with get_db() as db:
        where = [
            Message.conversation_id == conversation_id,
            Message.status == "complete",
            Message.hidden == False,  # noqa: E712
        ]
        if since_created_at is not None:
            where.append(Message.created_at > since_created_at)
        result = await db.execute(
            select(Message).where(and_(*where)).order_by(asc(Message.created_at))
        )
        return list(result.scalars().all())


def _at_natural_breakpoint(messages: list[Message]) -> bool:
    """Check if the last assistant message has no unresolved tool_use.

    Returns False when the last message contains a tool_use part without a
    matching tool_result — this means we're mid-tool-chain and should defer.
    """
    if not messages:
        return True

    last = messages[-1]
    parts = last.parts_list

    has_tool_use = any(p.get("type") == "tool_use" for p in parts)
    if not has_tool_use:
        return True

    # Collect all call_ids from tool_use parts
    tool_use_ids = {
        p.get("callId", "")
        for p in parts
        if p.get("type") == "tool_use"
    }
    # Check if all have matching tool_result in the same message or later
    # (in practice, tool_result comes as a separate message or in the same message)
    result_ids = set()
    for msg in messages:
        for p in msg.parts_list:
            if p.get("type") == "tool_result":
                result_ids.add(p.get("callId", ""))

    unresolved = tool_use_ids - result_ids
    return len(unresolved) == 0


def _count_tool_uses(messages: list[Message]) -> int:
    """Count tool_use parts across the given messages."""
    count = 0
    for msg in messages:
        for p in msg.parts_list:
            if p.get("type") == "tool_use":
                count += 1
    return count


def _render_transcript(messages: list[Message]) -> str:
    """Render messages as a plain-text transcript for the summariser."""
    lines: list[str] = []
    for msg in messages:
        text = _message_text(msg)
        if not text:
            continue
        if msg.role == "user":
            who = "用户"
        elif msg.role == "system":
            who = "系统"
        else:
            who = "Agent"
        lines.append(f"{who}：{text}")
    return "\n".join(lines)


def _message_text(msg: Message) -> str:
    """Extract plain text from a message's parts."""
    texts = [
        p.get("content", "")
        for p in msg.parts_list
        if p.get("type") == "text" and p.get("content")
    ]
    return "\n".join(texts).strip()
