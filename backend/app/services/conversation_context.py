"""Conversation-context serialization.

Turns a conversation's MessagePart history into OpenAI-format chat-message
dicts for ``AdapterInput.history`` so an agent remembers context across runs.

Uses the unified CompactMessage pipeline with tiered injection (design doc §8.1):
- Case A: no messages after Note → inject Note only
- Case B: ratio < 0.50 → full text only, no Note
- Case C: 0.50 ≤ ratio < 0.75 → Note + full text, no compaction
- Case D: 0.75 ≤ ratio < 0.88 → Note + mask-compacted
- Case E: ratio ≥ 0.88 → Note + fold-compacted

DB always stores complete, uncompacted messages. Layer 1 (ReAct loop) compaction
is transient (in-memory only). Layer 3 (cross-run) loads from DB and compacts
independently — no double-compaction risk.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Agent, Artifact, Conversation, Message
from app.infra.cache_helpers import get_agent_cached
from app.memory.session_memory import SessionMemory
from app.memory.session_note import SessionNote
from app.services.compact_pipeline import (
    from_compact_messages,
    run_compact_pipeline_unified,
    to_compact_messages_orm,
)
from app.services.prompt_assembler import (
    ContextAssembler,
    Query,
    RuntimeContext,
)
from app.services.transcript_renderer import (
    estimate_dict_message_tokens,
    estimate_full_message_tokens,
)

logger = logging.getLogger(__name__)

# Fixed context window for ratio calculation (design doc §8.2).
_CONTEXT_WINDOW = 200_000

# Tiered injection thresholds (design doc §8.1).
_RATIO_FULL_ONLY = 0.50      # Below: full text only, no Note
_RATIO_NOTE_PLUS_FULL = 0.75 # Below: Note + full text, no compaction
_RATIO_NOTE_PLUS_MASK = 0.88 # Below: Note + mask; at/above: Note + fold

# OpenAI ChatCompletionMessageParam, as a loose dict.
ChatMessage = dict


@dataclass
class BuildHistoryOptions:
    """Options for build_history_for."""

    max_turns: int | None = None
    include_pinned: bool | None = None
    exclude_message_id: str | None = None
    token_budget: int | None = None
    model_context_limit: int | None = None
    prompt_estimate: int = 0


@dataclass
class _Item:
    msg_id: str
    is_pinned: bool
    serialized: list[ChatMessage]
    tokens: int


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _extract_tool_result_text(part: dict) -> str:
    """Extract a readable string from a tool_result part's 'result' field."""
    result = part.get("result", "")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


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


def _extract_whitelist(note: SessionNote | None) -> set[str] | None:
    """Extract file paths from Session Note's files_touched.

    These files are preserved longer in Layer 1 compaction (design doc §8.3).
    Entry format: "path/to/file.py (已读, 292 行)" → "path/to/file.py"
    """
    if not note or not note.files_touched:
        return None
    whitelist: set[str] = set()
    for entry in note.files_touched:
        path = entry.split("(")[0].strip()
        if path:
            whitelist.add(path)
    return whitelist


def _build_session_note_message(session_mem) -> ChatMessage | None:
    """Build a chat message injecting the Session Note.

    Tries YAML parse → structured XML; falls back to plain text (design doc §7.3).
    """
    if not session_mem or not session_mem.summary:
        return None
    note = SessionNote.from_yaml(session_mem.summary)
    if note is not None:
        sm_content = note.to_xml()
    else:
        covers_ts = (
            session_mem.covers_up_to
            if session_mem.covers_up_to is not None
            else 0
        )
        sm_content = (
            f'<session_memory covers_up_to="{covers_ts}">\n'
            f"{session_mem.summary}\n"
            "</session_memory>"
        )
    return {"role": "user", "content": sm_content}


# ─── Capability context restoration ─────────────────────────────────────────


async def _build_capability_context(
    conversation_id: str, agent_ids: list[str]
) -> str:
    """Build a capability context block for post-compaction restoration.

    Collects: current tool names, active attachments, active dispatch plan,
    recent file paths (from Session Note), active plan cards, recent skill loads.
    """
    parts: list[str] = ["[能力上下文]"]

    # Tools
    tool_names: set[str] = set()
    for aid in agent_ids:
        agent = await get_agent_cached(aid)
        if agent:
            for tn in agent.tool_names_list or []:
                tool_names.add(tn)
    if tool_names:
        parts.append(f"- 可用工具: {', '.join(sorted(tool_names))}")

    # Attachments
    try:
        async with get_local_db() as db:
            attach_msgs = (
                (
                    await db.execute(
                        select(Message).where(
                            Message.conversation_id == conversation_id,
                            Message.status == "complete",
                        ).order_by(
                            Message.created_at.desc()
                        ).limit(30)
                    )
                ).scalars().all()
            )
            attachments: list[str] = []
            for msg in attach_msgs:
                for p in msg.parts_list or []:
                    if p.get("type") in ("image_attachment", "file_attachment"):
                        fname = p.get("fileName", "unknown")
                        attachments.append(fname)
            if attachments:
                parts.append(f"- 附件: {', '.join(attachments[:5])}")
    except Exception:
        pass

    # Recent file paths from Session Note
    session_note = await _get_session_note(conversation_id)
    if session_note and session_note.files_touched:
        files_list = ", ".join(session_note.files_touched[:10])
        parts.append(f"- 最近操作文件: {files_list}")

    # Active plan cards
    try:
        async with get_local_db() as db:
            plan_msgs = (
                (
                    await db.execute(
                        select(Message).where(
                            Message.conversation_id == conversation_id,
                            Message.status == "complete",
                        ).order_by(
                            Message.created_at.desc()
                        ).limit(50)
                    )
                ).scalars().all()
            )
            active_plans: list[str] = []
            for msg in plan_msgs:
                for p in msg.parts_list or []:
                    if p.get("type") == "tool_result":
                        data = _try_parse_json(_extract_tool_result_text(p))
                        if data and data.get("planId"):
                            active_plans.append(f"planId={data['planId']}")
            if active_plans:
                parts.append(f"- 活跃计划: {', '.join(active_plans[:3])}")
    except Exception:
        pass

    # Recent skill loads
    try:
        async with get_local_db() as db:
            skill_msgs = (
                (
                    await db.execute(
                        select(Message).where(
                            Message.conversation_id == conversation_id,
                            Message.status == "complete",
                        ).order_by(
                            Message.created_at.desc()
                        ).limit(50)
                    )
                ).scalars().all()
            )
            skills: set[str] = set()
            for msg in skill_msgs:
                for p in msg.parts_list or []:
                    if p.get("type") == "tool_use" and p.get("toolName") == "load_skill":
                        slug = (p.get("args") or {}).get("slug", "")
                        if slug:
                            skills.add(slug)
            if skills:
                parts.append(f"- 已加载技能: {', '.join(sorted(skills))}")
    except Exception:
        pass

    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def _try_parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


async def _get_session_note(conversation_id: str) -> SessionNote | None:
    """Load and parse the Session Note for a conversation."""
    session_mem = await SessionMemory().get(conversation_id)
    if not session_mem or not session_mem.summary:
        return None
    return SessionNote.from_yaml(session_mem.summary)


# ─── Public API ─────────────────────────────────────────────────────────────


async def build_run_messages(
    run_id: str,
    conversation_id: str,
    agent_id: str,
    *,
    include_hidden: bool = False,
) -> list[ChatMessage]:
    """Rebuild chat messages from Message table for a specific run.

    Unlike ``build_history_for`` which queries by conversation_id, this queries
    by ``run_id`` — needed for mini-run context reconstruction where only the
    target run's messages (including hidden ones) are relevant.

    When ``include_hidden=False`` (default), filters ``hidden == False``
    (preserving current ``build_history_for`` behavior).
    When ``include_hidden=True``, includes all messages (mini-run needs
    hidden messages for full context reconstruction).
    """
    async with get_local_db() as db:
        stmt = (
            select(Message)
            .where(
                Message.run_id == run_id,
                Message.status == "complete",
            )
            .order_by(Message.created_at)
        )
        if not include_hidden:
            stmt = stmt.where(Message.hidden == False)  # noqa: E712
        msgs = (await db.execute(stmt)).scalars().all()

        # Load conversation for agent_names (multi-agent rendering)
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalars().first()

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

        artifact_ids = _collect_artifact_ids(msgs)
        artifact_titles = await _load_artifact_titles(db, artifact_ids)

        db.expunge_all()

    out: list[ChatMessage] = []
    for msg in msgs:
        serialized = _serialize_message(msg, agent_id, artifact_titles, agent_names)
        if serialized:
            out.extend(serialized)
    return out


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
    context assembly. Otherwise uses the unified CompactMessage pipeline with
    tiered injection (design doc §8.1).
    """
    if assembler is not None:
        return await _build_history_with_assembler(
            agent_id, conversation_id, options, assembler, user_id=user_id,
        )
    return await _build_history_unified(
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
    max_turns = opts.max_turns
    exclude_message_id = opts.exclude_message_id

    session_mem = await SessionMemory().get(conversation_id)
    covers_up_to = session_mem.covers_up_to if session_mem else None

    async with get_local_db() as db:
        recent_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
                Message.hidden == False,  # noqa: E712 - SQLAlchemy filter
            )
            .order_by(Message.created_at.desc())
        )
        if max_turns is not None:
            recent_stmt = recent_stmt.limit(max_turns)
        if exclude_message_id:
            recent_stmt = recent_stmt.where(Message.id != exclude_message_id)
        if covers_up_to is not None:
            recent_stmt = recent_stmt.where(
                Message.created_at > covers_up_to
            )
        recent = (await db.execute(recent_stmt)).scalars().all()
        recent_asc = sorted(recent, key=lambda m: m.created_at)

        db.expunge_all()

    await get_agent_cached(agent_id)

    query_text = "\n".join(
        _extract_message_text(m) for m in recent_asc[-3:] if m.role == "user"
    )

    mode = "chat"
    if opts.token_budget is not None and opts.token_budget < 1000:
        mode = "chat"

    query = Query(
        text=query_text,
        mode=mode,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    ctx: RuntimeContext = await assembler.assemble(query)

    system_messages = ctx.render_history()

    history_messages = await _build_history_unified(
        agent_id, conversation_id, options
    )

    dynamic_content = ctx.render_dynamic()
    if dynamic_content and history_messages:
        for i, msg in enumerate(history_messages):
            if msg.get("role") == "user":
                history_messages[i] = {
                    "role": "user",
                    "content": f"{dynamic_content}\n\n{msg.get('content', '')}",
                }
                break
        else:
            history_messages.insert(0, {"role": "user", "content": dynamic_content})
    elif dynamic_content:
        history_messages.insert(0, {"role": "user", "content": dynamic_content})

    return system_messages + history_messages


# ─── Unified pipeline (tiered injection) ────────────────────────────────────


def _sum_dict_message_tokens(messages: list[ChatMessage]) -> int:
    """Pure helper: total token estimate for OpenAI-format chat dicts.

    Runs inside a worker thread (via ``asyncio.to_thread``) so long
    conversations don't block the event loop
    (speed-up-first-token-latency, decision 3).
    """
    return sum(
        estimate_dict_message_tokens(m, include_reasoning=False)
        for m in messages
    )


def _estimate_serialized_tokens(
    serialized_list: list[list[ChatMessage]],
) -> list[int]:
    """Pure helper: per-message token sums for a batch of serialized messages.

    Same-origin estimation as :func:`_sum_dict_message_tokens`; batched into
    one ``asyncio.to_thread`` call.
    """
    return [
        sum(
            estimate_dict_message_tokens(m, include_reasoning=False)
            for m in serialized
        )
        for serialized in serialized_list
    ]


async def _build_history_unified(
    agent_id: str,
    conversation_id: str,
    options: BuildHistoryOptions | None,
) -> list[ChatMessage]:
    """Build history using CompactMessage unified pipeline with tiered injection.

    Tiered injection (design doc §8.1):
      - Case A: no messages after Note → inject Note only
      - Case B: ratio < 0.50 → full text only, no Note
      - Case C: 0.50 ≤ ratio < 0.75 → Note + full text, no compaction
      - Case D: 0.75 ≤ ratio < 0.88 → Note + mask-compacted
      - Case E: ratio ≥ 0.88 → Note + fold-compacted
    """
    opts = options or BuildHistoryOptions()
    max_turns = opts.max_turns
    include_pinned = opts.include_pinned if opts.include_pinned is not None else True
    exclude_message_id = opts.exclude_message_id
    token_budget = opts.token_budget

    session_mem = await SessionMemory().get(conversation_id)
    covers_up_to = session_mem.covers_up_to if session_mem else None

    async with get_local_db() as db:
        recent_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
                Message.hidden == False,  # noqa: E712 - SQLAlchemy filter
            )
            .order_by(Message.created_at.desc())
        )
        if max_turns is not None:
            recent_stmt = recent_stmt.limit(max_turns)
        if exclude_message_id:
            recent_stmt = recent_stmt.where(Message.id != exclude_message_id)
        if covers_up_to is not None:
            recent_stmt = recent_stmt.where(
                Message.created_at > covers_up_to
            )
        recent = (await db.execute(recent_stmt)).scalars().all()

        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalars().first()

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
                    ).scalars().all()
                )
                pinned_id_set = {p.id for p in pinned}

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

        by_id: dict[str, Message] = {}
        for m in recent:
            by_id[m.id] = m
        for m in pinned:
            by_id[m.id] = m
        merged = sorted(by_id.values(), key=lambda m: m.created_at)

        # CRITICAL: expunge_all before any compaction logic.
        # DB always stores complete messages; Layer 1 compaction is transient.
        # Layer 3 loads from DB and compacts independently.
        db.expunge_all()

        artifact_ids = _collect_artifact_ids(merged)
        artifact_titles = await _load_artifact_titles(db, artifact_ids)

    # ─── Tiered injection decision ────────────────────────────────────
    if not merged:
        # Case A: no messages after Note → inject Note only (if exists).
        note_msg = _build_session_note_message(session_mem)
        return [note_msg] if note_msg else []

    # Full-message token estimation is pure CPU over local (expunged) lists —
    # run it off the event loop; long conversations cost tens to hundreds of ms
    # (speed-up-first-token-latency, decision 3).
    loaded_tokens = await asyncio.to_thread(estimate_full_message_tokens, merged)
    prompt_estimate = opts.prompt_estimate or 0
    ratio = (loaded_tokens + prompt_estimate) / _CONTEXT_WINDOW

    has_note = session_mem is not None and bool(session_mem.summary)
    note_msg = _build_session_note_message(session_mem) if has_note else None
    session_note_obj = (
        SessionNote.from_yaml(session_mem.summary)
        if has_note else None
    )

    inject_note = False
    compact_stage = 0  # 0 = no compaction

    if ratio < _RATIO_FULL_ONLY:
        # Case B: full text only, no Note.
        inject_note = False
        compact_stage = 0
    elif ratio < _RATIO_NOTE_PLUS_FULL:
        # Case C: Note + full text, no compaction.
        inject_note = True
        compact_stage = 0
    elif ratio < _RATIO_NOTE_PLUS_MASK:
        # Case D: Note + mask-compacted.
        inject_note = True
        compact_stage = 1
    else:
        # Case E: Note + fold-compacted.
        inject_note = True
        compact_stage = 3

    # ─── Apply compaction via unified pipeline ───────────────────────
    if compact_stage > 0:
        note_whitelist = _extract_whitelist(session_note_obj)
        compact_msgs = to_compact_messages_orm(merged)
        for cm in compact_msgs:
            if cm.id in pinned_id_set:
                cm.is_pinned = True
        compact_result = run_compact_pipeline_unified(
            compact_msgs,
            stage=compact_stage,
            pinned_ids=pinned_id_set,
            note_whitelist=note_whitelist,
        )
        compact_dicts = from_compact_messages(compact_result)
        result_msgs: list[ChatMessage] = []
        if inject_note and note_msg:
            result_msgs.append(note_msg)
        result_msgs.extend(compact_dicts)

        if token_budget is not None and token_budget > 0:
            total = await asyncio.to_thread(_sum_dict_message_tokens, result_msgs)
            while len(result_msgs) > 1 and total > token_budget:
                msg = result_msgs.pop(0)
                if msg is not note_msg:
                    total -= estimate_dict_message_tokens(
                        msg, include_reasoning=False
                    )
        return result_msgs

    # No compaction needed — serialize messages normally.
    items: list[_Item] = []
    if inject_note and note_msg:
        items.append(
            _Item(
                msg_id="session_memory",
                is_pinned=True,
                serialized=[note_msg],
                tokens=estimate_dict_message_tokens(
                    note_msg, include_reasoning=False
                ),
            )
        )
    serialized_pairs: list[tuple[str, bool, list[ChatMessage]]] = []
    for msg in merged:
        serialized = _serialize_message(msg, agent_id, artifact_titles, agent_names)
        if not serialized:
            continue
        serialized_pairs.append((msg.id, msg.id in pinned_id_set, serialized))

    # Batch per-message token estimation into one worker-thread call (pure CPU
    # over local lists; long conversations otherwise block the event loop).
    if serialized_pairs:
        token_sums = await asyncio.to_thread(
            _estimate_serialized_tokens, [s for _, _, s in serialized_pairs]
        )
        for (msg_id, is_pinned, serialized), tokens in zip(
            serialized_pairs, token_sums
        ):
            items.append(
                _Item(
                    msg_id=msg_id,
                    is_pinned=is_pinned,
                    serialized=serialized,
                    tokens=tokens,
                )
            )

    if token_budget is not None and token_budget > 0:
        total = sum(it.tokens for it in items)
        i = 0
        while i < len(items) and total > token_budget:
            if not items[i].is_pinned:
                total -= items[i].tokens
                items[i].tokens = -1
            i += 1

    out: list[ChatMessage] = []
    for it in items:
        if it.tokens < 0:
            continue
        out.extend(it.serialized)
    return out


# ─── Serialization core ─────────────────────────────────────────────────────


def _extract_message_text(msg: Message) -> str:
    """Extract plain text from a message for query context."""
    parts = msg.parts_list
    texts = []
    for p in parts:
        if p.get("type") == "text" and p.get("content"):
            texts.append(p["content"])
    return "\n".join(texts).strip()


def _serialize_message(
    msg: Message,
    current_agent_id: str,
    artifact_titles: dict[str, str],
    agent_names: dict[str, str],
) -> list[ChatMessage] | None:
    if msg.role == "system":
        return None

    parts = msg.parts_list

    if msg.role == "user":
        content = _render_user_parts(parts)
        if not content:
            return None
        return [{"role": "user", "content": content}]

    if msg.role == "agent":
        if msg.agent_id == current_agent_id:
            return _render_self_assistant_parts(parts, artifact_titles)
        if msg.agent_id and msg.agent_id in agent_names:
            m = _render_other_agent_as_user(
                parts, agent_names[msg.agent_id], artifact_titles
            )
            return [m] if m else None
        return None

    return None


def _render_user_parts(parts: list[dict]) -> str:
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

    buf: list[str] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            buf.append(p.get("content", ""))
        elif t == "image_attachment":
            buf.append(f"[图片附件: {p.get('fileName')}]")
        elif t == "file_attachment":
            buf.append(f"[文件附件: {p.get('fileName')}]")
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
    text = _render_agent_public_text(parts, artifact_titles)
    if not text:
        return None
    return {"role": "user", "content": f"[{agent_name}] {text}"}


def _render_agent_public_text(
    parts: list[dict], artifact_titles: dict[str, str]
) -> str:
    """Render agent message parts as visible text.

    No 4000-char hard truncation — mask-compacted content is already short,
    and whitelisted content should not be truncated (design doc §8.5).
    """
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
    return "\n".join(buf).strip()


def _format_deployment_source_label(deployment: dict) -> str:
    if deployment.get("sourceType") == "workspace":
        return f"workspace={deployment.get('workspacePath') or 'unknown'}"
    return f"v{deployment.get('version')}"


# ─── Batch artifact title load ───────────────────────────────────────────────


def _collect_artifact_ids(messages: list) -> list[str]:
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
