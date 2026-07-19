"""Conversation-context serialization.

Port of src/server/conversation-context.ts: turn a conversation's MessagePart
history into OpenAI-format chat-message dicts for ``AdapterInput.history`` so an
agent remembers context across runs. Handles pinned-message injection, the latest
context-summary block, agent self/other perspective rendering, and a token budget.
See specs/13-conversation-context.md.

The returned messages are plain dicts ({"role", "content", ...}) matching OpenAI's
ChatCompletionMessageParam shape — the same wire format the TS produced.

Task 4.7: build_history_for() now delegates to PromptAssembler for schema-driven
context assembly while preserving backward compatibility for existing callers.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, Artifact, Conversation, Message
from app.infra.cache_helpers import get_agent_cached
from app.services.compact_markers import CompactMarkerBuilder
from app.services.compact_pipeline import (
    FOLD_TURN_THRESHOLD,
    KEEP_RECENT_TURNS,
    LEGACY_RECENT_KEEP,
    summarize_tool_result_full,
)
from app.services.context_compaction_service import (
    get_latest_context_summary,
    render_conversation_summary_block,
)
from app.services.prompt_assembler import (
    ContextAssembler,
    Query,
    RuntimeContext,
)
from app.services.transcript_renderer import estimate_dict_message_tokens

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 20

# Cross-run replay: cap tool_result text to avoid blowing the history budget.
# prune_old_tool_results already replaces large results with a marker, but this
# is a second safety net for results that slip through (e.g. recent turns).
TOOL_RESULT_REPLAY_CHAR_CAP = 4000

# OpenAI ChatCompletionMessageParam, as a loose dict (kept camelCase-free; pure shape).
ChatMessage = dict


@dataclass
class BuildHistoryOptions:
    """Options for build_history_for (mirrors the TS BuildHistoryOptions)."""

    # How many recent (non-pinned) messages to load. None → default 20.
    max_turns: int | None = None
    # Whether to inject pinned messages. None → True.
    include_pinned: bool | None = None
    # The triggering message id; excluded from history to avoid duplication.
    exclude_message_id: str | None = None
    # Token budget for history only (excl. system / current user). None → no cut.
    token_budget: int | None = None


@dataclass
class _Item:
    msg_id: str
    is_pinned: bool
    serialized: list[ChatMessage]
    tokens: int


# ─── O1: context compaction layers (read-path, no DB writes) ────────────────


def _extract_tool_result_text(part: dict) -> str:
    """Extract a readable string from a tool_result part's 'result' field.

    The persisted shape is ``{"type": "tool_result", "result": dict|list|str|None, ...}``
    (see ``persist_event`` in ``agent_runner.py``). Earlier code read ``content``
    which never exists on tool_result parts — this fixes the field mismatch.
    """
    result = part.get("result", "")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def _find_turn_boundaries_messages(messages: list) -> list[tuple[int, int]]:
    """Identify complete ReAct turns in a list of DB Message objects.

    A turn = one ``role=="agent"`` Message whose ``parts_list`` contains a
    ``tool_use`` part. In the DB, ``tool_use`` and ``tool_result`` parts are
    typically in the same Message (written at ``message.end``), so the turn's
    start and end index are the same. Messages without tool_use don't constitute
    a turn. Returns ``[(start_index, end_index), ...]``.
    """
    boundaries: list[tuple[int, int]] = []
    for i, msg in enumerate(messages):
        if getattr(msg, "role", None) != "agent":
            continue
        parts = msg.parts_list or []
        if any(p.get("type") == "tool_use" for p in parts):
            boundaries.append((i, i))
    return boundaries


def _keep_recent_turns_messages(
    messages: list,
    k: int = KEEP_RECENT_TURNS,
) -> tuple[list, list]:
    """Split messages into (recent, old) on turn boundaries.

    ``recent`` contains everything from the start of the k-th-from-last turn
    onwards (inclusive of user messages between turns). ``old`` contains
    everything before. When there are ``<= k`` complete turns, returns
    ``(messages, [])``.
    """
    boundaries = _find_turn_boundaries_messages(messages)
    if len(boundaries) <= k:
        return list(messages), []
    keep_from = boundaries[-k][0]
    return list(messages[keep_from:]), list(messages[:keep_from])


def _build_tool_use_map(parts: list[dict]) -> dict[str, tuple[str, dict]]:
    """Build a ``callId -> (toolName, args)`` map from tool_use parts."""
    mapping: dict[str, tuple[str, dict]] = {}
    for p in parts:
        if p.get("type") != "tool_use":
            continue
        call_id = p.get("callId", "")
        tool_name = p.get("toolName", "")
        args = p.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                args = {}
        mapping[call_id] = (tool_name, args)
    return mapping


def _should_preserve_tool_result(tool_name: str, args: dict) -> bool:
    """Check if a tool_result should be preserved verbatim (not pruned)."""
    return (
        tool_name == "code_explore"
        or (tool_name == "fs_read" and args.get("mode") in ("outline", "head"))
    )


def prune_old_tool_results(
    messages: list,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
) -> list:
    """Replace old tool_result parts with structured, recoverable markers.

    Uses ``_keep_recent_turns_messages`` to find the cutoff (last
    ``keep_recent_turns`` complete turns). For each ``tool_result`` part in the
    old segment, dispatches to ``summarize_tool_result_full(stage=1)`` and
    replaces the part with a ``CompactMarkerBuilder.build_tool_result_marker``.
    ``code_explore`` and ``fs_read(mode=outline/head)`` results are preserved
    verbatim.
    """
    recent, old = _keep_recent_turns_messages(messages, k=keep_recent_turns)
    if not old:
        return messages

    for msg in old:
        parts = msg.parts_list
        tool_use_map = _build_tool_use_map(parts)
        modified = False
        for j, p in enumerate(parts):
            if p.get("type") != "tool_result":
                continue
            call_id = p.get("callId", "")
            tool_name, args = tool_use_map.get(call_id, ("", {}))
            if _should_preserve_tool_result(tool_name, args):
                continue
            content = _extract_tool_result_text(p)
            _new_content, summary, recover = summarize_tool_result_full(
                tool_name, args, content, stage=1,
            )
            marker = CompactMarkerBuilder.build_tool_result_marker(
                stage=1,
                tool_name=tool_name or "unknown",
                args=args,
                summary=summary,
                recover_hint=recover,
            )
            parts[j] = {"type": "text", "content": marker}
            modified = True
        if modified:
            msg.parts_list = parts
    return messages


def _collect_tool_names_in_span_messages(
    messages: list, start: int, end: int,
) -> Counter[str]:
    """Count tool names invoked by agent messages in [start, end]."""
    counts: Counter[str] = Counter()
    for idx in range(start, end + 1):
        msg = messages[idx]
        if getattr(msg, "role", None) != "agent":
            continue
        for p in msg.parts_list or []:
            if p.get("type") == "tool_use":
                name = p.get("toolName", "")
                if name:
                    counts[name] += 1
    return counts


def _first_user_head_messages(messages: list, start: int, end: int) -> str | None:
    for idx in range(start, end + 1):
        msg = messages[idx]
        if getattr(msg, "role", None) != "user":
            continue
        for p in msg.parts_list or []:
            if p.get("type") == "text" and p.get("content", "").strip():
                return p["content"].strip()[:80]
    return None


def _last_assistant_text_head_messages(
    messages: list, start: int, end: int,
) -> str | None:
    for idx in range(end, start - 1, -1):
        msg = messages[idx]
        if getattr(msg, "role", None) != "agent":
            continue
        for p in msg.parts_list or []:
            if p.get("type") == "text" and p.get("content", "").strip():
                return p["content"].strip()[:80]
    return None


def fold_old_messages(
    messages: list,
    pinned_ids: set[str] | None = None,
) -> list:
    """Fold old messages into a single structured marker when turns exceed threshold.

    Uses ``_find_turn_boundaries_messages`` to count complete turns. When the
    turn count exceeds ``FOLD_TURN_THRESHOLD``, older turns (beyond the most
    recent ``KEEP_RECENT_TURNS``) are replaced with a single fold marker built
    by ``CompactMarkerBuilder.build_fold_marker``. Pinned messages are never
    folded. If no complete turn is found, falls back to ``LEGACY_RECENT_KEEP``
    (count-based) with a warning.
    """
    pinned_set = pinned_ids or set()
    boundaries = _find_turn_boundaries_messages(messages)

    if not boundaries:
        if len(messages) <= LEGACY_RECENT_KEEP:
            return messages
        logger.warning(
            "[conversation-context] fold: no turn boundaries found, "
            "falling back to recent_keep=%d",
            LEGACY_RECENT_KEEP,
        )
        recent = list(messages[-LEGACY_RECENT_KEEP:])
        old = list(messages[:-LEGACY_RECENT_KEEP])
    elif len(boundaries) < FOLD_TURN_THRESHOLD:
        return messages
    else:
        recent, old = _keep_recent_turns_messages(messages, k=KEEP_RECENT_TURNS)
        if not old:
            return messages

    folded = [m for m in old if m.id not in pinned_set]
    kept_from_old = [m for m in old if m.id in pinned_set]
    if not folded:
        return messages

    tools_used = _collect_tool_names_in_span_messages(old, 0, len(old) - 1)
    first_user = _first_user_head_messages(old, 0, len(old) - 1)
    last_reply = _last_assistant_text_head_messages(old, 0, len(old) - 1)

    folded_turns = len(_find_turn_boundaries_messages(folded))
    summary = f"已折叠 {len(folded)} 条消息（{folded_turns} 个工具轮次）"
    fold_marker_text = CompactMarkerBuilder.build_fold_marker(
        stage=3,
        turns_folded=folded_turns,
        tools_used_counts=tools_used,
        summary=summary,
        first_user_msg_head=first_user,
        last_assistant_text_head=last_reply,
    )

    time_start = folded[0].created_at
    fold_marker = SimpleNamespace(
        id=f"folded_{len(folded)}",
        created_at=time_start,
        role="user",
        agent_id=None,
        parts_list=[{"type": "text", "content": fold_marker_text}],
    )
    return [*kept_from_old, fold_marker, *recent]


async def build_history_for(
    agent_id: str,
    conversation_id: str,
    options: BuildHistoryOptions | None = None,
    assembler: ContextAssembler | None = None,
    *,
    user_id: str = "",
) -> list[ChatMessage]:
    """Serialize a conversation into OpenAI chat messages for the given agent.
    
    If an assembler is provided, delegates to PromptAssembler for schema-driven
    context assembly. Otherwise falls back to the original implementation for
    backward compatibility.
    """
    if assembler is not None:
        return await _build_history_with_assembler(
            agent_id, conversation_id, options, assembler, user_id=user_id,
        )
    return await _build_history_legacy(
        agent_id, conversation_id, options
    )


async def _build_history_with_assembler(
    agent_id: str,
    conversation_id: str,
    options: BuildHistoryOptions | None,
    assembler: ContextAssembler,
    *,
    user_id: str = "",
) -> list[ChatMessage]:
    """Build history using PromptAssembler for schema-driven context assembly."""
    opts = options or BuildHistoryOptions()
    max_turns = opts.max_turns if opts.max_turns is not None else DEFAULT_MAX_TURNS
    exclude_message_id = opts.exclude_message_id

    latest_summary = await get_latest_context_summary(conversation_id)

    async with get_db() as db:
        # Load recent messages for query context
        recent_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
                Message.hidden == False,  # noqa: E712 - SQLAlchemy filter
            )
            .order_by(Message.created_at.desc())
            .limit(max_turns)
        )
        if exclude_message_id:
            recent_stmt = recent_stmt.where(Message.id != exclude_message_id)
        if latest_summary is not None:
            recent_stmt = recent_stmt.where(
                Message.created_at > latest_summary.covered_until_created_at
            )
        recent = (await db.execute(recent_stmt)).scalars().all()
        recent_asc = sorted(recent, key=lambda m: m.created_at)

    # Ensure agent is in cache for downstream callers
    await get_agent_cached(agent_id)

    # Build query text from recent messages
    query_text = "\n".join(
        _extract_message_text(m) for m in recent_asc[-3:] if m.role == "user"
    )

    # Determine schema mode based on options
    mode = "chat"
    if opts.token_budget is not None and opts.token_budget < 1000:
        mode = "chat"
    # Could be extended to detect "tool" or "react" modes

    # Assemble context using PromptAssembler
    query = Query(
        text=query_text,
        mode=mode,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    ctx: RuntimeContext = await assembler.assemble(query)

    # Render to OpenAI chat format — render_history() now uses render_static()
    # for cache-stable system messages.
    system_messages = ctx.render_history()
    
    # Fall back to legacy message serialization for conversation history
    legacy_messages = await _build_history_legacy(
        agent_id, conversation_id, options
    )
    
    # Inject dynamic content (render_dynamic) as user message prefix (cache-safe).
    # Dynamic content is wrapped in <system-reminder> tags by render_dynamic().
    dynamic_content = ctx.render_dynamic()
    if dynamic_content and legacy_messages:
        # Prepend dynamic content to the first user message in history
        for i, msg in enumerate(legacy_messages):
            if msg.get("role") == "user":
                legacy_messages[i] = {
                    "role": "user",
                    "content": f"{dynamic_content}\n\n{msg.get('content', '')}",
                }
                break
        else:
            # No user message found; inject as a new user message
            legacy_messages.insert(0, {"role": "user", "content": dynamic_content})
    elif dynamic_content:
        legacy_messages.insert(0, {"role": "user", "content": dynamic_content})
    
    # Combine: system context + conversation history
    return system_messages + legacy_messages


async def _build_history_legacy(
    agent_id: str,
    conversation_id: str,
    options: BuildHistoryOptions | None,
) -> list[ChatMessage]:
    """Original implementation preserved for backward compatibility."""
    opts = options or BuildHistoryOptions()
    max_turns = opts.max_turns if opts.max_turns is not None else DEFAULT_MAX_TURNS
    include_pinned = opts.include_pinned if opts.include_pinned is not None else True
    exclude_message_id = opts.exclude_message_id
    token_budget = opts.token_budget

    latest_summary = await get_latest_context_summary(conversation_id)

    async with get_db() as db:
        # Recent N complete messages (desc by time, flipped to asc below).
        recent_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
                Message.hidden == False,  # noqa: E712 - SQLAlchemy filter
            )
            .order_by(Message.created_at.desc())
            .limit(max_turns)
        )
        if exclude_message_id:
            recent_stmt = recent_stmt.where(Message.id != exclude_message_id)
        if latest_summary is not None:
            recent_stmt = recent_stmt.where(
                Message.created_at > latest_summary.covered_until_created_at
            )
        recent = (await db.execute(recent_stmt)).scalars().all()

        # Always load conversation for pinned ids + agentIds (name map for Phase C).
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalars().first()

        # Pinned messages may live outside the recent N; load them separately.
        pinned: list[Message] = []
        pinned_id_set: set[str] = set()
        if include_pinned and conv is not None:
            pinned_ids = [
                pid
                for pid in conv.pinned_message_ids_list
                if pid != exclude_message_id
            ]
            if pinned_ids:
                pinned = list(
                    (
                        await db.execute(
                            select(Message).where(
                                Message.id.in_(pinned_ids),
                                Message.status == "complete",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                pinned_id_set = {p.id for p in pinned}

        # Agent name map: Phase C group chat renders other agents as [Name]: text.
        agent_names: dict[str, str] = {}
        if conv is not None and len(conv.agent_ids_list) > 1:
            rows = (
                await db.execute(
                    select(Agent.id, Agent.name).where(
                        Agent.id.in_(conv.agent_ids_list)
                    )
                )
            ).all()
            for row in rows:
                agent_names[row.id] = row.name

        # Merge + dedup by id, sort ascending by createdAt.
        by_id: dict[str, Message] = {}
        for m in recent:
            by_id[m.id] = m
        for m in pinned:
            by_id[m.id] = m
        merged = sorted(by_id.values(), key=lambda m: m.created_at)

        # O1: prune large old tool_results, then fold old messages.
        merged = prune_old_tool_results(merged)
        merged = fold_old_messages(merged, pinned_ids=pinned_id_set)

        # Batch-load artifact titles for artifact_ref folding.
        artifact_ids = _collect_artifact_ids(merged)
        artifact_titles = await _load_artifact_titles(db, artifact_ids)

    # Serialize everything, then drop oldest non-pinned items to fit the budget.
    items: list[_Item] = []
    if latest_summary is not None:
        summary_message: ChatMessage = {
            "role": "user",
            "content": render_conversation_summary_block(latest_summary),
        }
        items.append(
            _Item(
                msg_id=latest_summary.id,
                is_pinned=True,
                serialized=[summary_message],
                tokens=estimate_dict_message_tokens(
                summary_message, include_reasoning=False
            ),
            )
        )
    for msg in merged:
        serialized = _serialize_message(msg, agent_id, artifact_titles, agent_names)
        if not serialized:
            continue
        tokens = sum(
            estimate_dict_message_tokens(m, include_reasoning=False)
            for m in serialized
        )
        items.append(
            _Item(
                msg_id=msg.id,
                is_pinned=msg.id in pinned_id_set,
                serialized=serialized,
                tokens=tokens,
            )
        )

    if token_budget is not None and token_budget > 0:
        total = sum(it.tokens for it in items)
        # Over budget: drop non-pinned from oldest to newest until it fits.
        i = 0
        while i < len(items) and total > token_budget:
            if not items[i].is_pinned:
                total -= items[i].tokens
                items[i].tokens = -1  # mark dropped; filtered below
            i += 1

    out: list[ChatMessage] = []
    for it in items:
        if it.tokens < 0:
            continue
        out.extend(it.serialized)
    return out


def _extract_message_text(msg: Message) -> str:
    """Extract plain text from a message for query context."""
    parts = msg.parts_list
    texts = []
    for p in parts:
        if p.get("type") == "text" and p.get("content"):
            texts.append(p["content"])
    return "\n".join(texts).strip()


# ─── serialization core ─────────────────────────────────────────────────────


def _serialize_message(
    msg: Message,
    current_agent_id: str,
    artifact_titles: dict[str, str],
    agent_names: dict[str, str],
) -> list[ChatMessage] | None:
    if msg.role == "system":
        return None  # system prompt is injected by the runner, not history

    parts = msg.parts_list

    if msg.role == "user":
        content = _render_user_parts(parts)
        if not content:
            return None
        return [{"role": "user", "content": content}]

    if msg.role == "agent":
        if msg.agent_id == current_agent_id:
            return _render_self_assistant_parts(parts, artifact_titles)
        # Phase C: other agent's message → [Name]: text user msg (group chat only).
        if msg.agent_id and msg.agent_id in agent_names:
            m = _render_other_agent_as_user(
                parts, agent_names[msg.agent_id], artifact_titles
            )
            return [m] if m else None
        return None

    return None


def _render_user_parts(parts: list[dict]) -> str:
    # 1. Prefer effective_prompt (contains dynamic_prefix + [current_time]) so
    #    history reconstruction matches what was actually sent to the LLM,
    #    keeping DeepSeek's prefix cache continuous across turns.
    effective = None
    attachment_labels: list[str] = []
    for p in parts:
        t = p.get("type")
        if t == "effective_prompt" and p.get("content"):
            effective = p["content"]
        elif t == "image_attachment":
            attachment_labels.append(f"[图片附件: {p.get('fileName')}]")
        elif t == "file_attachment":
            attachment_labels.append(f"[文件附件: {p.get('fileName')}]")

    if effective is not None:
        if attachment_labels:
            effective += "\n" + "\n".join(attachment_labels)
        return effective

    # 2. Fallback: reconstruct from raw text parts (pre-existing behavior)
    buf: list[str] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            buf.append(p.get("content", ""))
        elif t == "image_attachment":
            buf.append(f"[图片附件: {p.get('fileName')}]")
        elif t == "file_attachment":
            buf.append(f"[文件附件: {p.get('fileName')}]")
        # user shouldn't carry thinking/tool_use/tool_result/code/artifact_ref.
    return "\n".join(buf).strip()


def _render_self_assistant_parts(
    parts: list[dict], artifact_titles: dict[str, str]
) -> list[ChatMessage] | None:
    text = _render_agent_public_text(parts, artifact_titles)
    if not text:
        return None
    return [{"role": "assistant", "content": text}]


def _render_other_agent_as_user(
    parts: list[dict], agent_name: str, artifact_titles: dict[str, str]
) -> ChatMessage | None:
    # Phase C: fold another agent's message into a [Name] text user message;
    # keep text/code/artifact_ref only, drop thinking/tool_use/tool_result.
    text = _render_agent_public_text(parts, artifact_titles)
    if not text:
        return None
    return {"role": "user", "content": f"[{agent_name}] {text}"}


def _render_agent_public_text(
    parts: list[dict], artifact_titles: dict[str, str]
) -> str:
    tool_use_map = _build_tool_use_map(parts)
    buf: list[str] = []
    for p in parts:
        t = p.get("type")
        if t in ("text", "code"):
            if p.get("content"):
                buf.append(p["content"])
        elif t == "tool_result":
            text = _extract_tool_result_text(p)
            if text:
                is_error = p.get("isError", False)
                prefix = "[tool_error]" if is_error else "[tool_result]"
                call_id = p.get("callId", "")
                tool_name, args = tool_use_map.get(call_id, ("", {}))
                if not _should_preserve_tool_result(tool_name, args) and len(text) > TOOL_RESULT_REPLAY_CHAR_CAP:
                    text = (
                        text[:TOOL_RESULT_REPLAY_CHAR_CAP]
                        + f"...[truncated, {len(text)} chars total]"
                    )
                buf.append(f"{prefix} {text}")
        elif t == "artifact_ref":
            artifact_id = p.get("artifactId")
            title = artifact_titles.get(artifact_id, "")
            buf.append(
                f"[产物: {title} (id={artifact_id})]"
                if title
                else f"[产物 {artifact_id}]"
            )
        elif t == "deploy_status":
            deployment = p.get("deployment", {})
            if deployment.get("status") == "ready":
                buf.append(
                    f"[部署预览: {deployment.get('title')} "
                    f"{_format_deployment_source_label(deployment)} "
                    f"({deployment.get('previewPath')})]"
                )
            else:
                buf.append(
                    f"[部署失败: {deployment.get('title')} "
                    f"({deployment.get('error') or 'unknown error'})]"
                )
        # thinking/tool_use are not replayed in cross-run history.
    return "\n".join(buf).strip()


def _format_deployment_source_label(deployment: dict) -> str:
    if deployment.get("sourceType") == "workspace":
        return f"workspace={deployment.get('workspacePath') or 'unknown'}"
    return f"v{deployment.get('version')}"


# ─── batch artifact title load ──────────────────────────────────────────────


def _collect_artifact_ids(messages: list[Message]) -> list[str]:
    ids: set[str] = set()
    for m in messages:
        if m.role != "agent":
            continue
        for p in m.parts_list:
            if p.get("type") == "artifact_ref":
                ids.add(p.get("artifactId"))
    return list(ids)


async def _load_artifact_titles(db, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not ids:
        return out
    rows = (
        await db.execute(
            select(Artifact.id, Artifact.title).where(Artifact.id.in_(ids))
        )
    ).all()
    for row in rows:
        out[row.id] = row.title
    return out
