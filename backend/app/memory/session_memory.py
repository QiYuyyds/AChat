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

from app.db.engine import get_local_db
from app.db.models import ContextSummary, Message
from app.memory.session_note import SessionNote, _enforce_section_limits
from app.services.transcript_renderer import (
    estimate_full_message_tokens,
    render_tool_aware_transcript,
)
from app.utils.ids import new_context_summary_id
from app.utils.model_registry import estimate_tokens

logger = logging.getLogger(__name__)

# Thresholds (lowered for short-conversation coverage)
MINIMUM_TOKENS_TO_INIT = 3_000
MINIMUM_TOKENS_BETWEEN_UPDATE = 3_000
TOOL_CALLS_BETWEEN_UPDATES = 2

_SESSION_NOTE_SYSTEM_PROMPT = """你是会话笔记助手。请从对话内容中提取结构化信息，更新已有笔记。

你将看到:
1. 已有笔记 (YAML 格式)
2. 新增对话内容 (自上次提取以来的消息)

请输出**合并后的完整笔记** (YAML 格式)。

## 分 section 处理规则

不同 section 有不同的更新语义:

### title (覆盖)
- 如果新对话的主题更清晰，更新标题
- 否则保留原标题

### current_state (覆盖)
- 始终用最新状态覆盖
- 旧状态信息如果有价值，移入 key_decisions 或 architecture_understanding

### key_decisions (追加 + 去重 + 标记解决)
- 新决策追加到列表末尾，带时间戳 [HH:MM]
- 如果新对话推翻了旧决策，在旧决策后标注 [已更新]，新决策正常追加
- 完全相同的决策不重复追加
- 超过 20 条时，合并同类项 (如多个 "改了 X 文件" → "改了 6 个文件")

### files_touched (追加 + 去重)
- 新文件追加到列表末尾
- 同一文件多次操作只保留一条 (用最新状态: 已读/已改)
- 超过 30 条时，只保留最近 30 条

### commands_run (追加 + 去重)
- 重要命令追加 (跳过简单 cd/ls)
- 相同命令不重复
- 超过 20 条时，只保留最近 20 条

### artifacts_produced (追加 + 去重)
- 新产物追加
- 超过 10 条时，只保留最近 10 条

### blockers (覆盖 + 标记解决)
- 已解决的 blocker 标注 [已解决]，不移除 (保留历史)
- 新 blocker 追加
- 超过 10 条时，移除已解决超过 5 条的旧 blocker

### open_questions (覆盖 + 标记解决)
- 已回答的问题标注 [已回答]，不移除
- 新问题追加
- 超过 10 条时，移除已回答超过 5 条的旧问题

### next_steps (覆盖)
- 始终用最新的 next steps 覆盖
- 已完成的 step 不需要保留

### architecture_understanding (覆盖)
- 用最新理解覆盖
- 如果新增内容只是补充细节，合并到已有理解中

## 输出格式

```yaml
title: 简短会话标题
current_state: 当前正在做什么（1-2 句）
key_decisions:
  - "[14:32] 决策内容"
  - "[15:07] [已更新] 旧决策"
files_touched:
  - "path/to/file.py (已读/已改, N 行)"
commands_run:
  - "command (结果摘要)"
artifacts_produced:
  - "path/to/artifact (类型)"
blockers:
  - "阻塞项 [已解决]"
open_questions:
  - "待解决问题 [已回答]"
next_steps:
  - "下一步"
architecture_understanding: |
  架构理解与代码结构发现
```

如果某个 section 没有新增内容，保留已有内容不变。
"""


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

        total_tokens = estimate_full_message_tokens(messages)
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

    async def should_extract_short(self, conversation_id: str) -> bool:
        """Check whether short-conversation first extraction should trigger.

        Conditions (all must be true):
        - ``_generate_fn`` is available
        - No existing session Note (first extraction only)
        - At a natural breakpoint
        - >= 2 tool turns (no token threshold)
        """
        if self._generate_fn is None:
            return False

        existing = await self.get(conversation_id)
        if existing is not None:
            return False

        messages = await _load_messages_since(conversation_id, None)
        if not messages:
            return False

        if not _at_natural_breakpoint(messages):
            return False

        tool_call_count = _count_tool_uses(messages)
        return tool_call_count >= TOOL_CALLS_BETWEEN_UPDATES

    async def extract(self, conversation_id: str) -> None:
        """Run incremental extraction using Plan C (LLM full output + per-section merge).

        Flow:
        1. Load existing note (YAML) + new messages since covers_up_to
        2. Build Plan C prompt: existing YAML + new transcript
        3. LLM outputs merged complete YAML
        4. Parse via SessionNote.from_yaml; on failure keep existing note
        5. Post-process: _enforce_section_limits
        6. Upsert

        Silently skips on failure. Existing note is preserved on YAML parse failure.
        """
        if self._generate_fn is None:
            return

        try:
            existing = await self.get(conversation_id)
            covers_up_to = existing.covers_up_to if existing else None

            messages = await _load_messages_since(conversation_id, covers_up_to)
            if not messages:
                return

            recent_transcript = render_tool_aware_transcript(messages)
            if not recent_transcript.strip():
                return

            prior_summary = existing.summary if existing else None

            system_prompt = _SESSION_NOTE_SYSTEM_PROMPT
            user_msg = (
                f"# 已有笔记\n\n{prior_summary}\n\n"
                if prior_summary
                else "# 已有笔记\n\n# (new note)\n\n"
            )
            user_msg += f"# 新增对话\n\n{recent_transcript}\n\n"
            user_msg += "请输出合并后的完整笔记 (YAML 格式)。"

            raw_output = await asyncio.to_thread(
                self._generate_fn, system_prompt, user_msg
            )
            raw_output = (raw_output or "").strip()
            if not raw_output:
                logger.warning("[session-memory] LLM returned empty output, skipping")
                return

            new_note = SessionNote.from_yaml(raw_output)
            if new_note is None:
                logger.warning(
                    "[session-memory] LLM output is not valid YAML, keeping existing note"
                )
                return

            last_msg = messages[-1]
            new_note.covers_up_to = float(last_msg.created_at)

            new_note = _enforce_section_limits(new_note)
            new_summary = new_note.to_yaml()

            await self._upsert(
                conversation_id, new_summary, new_note.covers_up_to
            )
            logger.info(
                "[session-memory] conv=%s updated session note, covers_up_to=%.0f, msg_count=%d",
                conversation_id,
                new_note.covers_up_to,
                len(messages),
            )
        except Exception as e:
            logger.warning("[session-memory] extraction failed: %s", e)

    async def get(self, conversation_id: str) -> SessionMemoryRecord | None:
        """Read the current session memory for a conversation."""
        async with get_local_db() as db:
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

        async with get_local_db() as db:
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
    async with get_local_db() as db:
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
