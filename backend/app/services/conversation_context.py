"""Conversation-context serialization.

Port of src/server/conversation-context.ts: turn a conversation's MessagePart
history into OpenAI-format chat-message dicts for ``AdapterInput.history`` so an
agent remembers context across runs. Handles pinned-message injection, the latest
context-summary block, agent self/other perspective rendering, and a token budget.
See specs/13-conversation-context.md.

The returned messages are plain dicts ({"role", "content", ...}) matching OpenAI's
ChatCompletionMessageParam shape — the same wire format the TS produced.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, Artifact, Conversation, Message
from app.services.context_compaction_service import (
    get_latest_context_summary,
    render_conversation_summary_block,
)
from app.utils.model_registry import estimate_tokens

DEFAULT_MAX_TURNS = 20

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


async def build_history_for(
    agent_id: str,
    conversation_id: str,
    options: BuildHistoryOptions | None = None,
) -> list[ChatMessage]:
    """Serialize a conversation into OpenAI chat messages for the given agent."""
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
                tokens=_estimate_chat_message_tokens(summary_message),
            )
        )
    for msg in merged:
        serialized = _serialize_message(msg, agent_id, artifact_titles, agent_names)
        if not serialized:
            continue
        tokens = sum(_estimate_chat_message_tokens(m) for m in serialized)
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


# ─── token estimation (coarse, 4 chars ≈ 1 token) ───────────────────────────


def _estimate_chat_message_tokens(m: ChatMessage) -> int:
    s = ""
    content = m.get("content")
    if isinstance(content, str):
        s += content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                s += part.get("text", "")
            # multimodal image_url isn't in Phase A history (spec 13); skip.
    tool_calls = m.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            if tc.get("type") == "function":
                fn = tc.get("function", {})
                s += fn.get("name", "") + fn.get("arguments", "")
    # Each message has role/metadata overhead; add 4 tokens of slack.
    return estimate_tokens(s) + 4


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
    buf: list[str] = []
    for p in parts:
        t = p.get("type")
        if t in ("text", "code"):
            if p.get("content"):
                buf.append(p["content"])
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
        # Cross-run history keeps public output only; thinking/tool_* not replayed.
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
