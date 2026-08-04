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

from app.db.engine import get_local_db
from app.db.models import Agent, AgentRun, Attachment, ContextSummary, Conversation, Message, ModelProfile
from app.schemas.events import MessageAddedEvent, MessageRecord
from app.schemas.messages import ContextSummaryRecord
from app.services.event_bus import event_bus
from app.services.transcript_renderer import (
    estimate_full_message_tokens,
    render_tool_aware_transcript,
)
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
    """Benign 'nothing to compact' outcome — surfaced as a friendly notice, not an error.

    In non-silent mode ``message`` is a broadcast ephemeral notice; in silent mode
    ``message`` is ``None`` (no broadcast).
    """

    def __init__(self, reason: str, message: MessageRecord | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message = message


def _broadcast_ephemeral_notice(
    conversation_id: str, content: str, user_id: str | None = None
) -> MessageRecord:
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
        ),
        user_id=user_id,
    )
    return record


def _skip(
    conversation_id: str, reason: str, content: str, user_id: str | None = None
) -> CompactionSkipped:
    return CompactionSkipped(reason, _broadcast_ephemeral_notice(conversation_id, content, user_id))


def _skip_silent(reason: str) -> CompactionSkipped:
    """Silent skip: raise CompactionSkipped without broadcasting an ephemeral notice."""
    return CompactionSkipped(reason, None)


async def get_latest_context_summary(conversation_id: str) -> ContextSummary | None:
    """Most recent compaction summary for a conversation, or None.

    Only returns ``summary_type='compaction'`` records — Session Memory
    records (``summary_type='session'``) are excluded because they have
    a separate lifecycle and coverage tracking.
    """
    async with get_local_db() as db:
        result = await db.execute(
            select(ContextSummary)
            .where(
                and_(
                    ContextSummary.conversation_id == conversation_id,
                    ContextSummary.summary_type == "compaction",
                )
            )
            .order_by(desc(ContextSummary.created_at))
            .limit(1)
        )
        return result.scalars().first()


async def count_uncompacted_messages(conversation_id: str) -> int:
    """Count completed messages after the last summary's coverage window.

    Informational only — no longer used as an auto-compact trigger.
    When no prior summary exists, counts all complete messages in the
    conversation.
    """
    latest = await get_latest_context_summary(conversation_id)
    since_created_at = latest.covered_until_created_at if latest else None
    async with get_local_db() as db:
        where = [
            Message.conversation_id == conversation_id,
            Message.status == "complete",
        ]
        if since_created_at is not None:
            where.append(Message.created_at > since_created_at)
        result = await db.execute(
            select(Message.id).where(and_(*where))
        )
        return len(result.scalars().all())


async def estimate_uncompacted_tokens(conversation_id: str) -> int:
    """Estimate total token count of uncompacted messages.

    Loads the same uncompacted message slice as ``count_uncompacted_messages``
    and sums coarse token estimates of ALL message parts (text, tool_use,
    tool_result, thinking). Used by the auto-compaction hook's token-based
    trigger.
    """
    latest = await get_latest_context_summary(conversation_id)
    since_created_at = latest.covered_until_created_at if latest else None
    async with get_local_db() as db:
        where = [
            Message.conversation_id == conversation_id,
            Message.status == "complete",
        ]
        if since_created_at is not None:
            where.append(Message.created_at > since_created_at)
        rows = (
            (await db.execute(select(Message).where(and_(*where))))
            .scalars()
            .all()
        )
    return estimate_full_message_tokens(rows)


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
    """Result of a /compact action: the new summary + the system message.

    ``message`` is ``None`` in silent mode (no system announcement).
    """

    summary: ContextSummaryRecord
    message: MessageRecord | None
    # Estimated next-turn context (prompt tokens) before vs. after compaction —
    # the frontend uses ctx_after to optimistically refresh its "当前 ctx" badge.
    ctx_before: int
    ctx_after: int


async def compact_conversation(
    conversation_id: str, *, silent: bool = False
) -> CompactResult:
    """Summarise older history into a ContextSummary and insert a system message.

    When ``silent=True`` (auto-compaction path): skips the system-message
    insertion and ``MessageAddedEvent`` broadcast (step i), and does not
    broadcast ephemeral notices on skip. ``CompactResult.message`` will be
    ``None`` in silent mode.

    Raises ``CompactionSkipped`` (surfaced as HTTP 200 + a friendly ephemeral
    notice in non-silent mode) for benign no-op cases: nothing worth compacting,
    or no model-backed agent. Raises ValueError (HTTP 400) for genuine failures:
    conversation missing, or the summariser returning empty.
    """
    # a) conversation exists?
    async with get_local_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            raise ValueError("会话不存在")
        agent_ids = conv.agent_ids_list
        conv_user_id = None

    # b) incremental cut-off: only compact messages after the last summary
    latest = await get_latest_context_summary(conversation_id)
    since_created_at = latest.covered_until_created_at if latest else None

    # c) load completed messages after the cut-off, oldest first
    async with get_local_db() as db:
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

    # d) breakpoint protection: find safe cut point (Phase 5)
    cut = _find_safe_cut_point(rows)
    if cut <= 0:
        raise (
            _skip_silent("conversation_too_short")
            if silent
            else _skip(conversation_id, "conversation_too_short", _TOO_SHORT_NOTICE, user_id=conv_user_id)
        )

    to_compact = rows[:cut]
    kept = rows[cut:]
    if len(to_compact) < MIN_COMPACTABLE:
        raise (
            _skip_silent("conversation_too_short")
            if silent
            else _skip(conversation_id, "conversation_too_short", _TOO_SHORT_NOTICE, user_id=conv_user_id)
        )

    agent_names = await _load_agent_names(agent_ids)
    prior = latest.summary if latest else None
    full_transcript = render_tool_aware_transcript(to_compact, agent_names)

    # ctx-before: the full uncompacted tail that the next turn would otherwise
    # carry (prior summary block, if any, + every message after the cut-off).
    ctx_before = estimate_tokens(full_transcript) + estimate_full_message_tokens(kept)
    if prior:
        ctx_before += estimate_tokens(prior)

    # e) three-way branching on Session Memory coverage (Phase 4)
    session_mem = await get_session_memory(conversation_id)
    model_provider: str | None = None
    model_id: str | None = None

    if (
        session_mem
        and session_mem.covers_up_to is not None
        and session_mem.covers_up_to >= float(to_compact[-1].created_at)
    ):
        # Case 1: Full coverage — use session memory directly, zero LLM call
        summary_text = session_mem.summary
        logger.info(
            "[compact] conv=%s full session memory coverage, skipping LLM",
            conversation_id,
        )

    elif session_mem and session_mem.covers_up_to is not None:
        # Case 2: Partial coverage — gap messages + session summary → LLM
        gap_messages = [
            m for m in to_compact
            if float(m.created_at) > session_mem.covers_up_to
        ]
        gap_transcript = render_tool_aware_transcript(gap_messages, agent_names)

        if estimate_tokens(full_transcript) < MIN_COMPACT_TOKENS:
            raise (
                _skip_silent("compactable_too_small")
                if silent
                else _skip(conversation_id, "compactable_too_small", _TOO_LITTLE_NOTICE, user_id=conv_user_id)
            )

        try:
            model_provider, model_id, api_key, api_base_url, summariser_agent_id = (
                await _pick_summary_model(agent_ids)
            )
        except ValueError:
            raise (
                _skip_silent("no_summariser_model")
                if silent
                else _skip(conversation_id, "no_summariser_model", _NO_MODEL_NOTICE, user_id=conv_user_id)
            ) from None

        parent_system_prompt = await _get_agent_system_prompt(summariser_agent_id)
        summary_text = await _summarise(
            gap_transcript, session_mem.summary,
            model_provider, model_id, api_key, api_base_url,
            parent_system_prompt=parent_system_prompt,
        )
        logger.info(
            "[compact] conv=%s partial session memory coverage, gap=%d msgs",
            conversation_id, len(gap_messages),
        )

    else:
        # Case 3: No session memory — original path (full transcript → LLM)
        if estimate_tokens(full_transcript) < MIN_COMPACT_TOKENS:
            raise (
                _skip_silent("compactable_too_small")
                if silent
                else _skip(conversation_id, "compactable_too_small", _TOO_LITTLE_NOTICE, user_id=conv_user_id)
            )

        try:
            model_provider, model_id, api_key, api_base_url, summariser_agent_id = (
                await _pick_summary_model(agent_ids)
            )
        except ValueError:
            raise (
                _skip_silent("no_summariser_model")
                if silent
                else _skip(conversation_id, "no_summariser_model", _NO_MODEL_NOTICE, user_id=conv_user_id)
            ) from None

        parent_system_prompt = await _get_agent_system_prompt(summariser_agent_id)
        summary_text = await _summarise(
            full_transcript, prior,
            model_provider, model_id, api_key, api_base_url,
            parent_system_prompt=parent_system_prompt,
        )

    if not summary_text:
        raise ValueError("摘要生成失败：模型返回为空")

    # ctx-after: the new summary block + only the kept recent messages.
    ctx_after = estimate_tokens(summary_text) + estimate_full_message_tokens(kept)

    # h) persist ContextSummary
    last = to_compact[-1]
    summary_id = new_context_summary_id()
    created_at = now_ms()
    token_estimate = estimate_tokens(full_transcript)
    async with get_local_db() as db:
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
            summary_type="compaction",
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

    # i) insert a system message announcing the compaction (skipped in silent mode)
    # Phase 6: append capability context restoration after the summary notice
    sys_record: MessageRecord | None = None
    if not silent:
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

        # Capability restoration: inject tool/attachment/dispatch context
        cap_block = await _build_capability_context(conversation_id, agent_ids)
        if cap_block:
            content = f"{content}\n\n{cap_block}"

        sys_parts = [{"type": "text", "content": content}]
        async with get_local_db() as db:
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
            ),
            user_id=conv_user_id,
        )

    logger.info(
        "[compact] conversation=%s compacted=%d summary_id=%s model=%s silent=%s",
        conversation_id,
        len(to_compact),
        summary_id,
        model_id,
        silent,
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
) -> tuple[str, str, str | None, str | None, str]:
    """First Custom agent (adapter_name='custom') with a user-scoped ModelProfile.

    Returns (model_provider, model_id, api_key, api_base_url, agent_id).
    Raises ValueError when no model-backed agent exists (e.g. CLI-only chat).
    """
    if not agent_ids:
        raise ValueError("当前会话没有配置模型的 agent，无法生成摘要")
    async with get_local_db() as db:
        agents = (
            (await db.execute(select(Agent).where(Agent.id.in_(agent_ids))))
            .scalars()
            .all()
        )
    by_id = {a.id: a for a in agents}
    for aid in agent_ids:
        agent = by_id.get(aid)
        if agent is None or agent.adapter_name != "custom":
            continue
        async with get_local_db() as db:
            profile = (
                await db.execute(
                    select(ModelProfile).where(
                        ModelProfile.is_default == True,  # noqa: E712
                    ).limit(1)
                )
            ).scalar_one_or_none()
        if profile is None:
            continue
        return (profile.provider, profile.model_id, profile.api_key, profile.api_base_url, aid)
    raise ValueError("当前会话没有配置模型的 agent，无法生成摘要")


async def _load_agent_names(agent_ids: list[str]) -> dict[str, str]:
    if not agent_ids:
        return {}
    async with get_local_db() as db:
        agents = (
            (await db.execute(select(Agent).where(Agent.id.in_(agent_ids))))
            .scalars()
            .all()
        )
    return {a.id: a.name for a in agents}


async def _get_agent_system_prompt(agent_id: str) -> str:
    """Fetch the agent's system prompt from DB for cache-safe compaction."""
    if not agent_id:
        return ""
    from app.infra.cache_helpers import get_agent_cached

    agent = await get_agent_cached(agent_id)
    if agent is None:
        return ""
    return agent.system_prompt or ""


async def _summarise(
    transcript: str,
    prior_summary: str | None,
    model_provider: str,
    model_id: str,
    api_key: str | None,
    api_base_url: str | None,
    parent_system_prompt: str = "",
) -> str:
    """Call the LLM to produce a compaction summary. Raises on API failure.

    Reuses the parent conversation's system prompt to maximise cache prefix hits.
    Does NOT pass tools to avoid unintended tool calls during compaction.
    """
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
        "- 已探索的文件/目录结构（路径 + 关键发现）\n"
        "- 执行过的关键命令及其结果摘要\n"
        "- 架构理解与代码结构发现\n"
        "只输出摘要正文，不要加前缀、标题或引号。\n\n"
        f"对话内容：\n{transcript}"
    )

    config = resolve_custom_provider_client_config(
        model_provider, override_key=api_key, api_base_url=api_base_url
    )
    client = AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, max_retries=1
    )
    messages = []
    if parent_system_prompt:
        messages.append({"role": "system", "content": parent_system_prompt})
    messages.append({"role": "user", "content": prompt})
    logger.info(
        "[compact-debug] model=%s messages=%d has_system=%s sys_len=%d tools=none",
        model_id, len(messages), bool(parent_system_prompt), len(parent_system_prompt),
    )
    response = await client.chat.completions.create(
        model=model_id,
        messages=messages,
        max_tokens=1024,
        temperature=0.3,
    )
    raw = response.choices[0].message.content
    return raw.strip() if raw else ""


# ─── Session Memory helpers (Phase 4) ─────────────────────────────────────────


async def get_session_memory(conversation_id: str):
    """Load the active Session Memory record for a conversation.

    Returns a SessionMemoryRecord (summary + covers_up_to) or None.
    """
    from app.memory.session_memory import SessionMemory

    sm = SessionMemory()
    return await sm.get(conversation_id)


# ─── Breakpoint protection helpers (Phase 5) ──────────────────────────────────


def _find_safe_cut_point(messages: list[Message]) -> int:
    """Find a safe cut point that doesn't split tool_use/tool_result chains.

    Starts at ``len(messages) - KEEP_RECENT_MESSAGES`` and moves backward
    when the boundary would orphan a tool_result or leave a pending tool_use.
    """
    cut = len(messages) - KEEP_RECENT_MESSAGES
    while cut > 0 and (
        _is_orphan_tool_result(messages, cut)
        or _is_pending_tool_use(messages, cut)
    ):
        cut -= 1
    return cut


def _is_orphan_tool_result(messages: list[Message], cut: int) -> bool:
    """True when messages[cut] (first in kept) has tool_result whose tool_use is in to_compact."""
    if cut <= 0 or cut >= len(messages):
        return False
    first_kept = messages[cut]
    result_ids = {
        p.get("callId", "")
        for p in first_kept.parts_list
        if p.get("type") == "tool_result"
    }
    if not result_ids:
        return False
    for msg in messages[:cut]:
        for p in msg.parts_list:
            if p.get("type") == "tool_use" and p.get("callId", "") in result_ids:
                return True
    return False


def _is_pending_tool_use(messages: list[Message], cut: int) -> bool:
    """True when messages[cut-1] (last in to_compact) has tool_use whose result is in kept."""
    if cut <= 0 or cut >= len(messages):
        return False
    last_compact = messages[cut - 1]
    use_ids = {
        p.get("callId", "")
        for p in last_compact.parts_list
        if p.get("type") == "tool_use"
    }
    if not use_ids:
        return False
    for msg in messages[cut:]:
        for p in msg.parts_list:
            if p.get("type") == "tool_result" and p.get("callId", "") in use_ids:
                return True
    return False


# ─── Capability restoration helpers (Phase 6) ────────────────────────────────


async def _build_capability_context(
    conversation_id: str, agent_ids: list[str]
) -> str:
    """Build a capability context block for post-compaction restoration.

    Collects: current tool names, active attachments, active dispatch plan.
    Returns a formatted string, or empty string when nothing to inject.
    """
    parts: list[str] = ["[能力上下文]"]

    # Tool names from registry
    try:
        from app.tools.registry import tool_registry

        tool_names = sorted(tool_registry._tools.keys())
        if tool_names:
            parts.append(f"- 当前可用工具: {', '.join(tool_names)}")
    except Exception:
        pass

    # Active attachments
    try:
        async with get_local_db() as db:
            atts = (
                (
                    await db.execute(
                        select(Attachment).where(
                            Attachment.conversation_id == conversation_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if atts:
                att_list = ", ".join(a.file_name for a in atts)
                parts.append(f"- 活跃附件: {att_list}")
    except Exception:
        pass

    # Active dispatch plan
    try:
        async with get_local_db() as db:
            run = (
                (
                    await db.execute(
                        select(AgentRun)
                        .where(
                            and_(
                                AgentRun.conversation_id == conversation_id,
                                AgentRun.dispatch_plan.isnot(None),
                                AgentRun.status == "running",
                            )
                        )
                        .order_by(desc(AgentRun.started_at))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if run and run.dispatch_plan:
                plan = run.dispatch_plan
                tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
                pending = sum(1 for t in tasks if t.get("status") == "pending")
                done = sum(1 for t in tasks if t.get("status") == "done")
                parts.append(
                    f"- 进行中的派发计划: {done}/{len(tasks)} 完成, {pending} 待处理"
                )
    except Exception:
        pass

    if len(parts) == 1:
        return ""
    return "\n".join(parts)
