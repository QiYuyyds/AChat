"""Context-compaction service.

Port of src/server/context-compaction-service.ts.

Read/format helpers (used by agent-runner's hot path, no LLM):
  - get_latest_context_summary
  - render_conversation_summary_block
  - prefix_prompt_with_context_summary

Full compaction flow (LLM-backed, triggered by the explicit /compact action):
  - compact_conversation — load uncompacted history → summarise via LLM →
    persist a ContextSummary + a system message → return both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import and_, asc, desc, select

from app.db.engine import get_db
from app.db.models import Agent, ContextSummary, Conversation, Message
from app.schemas.events import MessageAddedEvent, MessageRecord
from app.schemas.messages import ContextSummaryRecord
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_context_summary_id, new_message_id
from app.utils.model_registry import estimate_tokens

logger = logging.getLogger(__name__)

# Keep the most recent N messages uncompacted; summarise everything older.
KEEP_RECENT_MESSAGES = 6
# Refuse to compact when there aren't enough older messages to be worth it.
MIN_COMPACTABLE = 2
# Token floor on the compactable slice: gate on size, not count, so a short
# conversation isn't summarised for no gain while a few huge messages still can.
MIN_COMPACT_TOKENS = 800

# Friendly notices for benign "nothing to compact" outcomes (not errors).
_TOO_SHORT_NOTICE = "当前对话还太短，暂时不需要压缩上下文。"
_TOO_LITTLE_NOTICE = "待压缩的内容太少，压缩收益不明显，暂不压缩。"
_NO_MODEL_NOTICE = "当前会话没有可用于生成摘要的模型 agent，无法压缩上下文。"


class CompactionSkipped(Exception):
    """Benign 'nothing to compact' outcome — surfaced as a friendly notice, not an error."""

    def __init__(self, reason: str, message: MessageRecord) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message = message


def _broadcast_ephemeral_notice(conversation_id: str, content: str) -> MessageRecord:
    """Broadcast a role=system notice WITHOUT persisting it (transient by design)."""
    record = MessageRecord(
        id=new_message_id(),
        conversation_id=conversation_id,
        role="system",
        agent_id=None,
        parts=[{"type": "text", "content": content}],
        status="complete",
        parent_message_id=None,
        mentioned_agent_ids=[],
        run_id=None,
        usage=None,
        created_at=now_ms(),
    )
    event_bus.publish(
        MessageAddedEvent(
            conversation_id=conversation_id, timestamp=record.created_at, message=record
        )
    )
    return record


def _skip(conversation_id: str, reason: str, content: str) -> CompactionSkipped:
    return CompactionSkipped(reason, _broadcast_ephemeral_notice(conversation_id, content))


async def get_latest_context_summary(conversation_id: str) -> ContextSummary | None:
    """Most recent stored summary for a conversation, or None."""
    async with get_db() as db:
        result = await db.execute(
            select(ContextSummary)
            .where(ContextSummary.conversation_id == conversation_id)
            .order_by(desc(ContextSummary.created_at))
            .limit(1)
        )
        return result.scalars().first()


def render_conversation_summary_block(summary: ContextSummary) -> str:
    """Wrap a summary in the XML-ish tag the runner injects into prompts."""
    return "\n".join(
        [
            f'<conversation_summary covered_until_message_id="'
            f'{_escape_attr(summary.covered_until_message_id)}">',
            summary.summary,
            "</conversation_summary>",
        ]
    )


async def prefix_prompt_with_context_summary(conversation_id: str, prompt: str) -> str:
    """Prepend the latest summary block to a prompt (no-op when none exists)."""
    latest = await get_latest_context_summary(conversation_id)
    if latest is None:
        return prompt
    return "\n".join([render_conversation_summary_block(latest), "", prompt])


def _escape_attr(value: str) -> str:
    # XML attribute escaping, matching the TS escapeAttr (&, ", < only).
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _fmt_k(tokens: int) -> str:
    """Format a token count like the frontend badge: 10123 -> '10.1k'."""
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


# ─── full compaction flow (LLM-backed) ──────────────────────────────────────


@dataclass
class CompactResult:
    """Result of a /compact action: the new summary + the system message."""

    summary: ContextSummaryRecord
    message: MessageRecord
    # Estimated next-turn context (prompt tokens) before vs. after compaction —
    # the frontend uses ctx_after to optimistically refresh its "当前 ctx" badge.
    ctx_before: int
    ctx_after: int


async def compact_conversation(conversation_id: str) -> CompactResult:
    """Summarise older history into a ContextSummary and insert a system message.

    Raises ``CompactionSkipped`` (surfaced as HTTP 200 + a friendly ephemeral
    notice) for benign no-op cases: nothing worth compacting, or no model-backed
    agent. Raises ValueError (HTTP 400) for genuine failures: conversation
    missing, or the summariser returning empty.
    """
    # a) conversation exists?
    async with get_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            raise ValueError("会话不存在")
        agent_ids = conv.agent_ids_list

    # b) incremental cut-off: only compact messages after the last summary
    latest = await get_latest_context_summary(conversation_id)
    since_created_at = latest.covered_until_created_at if latest else None

    # c) load completed messages after the cut-off, oldest first
    async with get_db() as db:
        where = [
            Message.conversation_id == conversation_id,
            Message.status == "complete",
        ]
        if since_created_at is not None:
            where.append(Message.created_at > since_created_at)
        rows = (
            (
                await db.execute(
                    select(Message).where(and_(*where)).order_by(asc(Message.created_at))
                )
            )
            .scalars()
            .all()
        )

    # d) keep the most recent N; compact the rest
    if len(rows) <= KEEP_RECENT_MESSAGES:
        raise _skip(conversation_id, "conversation_too_short", _TOO_SHORT_NOTICE)
    to_compact = rows[:-KEEP_RECENT_MESSAGES]
    if len(to_compact) < MIN_COMPACTABLE:
        raise _skip(conversation_id, "conversation_too_short", _TOO_SHORT_NOTICE)

    # e) pick a summariser model: first Custom agent with model config
    try:
        model_provider, model_id, api_key, api_base_url = await _pick_summary_model(agent_ids)
    except ValueError:
        raise _skip(conversation_id, "no_summariser_model", _NO_MODEL_NOTICE) from None

    # f) render the messages to compact into a transcript
    agent_names = await _load_agent_names(agent_ids)
    transcript = _render_transcript(to_compact, agent_names)

    # size gate: only compact when the slice is big enough to be worth an LLM call
    if estimate_tokens(transcript) < MIN_COMPACT_TOKENS:
        raise _skip(conversation_id, "compactable_too_small", _TOO_LITTLE_NOTICE)

    prior = latest.summary if latest else None
    kept = rows[-KEEP_RECENT_MESSAGES:]

    # ctx-before: the full uncompacted tail that the next turn would otherwise
    # carry (prior summary block, if any, + every message after the cut-off).
    ctx_before = estimate_tokens(transcript) + sum(
        estimate_tokens(_message_text(m)) for m in kept
    )
    if prior:
        ctx_before += estimate_tokens(prior)

    # g) call the LLM
    summary_text = await _summarise(
        transcript, prior, model_provider, model_id, api_key, api_base_url
    )
    if not summary_text:
        raise ValueError("摘要生成失败：模型返回为空")

    # ctx-after: the new summary block + only the kept recent messages.
    ctx_after = estimate_tokens(summary_text) + sum(
        estimate_tokens(_message_text(m)) for m in kept
    )

    # h) persist ContextSummary
    last = to_compact[-1]
    summary_id = new_context_summary_id()
    created_at = now_ms()
    token_estimate = estimate_tokens(transcript)
    async with get_db() as db:
        row = ContextSummary(
            id=summary_id,
            conversation_id=conversation_id,
            summary=summary_text,
            covered_until_message_id=last.id,
            covered_until_created_at=last.created_at,
            source_message_count=len(to_compact),
            token_estimate=token_estimate,
            model_provider=model_provider,
            model_id=model_id,
            created_at=created_at,
        )
        db.add(row)

    summary_record = ContextSummaryRecord(
        id=summary_id,
        conversation_id=conversation_id,
        summary=summary_text,
        covered_until_message_id=last.id,
        covered_until_created_at=last.created_at,
        source_message_count=len(to_compact),
        token_estimate=token_estimate,
        model_provider=model_provider,
        model_id=model_id,
        created_at=created_at,
    )

    # i) insert a system message announcing the compaction
    sys_msg_id = new_message_id()
    sys_now = now_ms()
    saved = max(0, ctx_before - ctx_after)
    if saved >= 500:
        content = (
            f"已将 {len(to_compact)} 条历史消息压缩为上下文摘要。"
            f"下次对话的上下文预计从 ~{_fmt_k(ctx_before)} 降到 "
            f"~{_fmt_k(ctx_after)}（约省 {_fmt_k(saved)} tokens）。"
        )
    else:
        content = f"已将 {len(to_compact)} 条历史消息压缩为上下文摘要。"
    sys_parts = [{"type": "text", "content": content}]
    async with get_db() as db:
        sys_msg = Message(
            id=sys_msg_id,
            conversation_id=conversation_id,
            role="system",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            run_id=None,
            created_at=sys_now,
        )
        sys_msg.parts_list = sys_parts
        sys_msg.mentioned_agent_ids_list = []
        db.add(sys_msg)

    sys_record = MessageRecord(
        id=sys_msg_id,
        conversation_id=conversation_id,
        role="system",
        agent_id=None,
        parts=sys_parts,
        status="complete",
        parent_message_id=None,
        mentioned_agent_ids=[],
        run_id=None,
        usage=None,
        created_at=sys_now,
    )
    event_bus.publish(
        MessageAddedEvent(
            conversation_id=conversation_id,
            timestamp=sys_now,
            message=sys_record,
        )
    )

    logger.info(
        "[compact] conversation=%s compacted=%d summary_id=%s model=%s",
        conversation_id,
        len(to_compact),
        summary_id,
        model_id,
    )
    return CompactResult(
        summary=summary_record,
        message=sys_record,
        ctx_before=ctx_before,
        ctx_after=ctx_after,
    )


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _pick_summary_model(
    agent_ids: list[str],
) -> tuple[str, str, str | None, str | None]:
    """First Custom agent (adapter_name='custom') with a full model config.

    Returns (model_provider, model_id, api_key, api_base_url).
    Raises ValueError when no model-backed agent exists (e.g. CLI-only chat).
    """
    if not agent_ids:
        raise ValueError("当前会话没有配置模型的 agent，无法生成摘要")
    async with get_db() as db:
        agents = (
            (await db.execute(select(Agent).where(Agent.id.in_(agent_ids))))
            .scalars()
            .all()
        )
    by_id = {a.id: a for a in agents}
    # preserve conversation agent order
    for aid in agent_ids:
        agent = by_id.get(aid)
        if (
            agent is not None
            and agent.adapter_name == "custom"
            and agent.model_provider
            and agent.model_id
        ):
            return (agent.model_provider, agent.model_id, agent.api_key, agent.api_base_url)
    raise ValueError("当前会话没有配置模型的 agent，无法生成摘要")


async def _load_agent_names(agent_ids: list[str]) -> dict[str, str]:
    if not agent_ids:
        return {}
    async with get_db() as db:
        agents = (
            (await db.execute(select(Agent).where(Agent.id.in_(agent_ids))))
            .scalars()
            .all()
        )
    return {a.id: a.name for a in agents}


def _render_transcript(messages: list[Message], agent_names: dict[str, str]) -> str:
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
            who = agent_names.get(msg.agent_id or "", msg.agent_id or "Agent")
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


async def _summarise(
    transcript: str,
    prior_summary: str | None,
    model_provider: str,
    model_id: str,
    api_key: str | None,
    api_base_url: str | None,
) -> str:
    """Call the LLM to produce a compaction summary. Raises on API failure."""
    from openai import AsyncOpenAI

    from app.adapters.custom_provider_client import resolve_custom_provider_client_config

    prior_block = (
        f"以下是更早对话的已有摘要，请在此基础上继续整合：\n{prior_summary}\n\n"
        if prior_summary
        else ""
    )
    prompt = (
        "你在压缩一段多 Agent 群聊的历史，为后续对话保留必要上下文。\n"
        f"{prior_block}"
        "请把下面的对话压缩成一份简洁但信息完整的摘要，务必保留：\n"
        "- 用户的核心目标和明确偏好\n"
        "- 关键决策与结论\n"
        "- 已产出的产物（含 artifact/deployment id）\n"
        "- 尚未完成或待跟进的事项\n"
        "只输出摘要正文，不要加前缀、标题或引号。\n\n"
        f"对话内容：\n{transcript}"
    )

    config = resolve_custom_provider_client_config(
        model_provider, override_key=api_key, api_base_url=api_base_url
    )
    client = AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, max_retries=1
    )
    response = await client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.3,
    )
    raw = response.choices[0].message.content
    return raw.strip() if raw else ""
