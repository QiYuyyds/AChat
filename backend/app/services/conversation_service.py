"""Conversation service.

Port of src/server/conversation-service.ts (~1,000 lines): conversation CRUD plus
the message lifecycle (send / withdraw / edit-resend / regenerate / pin /
bookmark) and the dispatch-plan revision hook.

Design notes for the TS → Python port:

  - The TS module used a global ``db`` singleton with ``db.transaction(...)``.
    Here each function manages its own session via :func:`get_db` (one
    transaction per ``async with`` block), which keeps multi-table writes atomic
    and — importantly — lets the 500 ms "let finalize() flush" wait in
    withdraw / regenerate happen *between* transactions instead of holding a DB
    connection open.
  - JSON columns (parts / agent_ids / usage ...) are stored as **camelCase** to
    stay byte-compatible with the existing frontend and the TS rows. Message
    ``parts`` are therefore built as raw camelCase dicts, not via Pydantic.
  - ``AgentRunner`` is looked up through :mod:`runner_registry` (no-op until
    阶段 5); deploy tooling through :mod:`deploy_command_service`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select

from app.adapters.session_store import clear_claude_code_session, clear_codex_session
from app.config import get_settings
from app.db.engine import get_local_db
from app.db.models import (
    Agent,
    AgentRun,
    Artifact,
    Attachment,
    ContextSummary,
    Conversation,
    Message,
    ModelProfile,
    Workspace,
)
from app.schemas.events import (
    MessageAddedEvent,
    MessageRecord,
    MessageRemovedEvent,
    SummaryUpdatedEvent,
)
from app.schemas.requests import ConversationResponse
from app.services import deploy_command_service
from app.services.deploy_command_service import DeployCommandResult
from app.services.event_bus import event_bus
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.runner_registry import get_agent_runner
from app.services.worktree_service import (
    cleanup_fork_worktree,
    create_fork_worktree,
    ensure_git_init,
    is_git_repo,
)
from app.utils.clock import now_ms
from app.utils.ids import (
    new_artifact_id,
    new_conversation_id,
    new_message_id,
    new_workspace_id,
)
from app.utils.platform import IS_WINDOWS
from app.utils.workspace_utils import is_path_safe

logger = logging.getLogger(__name__)

# Per-conversation pin cap (port of shared/constants.ts PIN_LIMIT_PER_CONVERSATION):
# bounds how many messages get re-injected into the system prompt.
PIN_LIMIT_PER_CONVERSATION = 5


# ─── Per-conversation write lock ────────────────────────────────────────────
# Serializes all message-writing operations (send / withdraw / regenerate /
# clear / delete / revise_plan / edit_and_resend) within a single conversation.
# Different conversations run in parallel; the same conversation is strictly
# serial. This prevents timestamp collisions, inconsistent history snapshots
# between concurrent runs, and withdraw-vs-send race conditions.
#
# edit_and_resend acquires the lock once and calls the *_unlocked internal
# functions to avoid a self-deadlock (asyncio.Lock is non-reentrant).
class _ConvLockManager:
    """Per-conversation asyncio.Lock: different conversations parallel, same serial."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock_for(self, conversation_id: str) -> AsyncIterator[None]:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        async with lock:
            yield


_conv_locks = _ConvLockManager()

# Windows requires an explicit drive letter / UNC for a bound path, otherwise
# "/tmp" would be resolved against the current drive — not what the user meant.
_WIN_ABS_RE = re.compile(r"^([A-Za-z]:[\\/]|\\\\)")


def _workspaces_root(user_id: str | None = None) -> str:
    """Root dir holding per-conversation sandbox workspaces.

    When ``user_id`` is given, returns a user-scoped path:
    ``<workspace_path>/users/<user_id>/workspaces/``.
    Without ``user_id``, returns the legacy unscoped root for backward compat.
    """
    base = str(get_settings().workspace_path)
    if user_id is not None:
        return os.path.join(base, "users", user_id, "workspaces")
    return base


# ─── Result types (mirror the TS interfaces) ────────────────────────────────
@dataclass
class SendMessageResult:
    message_id: str
    run_ids: list[str]
    messages: list[MessageRecord] | None = None
    deploy: DeployCommandResult | None = None


@dataclass
class WithdrawResult:
    deleted_message_ids: list[str]
    deleted_artifact_ids: list[str]


@dataclass
class RegenerateResult:
    deleted_message_ids: list[str]
    deleted_artifact_ids: list[str]
    trigger_message_id: str
    run_ids: list[str]


@dataclass
class EditAndResendResult:
    deleted_message_ids: list[str]
    deleted_artifact_ids: list[str]
    new_message: MessageRecord
    run_ids: list[str]


@dataclass
class ClearConversationHistoryResult:
    conversation: ConversationResponse
    deleted_message_count: int
    deleted_run_count: int
    deleted_summary_count: int


# ─── Fork ────────────────────────────────────────────────────────────────────
class GitInitRequiredError(Exception):
    """Raised when fork source is not a git repo and user hasn't confirmed git init."""

    def __init__(self, source_path: str) -> None:
        self.source_path = source_path
        super().__init__(f"Git initialization required for: {source_path}")


@dataclass
class ForkConversationResult:
    conversation: ConversationResponse


async def fork_conversation(
    *,
    source_conv_id: str,
    fork_point_message_id: str,
    confirm_git_init: bool = False,
) -> ConversationResponse:
    """Create a new conversation by deep-copying messages from source up to fork point.

    The new conversation gets its own workspace (git worktree of source) and
    its own copies of messages and artifacts. The source conversation is not modified.
    """
    now = now_ms()
    new_conv_id = new_conversation_id()

    # ── Load source conversation + workspace ──────────────────────────
    async with get_local_db() as db:
        source_conv = await _require_conversation(db, source_conv_id)

        # Validate fork point message
        fork_msg = await _get_message(db, source_conv_id, fork_point_message_id)
        if fork_msg is None:
            raise ValueError("Message not found")
        if fork_msg.status == "streaming":
            raise ValueError("Cannot fork from a message that is still streaming")

        # Load source workspace
        ws_result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == source_conv_id)
        )
        source_ws = ws_result.scalar_one_or_none()
        if source_ws is None:
            raise ValueError(f"Source workspace not found for conversation: {source_conv_id}")

    # Determine source workspace effective cwd
    source_cwd = source_ws.root_path
    if source_ws.mode == "local" and source_ws.bound_path:
        source_cwd = source_ws.bound_path

    # ── Git init check ────────────────────────────────────────────────
    if not is_git_repo(source_cwd):
        if not confirm_git_init:
            raise GitInitRequiredError(source_cwd)
        try:
            await ensure_git_init(source_cwd)
        except Exception as exc:
            raise ValueError(f"Failed to initialize git in source directory: {exc}") from exc

    # ── Create fork worktree ──────────────────────────────────────────
    worktree_ref = await create_fork_worktree(source_cwd, new_conv_id)
    if worktree_ref is None:
        raise ValueError("Failed to create fork workspace")
    worktree_path = worktree_ref.path

    # ── Title deduplication ───────────────────────────────────────────
    base_title = f"{source_conv.title} (分支)"
    resolved_title = await _deduplicate_fork_title(base_title, source_conv_id)

    try:
        # ── Create new Conversation + Workspace ───────────────────────
        async with get_local_db() as db:
            new_conv = Conversation(
                id=new_conv_id,
                title=resolved_title,
                mode=source_conv.mode,
                archived=False,
                pinned_at=None,
                fs_write_approval_mode=source_conv.fs_write_approval_mode,
                rag_enabled=False,
                dispatch_mode=source_conv.dispatch_mode,
                parent_conversation_id=source_conv_id,
                fork_point_message_id=fork_point_message_id,
                created_at=now,
                updated_at=now,
            )
            new_conv.agent_ids_list = source_conv.agent_ids_list
            new_conv.pinned_message_ids_list = []
            new_conv.bookmarked_message_ids_list = []

            new_ws = Workspace(
                id=new_workspace_id(),
                conversation_id=new_conv_id,
                root_path=worktree_path,
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
            db.add(new_conv)
            db.add(new_ws)

            # ── Deep-copy messages ─────────────────────────────────────
            msg_result = await db.execute(
                select(Message).where(
                    Message.conversation_id == source_conv_id,
                    Message.hidden == False,  # noqa: E712
                    Message.created_at <= fork_msg.created_at,
                ).order_by(Message.created_at)
            )
            source_messages = msg_result.scalars().all()

            for src_msg in source_messages:
                new_msg = Message(
                    id=new_message_id(),
                    conversation_id=new_conv_id,
                    role=src_msg.role,
                    agent_id=src_msg.agent_id,
                    parts=src_msg.parts,
                    status=src_msg.status,
                    parent_message_id=src_msg.parent_message_id,
                    mentioned_agent_ids=src_msg.mentioned_agent_ids,
                    run_id=src_msg.run_id,
                    usage=src_msg.usage,
                    hidden=src_msg.hidden,
                    created_at=src_msg.created_at,
                )
                db.add(new_msg)

            # ── Deep-copy artifacts ────────────────────────────────────
            art_result = await db.execute(
                select(Artifact).where(
                    Artifact.conversation_id == source_conv_id,
                    Artifact.created_at <= fork_msg.created_at,
                ).order_by(Artifact.created_at)
            )
            source_artifacts = art_result.scalars().all()

            for src_art in source_artifacts:
                new_art = Artifact(
                    id=new_artifact_id(),
                    conversation_id=new_conv_id,
                    type=src_art.type,
                    title=src_art.title,
                    content=src_art.content,
                    version=1,
                    parent_artifact_id=None,
                    created_by_agent_id=src_art.created_by_agent_id,
                    created_at=src_art.created_at,
                )
                db.add(new_art)

            mode, bound_path, env_pref = await _ws_meta(db, new_conv_id)
            response = _conversation_response(new_conv, mode, bound_path, env_pref)

    except Exception:
        # Rollback: clean up worktree and any partial DB rows
        await cleanup_fork_worktree(worktree_path, f"fork/{new_conv_id}")
        raise

    return response


async def _deduplicate_fork_title(
    base_title: str,
    source_conv_id: str,
) -> str:
    """Find a unique fork title: base, base 2, base 3, ..."""
    async with get_local_db() as db:
        result = await db.execute(
            select(Conversation.title).where(
                Conversation.parent_conversation_id == source_conv_id,
            )
        )
        existing = {row[0] for row in result.all()}

    if base_title not in existing:
        return base_title
    n = 2
    while f"{base_title[:-1]} {n})" in existing:
        n += 1
    return f"{base_title[:-1]} {n})"


# ─── Conversion helpers ─────────────────────────────────────────────────────
def _conversation_response(
    conv: Conversation,
    ws_mode: str,
    ws_bound_path: str | None,
    ws_env_preference: str | None = None,
) -> ConversationResponse:
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        mode=conv.mode,
        agent_ids=conv.agent_ids_list,
        pinned_message_ids=conv.pinned_message_ids_list,
        bookmarked_message_ids=conv.bookmarked_message_ids_list,
        archived=conv.archived,
        pinned_at=conv.pinned_at,
        fs_write_approval_mode=conv.fs_write_approval_mode,
        summary=conv.summary,
        dispatch_mode=getattr(conv, "dispatch_mode", None) or "solo",
        parent_conversation_id=getattr(conv, "parent_conversation_id", None),
        fork_point_message_id=getattr(conv, "fork_point_message_id", None),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        workspace_mode=ws_mode,
        workspace_bound_path=ws_bound_path,
        workspace_env_preference=ws_env_preference,
    )


def _message_record(msg: Message) -> MessageRecord:
    return MessageRecord(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        agent_id=msg.agent_id,
        parts=msg.parts_list,
        status=msg.status,
        parent_message_id=msg.parent_message_id,
        mentioned_agent_ids=msg.mentioned_agent_ids_list,
        run_id=msg.run_id,
        usage=msg.usage_dict,
        hidden=getattr(msg, "hidden", False),
        created_at=msg.created_at,
    )


async def _ws_meta(
    db, conversation_id: str
) -> tuple[str, str | None, str | None]:
    """Return (mode, bound_path, env_preference) for a conversation's workspace."""
    result = await db.execute(
        select(Workspace.mode, Workspace.bound_path, Workspace.env_preference).where(
            Workspace.conversation_id == conversation_id
        )
    )
    row = result.first()
    if row is None:
        return ("sandbox", None, None)
    return (row[0], row[1], row[2])


def _default_title_for(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"群聊（{len(names)}）"


async def maybe_generate_summary(
    conversation_id: str,
    agent_id: str,
    user_message: str,
    agent_reply: str,
) -> None:
    """Generate a one-line summary (≤50 chars) after the first successful agent reply.

    Only runs when conversation.summary IS NULL (first reply only).
    Uses the agent's own model for generation via OpenAI-compatible chat completions.
    Fails silently — summary generation is best-effort and must not block the conversation.
    """
    from openai import AsyncOpenAI

    from app.adapters.custom_provider_client import resolve_custom_provider_client_config
    from app.infra.cache_helpers import get_agent_cached

    # Step 1: Quick check — is summary already set? Does the agent have a model?
    async with get_local_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.summary is not None:
            logger.info(
                "[maybe_generate_summary] Skipped: conv=%s summary_already_set=%s",
                conversation_id,
                conv.summary if conv else "N/A",
            )
            return

    agent = await get_agent_cached(agent_id)
    if agent is None:
        logger.info("[maybe_generate_summary] Skipped: agent not found")
        return
    if agent.adapter_name != "custom":
        logger.info(
            "[maybe_generate_summary] Skipped: agent is not SDK"
        )
        return

    async with get_local_db() as db:
        profile = (
            await db.execute(
                select(ModelProfile).where(
                    ModelProfile.is_default == True,  # noqa: E712
                ).limit(1)
            )
        ).scalar_one_or_none()
    if profile is None:
        logger.info(
            "[maybe_generate_summary] Skipped: no default ModelProfile"
        )
        return

    agent_name = agent.name
    model_provider = profile.provider
    model_id = profile.model_id
    api_key = profile.api_key
    api_base_url = profile.api_base_url

    logger.info(
        "[maybe_generate_summary] Calling LLM provider=%s model=%s",
        model_provider,
        model_id,
    )

    # Step 2: Build the summary prompt and call the LLM (outside DB session)
    user_snippet = user_message.strip()[:500]
    reply_snippet = agent_reply.strip()[:500]

    prompt = (
        "请用一句话（不超过 50 字）总结以下对话的核心话题和结论：\n\n"
        f"用户：{user_snippet}\n"
        f"{agent_name}：{reply_snippet}\n\n"
        "只输出摘要内容，不要加任何前缀或引号。"
    )

    try:
        config = resolve_custom_provider_client_config(
            model_provider,
            override_key=api_key,
            api_base_url=api_base_url,
        )
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=1,
        )
        response = await client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.3,
        )
        raw = response.choices[0].message.content
        summary = raw.strip()[:100] if raw else ""
        if not summary:
            logger.info("[maybe_generate_summary] LLM returned empty content")
    except Exception:
        logger.warning(
            "[maybe_generate_summary] Failed to generate summary for conversation %s",
            conversation_id,
            exc_info=True,
        )
        return

    if not summary:
        return

    # Step 3: Write with double-check for concurrency safety
    async with get_local_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.summary is not None:
            return  # Another concurrent call already wrote it
        conv.summary = summary
        conv.updated_at = now_ms()

    # Step 4: Publish SSE event so the frontend updates in real time
    event_bus.publish(
        SummaryUpdatedEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            summary=summary,
        ),
        user_id=None,
    )
    logger.info(
        "[maybe_generate_summary] Success! conv=%s summary=%s",
        conversation_id,
        summary,
    )


# ─── Create ─────────────────────────────────────────────────────────────────
async def create_conversation(
    *,
    mode: str,
    agent_ids: list[str],
    title: str | None = None,
    bound_path: str | None = None,
    # Deprecated: code intelligence now auto-enables for all local workspaces.
    code_intelligence_enabled: bool = False,
    dispatch_mode: str | None = None,
    user_id: str | None = None,
    fs_write_approval_mode: str = "review",
) -> ConversationResponse:
    """Create a conversation + its workspace, validating agents and the bound path."""
    if mode == "guide":
        # Guide mode: fixed to ag_guide_builtin, skip quantity validation.
        agent_ids = ["ag_guide_builtin"]
    else:
        if len(agent_ids) == 0:
            raise ValueError("At least one agent is required")
        if mode == "single" and len(agent_ids) != 1:
            raise ValueError("Single conversation requires exactly one agent")
        if mode == "group" and len(agent_ids) < 2:
            raise ValueError("Group conversation requires at least two agents")

    # Resolve / validate the optional local bound path (sandbox by default).
    # Guide mode: always sandbox, ignore bound_path.
    workspace_mode = "sandbox"
    resolved_bound_path: str | None = None
    if mode != "guide" and bound_path and bound_path.strip():
        raw = bound_path.strip()
        if IS_WINDOWS and not _WIN_ABS_RE.match(raw):
            raise ValueError(
                f"boundPath must start with a drive letter (e.g. D:\\projects\\foo) "
                f"on Windows: {raw}"
            )
        candidate = os.path.abspath(raw)
        if not os.path.isabs(candidate):
            raise ValueError("boundPath must be absolute")
        if not os.path.exists(candidate):
            raise ValueError(f"Path does not exist: {candidate}")
        if not os.path.isdir(candidate):
            raise ValueError(f"Not a directory: {candidate}")
        if not os.access(candidate, os.R_OK | os.W_OK):
            raise ValueError(f"Not readable/writable: {candidate}")
        if not is_path_safe(candidate):
            raise ValueError(
                f"Path is not allowed (system / sensitive directory): {candidate}"
            )
        workspace_mode = "local"
        resolved_bound_path = candidate

    now = now_ms()
    conversation_id = new_conversation_id()
    workspace_id = new_workspace_id()
    root_path = os.path.join(_workspaces_root(user_id), conversation_id)

    # Internal sandbox dir always exists (used for attachments etc.) regardless of mode.
    os.makedirs(root_path, exist_ok=True)

    # Sandbox-mode workspaces are auto-initialised as git repos so they support
    # true git worktree isolation for parallel dispatch tasks. Failure is
    # non-fatal: the workspace simply degrades to directory-copy worktrees.
    if workspace_mode == "sandbox":
        try:
            await ensure_git_init(root_path)
        except Exception as exc:  # noqa: BLE001 - never block conversation creation
            logger.warning("ensure_git_init failed for %s: %s", root_path, exc)

    async with get_local_db() as db:
        result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
        agents = result.scalars().all()
        if len(agents) != len(agent_ids):
            found = {a.id for a in agents}
            missing = [aid for aid in agent_ids if aid not in found]
            raise ValueError(f"Agents not found: {', '.join(missing)}")

        names_by_id = {a.id: a.name for a in agents}
        resolved_title = title or (
            "小A" if mode == "guide" else _default_title_for([names_by_id[a] for a in agent_ids])
        )

        conv = Conversation(
            id=conversation_id,
            title=resolved_title,
            mode=mode,
            archived=False,
            pinned_at=None,
            fs_write_approval_mode=fs_write_approval_mode,
            rag_enabled=False,
            dispatch_mode=dispatch_mode or ("orchestrated" if mode == "group" else "solo"),
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = agent_ids
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []

        workspace = Workspace(
            id=workspace_id,
            conversation_id=conversation_id,
            root_path=root_path,
            mode=workspace_mode,
            bound_path=resolved_bound_path,
            created_at=now,
        )
        db.add(conv)
        db.add(workspace)

    if workspace_mode == "local" and resolved_bound_path:
        from app.code_intelligence.service import schedule_workspace_enable

        logger.info(
            "[code-intelligence] auto-enable triggered for workspace %s "
            "(bound_path=%s, triggered_by=local_workspace_binding)",
            conversation_id,
            resolved_bound_path,
        )
        schedule_workspace_enable(
            workspace_root=Path(root_path),
            project_path=Path(resolved_bound_path).resolve(),
            download_approved=True,
        )

        # Fire-and-forget project env detection: if the bound project is
        # Python without a .venv, emit a WorkspaceEnvHintEvent so the frontend
        # can prompt the user. Must never block conversation creation.
        from app.services.workspace_env_service import detect_and_hint

        asyncio.create_task(
            detect_and_hint(conversation_id, user_id)
        )

    return ConversationResponse(
        id=conversation_id,
        title=resolved_title,
        mode=mode,
        agent_ids=agent_ids,
        pinned_message_ids=[],
        bookmarked_message_ids=[],
        archived=False,
        pinned_at=None,
        fs_write_approval_mode="review",
        rag_enabled=False,
        summary=None,
        parent_conversation_id=None,
        fork_point_message_id=None,
        created_at=now,
        updated_at=now,
        workspace_mode=workspace_mode,
        workspace_bound_path=resolved_bound_path,
        workspace_env_preference=None,
    )


# ─── List ───────────────────────────────────────────────────────────────────
async def list_conversations() -> list[ConversationResponse]:
    """Pinned first (by pinnedAt desc), then by updatedAt desc. Excludes guide-mode conversations."""
    async with get_local_db() as db:
        query = select(Conversation).where(Conversation.mode != "guide").order_by(
            Conversation.pinned_at.desc(), Conversation.updated_at.desc()
        )
        result = await db.execute(query)
        convs = result.scalars().all()
        if not convs:
            return []

        conv_ids = [c.id for c in convs]
        ws_result = await db.execute(
            select(
                Workspace.conversation_id,
                Workspace.mode,
                Workspace.bound_path,
                Workspace.env_preference,
            ).where(Workspace.conversation_id.in_(conv_ids))
        )
        ws_map = {row[0]: (row[1], row[2], row[3]) for row in ws_result.all()}

    out: list[ConversationResponse] = []
    for c in convs:
        mode, bound_path, env_pref = ws_map.get(c.id, ("sandbox", None, None))
        out.append(_conversation_response(c, mode, bound_path, env_pref))
    return out


async def get_conversation(conversation_id: str) -> ConversationResponse:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


# ─── Pin / archive / rename / approval-mode ─────────────────────────────────
async def toggle_pin_conversation(conversation_id: str) -> ConversationResponse:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.pinned_at = None if conv.pinned_at else now_ms()
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


async def toggle_archive_conversation(conversation_id: str) -> ConversationResponse:
    # Archive is a conversation-level meta op; it does NOT bump updated_at
    # (shouldn't float to the top of the list), matching toggle_pin.
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.archived = not conv.archived
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


async def rename_conversation(conversation_id: str, title: str) -> ConversationResponse:
    trimmed = title.strip()
    if not trimmed:
        raise ValueError("Title cannot be empty")
    if len(trimmed) > 100:
        raise ValueError("Title too long (max 100)")

    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.title = trimmed
        conv.updated_at = now_ms()
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


async def update_conversation_summary(
    conversation_id: str, summary: str | None
) -> ConversationResponse:
    """Update the conversation summary (set or clear)."""
    if summary is not None:
        trimmed = summary.strip()
        if len(trimmed) > 100:
            raise ValueError("Summary too long (max 100)")
    else:
        trimmed = None

    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.summary = trimmed
        conv.updated_at = now_ms()
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


async def set_conversation_approval_mode(
    conversation_id: str, mode: str
) -> ConversationResponse:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.fs_write_approval_mode = mode
        conv.updated_at = now_ms()
        ws_mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, ws_mode, bound_path, env_pref)



async def set_dispatch_mode(
    conversation_id: str, dispatch_mode: str
) -> ConversationResponse:
    """Update conversation dispatch_mode (solo | orchestrated)."""
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv.dispatch_mode = dispatch_mode
        conv.updated_at = now_ms()
        ws_mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, ws_mode, bound_path, env_pref)


# ─── Bookmark / pin a message ───────────────────────────────────────────────
async def toggle_bookmarked_message(
    conversation_id: str, message_id: str
) -> dict:
    """UI bookmark toggle (navigation only; not injected into the LLM context)."""
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        await _require_message_in_conversation(db, conversation_id, message_id)

        current = conv.bookmarked_message_ids_list
        is_bookmarked = message_id in current
        nxt = [mid for mid in current if mid != message_id] if is_bookmarked else [
            *current,
            message_id,
        ]
        conv.bookmarked_message_ids_list = nxt
        conv.updated_at = now_ms()
        return {"bookmarkedMessageIds": nxt, "bookmarked": not is_bookmarked}


async def toggle_pinned_message(conversation_id: str, message_id: str) -> dict:
    """Pin toggle: pinned messages are injected into the LLM's long-term context.

    Differs from bookmarking: capped at PIN_LIMIT_PER_CONVERSATION and does NOT
    bump updated_at (pinning isn't conversation "activity").
    """
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        await _require_message_in_conversation(db, conversation_id, message_id)

        current = conv.pinned_message_ids_list
        is_pinned = message_id in current
        if not is_pinned and len(current) >= PIN_LIMIT_PER_CONVERSATION:
            raise ValueError("PIN_LIMIT_EXCEEDED")
        nxt = [mid for mid in current if mid != message_id] if is_pinned else [
            *current,
            message_id,
        ]
        conv.pinned_message_ids_list = nxt
        return {"pinnedMessageIds": nxt, "pinned": not is_pinned}


# ─── Add agents ─────────────────────────────────────────────────────────────
async def add_agents_to_conversation(
    conversation_id: str, agent_ids: list[str]
) -> ConversationResponse:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)

        result = await db.execute(select(Agent.id).where(Agent.id.in_(agent_ids)))
        found = {row[0] for row in result.all()}
        if len(found) != len(agent_ids):
            missing = [aid for aid in agent_ids if aid not in found]
            raise ValueError(f"Agents not found: {', '.join(missing)}")

        merged = list(dict.fromkeys([*conv.agent_ids_list, *agent_ids]))
        conv.agent_ids_list = merged
        conv.mode = "group" if len(merged) >= 2 else "single"
        conv.updated_at = now_ms()
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        return _conversation_response(conv, mode, bound_path, env_pref)


# ─── List messages ──────────────────────────────────────────────────────────
async def list_messages(conversation_id: str) -> list[MessageRecord]:
    async with get_local_db() as db:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return [_message_record(m) for m in result.scalars().all()]


# ─── Delete ─────────────────────────────────────────────────────────────────
async def _delete_conversation_unlocked(conversation_id: str) -> None:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        if conv.mode == "guide":
            raise ValueError("Guide conversations cannot be deleted")

        is_fork = getattr(conv, "parent_conversation_id", None) is not None

        ws_result = await db.execute(
            select(Workspace.root_path).where(
                Workspace.conversation_id == conversation_id
            )
        )
        ws_row = ws_result.first()
        root_path = ws_row[0] if ws_row else None

        # FK ON DELETE CASCADE (enabled via PRAGMA foreign_keys=ON) clears
        # messages / artifacts / workspaces / attachments / agent_runs / summaries.
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))

    if root_path:
        if is_fork:
            # Forked conversations have a git worktree that needs branch cleanup.
            try:
                await cleanup_fork_worktree(root_path, f"fork/{conversation_id}")
            except Exception as err:  # noqa: BLE001 - worktree cleanup is best-effort
                logger.warning(
                    "[delete_conversation] cleanup_fork_worktree failed for %s: %s",
                    root_path,
                    err,
                )
        else:
            try:
                await _rmdir_with_retry(root_path)
            except OSError as err:  # noqa: BLE001 - workspace removal is best-effort
                logger.warning(
                    "[delete_conversation] failed to remove workspace dir %s: %s",
                    root_path,
                    err,
                )

    clear_claude_code_session(conversation_id)
    clear_codex_session(conversation_id)

    from app.infra.cache_helpers import invalidate_workspace_cache
    await invalidate_workspace_cache(conversation_id)


async def delete_conversation(conversation_id: str) -> None:
    async with _conv_locks.lock_for(conversation_id):
        return await _delete_conversation_unlocked(conversation_id)


async def _rmdir_with_retry(target: str) -> None:
    """Remove a directory, retrying on transient Windows lock errors.

    On Windows EBUSY/EPERM/ENOTEMPTY (process locks, AV scans, leftover
    .git/index.lock) usually clear within a couple of backoff retries.
    """
    for attempt in range(1, 4):
        try:
            shutil.rmtree(target)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 3:
                raise
            await asyncio.sleep(0.1 * (3 ** (attempt - 1)))


# ─── Clear history ──────────────────────────────────────────────────────────
async def _clear_conversation_history_unlocked(
    conversation_id: str,
) -> ClearConversationHistoryResult:
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)

        active_result = await db.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.status.in_(["queued", "running"]),
            )
        )
        if active_result.first() is not None:
            raise ValueError(
                "Cannot clear conversation history while agent runs are active"
            )

        msg_count = await _count(db, Message, Message.conversation_id == conversation_id)
        run_count = await _count(
            db, AgentRun, AgentRun.conversation_id == conversation_id
        )
        summary_count = await _count(
            db, ContextSummary, ContextSummary.conversation_id == conversation_id
        )

        now = now_ms()
        await db.execute(
            delete(ContextSummary).where(
                ContextSummary.conversation_id == conversation_id
            )
        )
        await db.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        await db.execute(
            delete(AgentRun).where(AgentRun.conversation_id == conversation_id)
        )
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        conv.updated_at = now
        mode, bound_path, env_pref = await _ws_meta(db, conversation_id)
        response = _conversation_response(conv, mode, bound_path, env_pref)

    clear_claude_code_session(conversation_id)
    clear_codex_session(conversation_id)

    return ClearConversationHistoryResult(
        conversation=response,
        deleted_message_count=msg_count,
        deleted_run_count=run_count,
        deleted_summary_count=summary_count,
    )


async def clear_conversation_history(
    conversation_id: str,
) -> ClearConversationHistoryResult:
    async with _conv_locks.lock_for(conversation_id):
        return await _clear_conversation_history_unlocked(conversation_id)


# ─── Send message ───────────────────────────────────────────────────────────
async def _send_message_unlocked(
    *,
    conversation_id: str,
    content: str,
    mentioned_agent_ids: list[str] | None = None,
    parent_message_id: str | None = None,
    attachment_ids: list[str] | None = None,
    model_profile_id: str | None = None,
) -> SendMessageResult:
    mentioned_agent_ids = mentioned_agent_ids or []
    attachment_ids = attachment_ids or []

    now = now_ms()
    message_id = new_message_id()

    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv_agent_ids = conv.agent_ids_list
        conv_mode = conv.mode
        conv_user_id = None

        parts: list[dict] = []
        if content and content.strip():
            parts.append({"type": "text", "content": content})

        if attachment_ids:
            att_result = await db.execute(
                select(Attachment).where(Attachment.id.in_(attachment_ids))
            )
            for r in att_result.scalars().all():
                if r.conversation_id != conversation_id:
                    continue  # don't allow referencing another conversation's attachment
                kind = "image_attachment" if r.kind == "image" else "file_attachment"
                parts.append(
                    {
                        "type": kind,
                        "attachmentId": r.id,
                        "fileName": r.file_name,
                        "size": r.size,
                        "mimeType": r.mime_type,
                    }
                )

        msg = Message(
            id=message_id,
            conversation_id=conversation_id,
            role="user",
            agent_id=None,
            status="complete",
            parent_message_id=parent_message_id,
            run_id=None,
            created_at=now,
        )
        msg.parts_list = parts
        msg.mentioned_agent_ids_list = mentioned_agent_ids
        db.add(msg)
        conv.updated_at = now

    # Broadcast the new user message so other connected clients insert it live.
    # The sender reconciles via optimistic update + POST return; idempotent by id.
    event_bus.publish(
        MessageAddedEvent(
            conversation_id=conversation_id,
            timestamp=now,
            message=MessageRecord(
                id=message_id,
                conversation_id=conversation_id,
                role="user",
                agent_id=None,
                parts=parts,
                status="complete",
                parent_message_id=parent_message_id,
                mentioned_agent_ids=mentioned_agent_ids,
                run_id=None,
                usage=None,
                created_at=now,
            ),
        ),
        user_id=None,
    )

    # Bare deploy command (only when it's a lone text message): handle inline.
    deploy_intent = None
    if (
        len(parts) == 1
        and not parent_message_id
        and not mentioned_agent_ids
        and not attachment_ids
    ):
        deploy_intent = deploy_command_service.parse_deploy_command(content)
    if deploy_intent is not None:
        deploy = await deploy_command_service.handle_deploy_command(
            conversation_id=conversation_id,
            artifact_id=deploy_intent.artifact_id,
            after_created_at=now,
        )
        return SendMessageResult(
            message_id=message_id, run_ids=[], messages=[deploy.message], deploy=deploy
        )

    # Decide responders, then kick off a run per responder.
    async with get_local_db() as db:
        agents_result = await db.execute(
            select(Agent.id, Agent.is_orchestrator).where(Agent.id.in_(conv_agent_ids))
        )
        agent_infos = [(row[0], row[1]) for row in agents_result.all()]

    responders = _decide_responders(
        conv_mode, conv_agent_ids, mentioned_agent_ids, agent_infos
    )

    # Group chat with no Orchestrator and no @mention: emit a system message.
    if not responders and conv_mode == "group" and not mentioned_agent_ids:
        sys_msg_id = new_message_id()
        sys_parts = [
            {
                "type": "text",
                "content": "此群聊没有协调者。请使用 @Agent名 指定回复对象。",
            }
        ]
        sys_now = now_ms()

        async with get_local_db() as db:
            sys_msg = Message(
                id=sys_msg_id,
                conversation_id=conversation_id,
                role="agent",
                agent_id=None,
                status="complete",
                parent_message_id=message_id,
                run_id=None,
                created_at=sys_now,
            )
            sys_msg.parts_list = sys_parts
            sys_msg.mentioned_agent_ids_list = []
            db.add(sys_msg)

        sys_record = MessageRecord(
            id=sys_msg_id,
            conversation_id=conversation_id,
            role="agent",
            agent_id=None,
            parts=sys_parts,
            status="complete",
            parent_message_id=message_id,
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
        return SendMessageResult(
            message_id=message_id, run_ids=[], messages=[sys_record]
        )

    # Check if there are active (running or queued) runs for this conversation.
    # If so, new runs are queued instead of started immediately — the queue
    # is drained automatically when each run finishes (see agent_runner.finalize).
    async with get_local_db() as db:
        active_result = await db.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.status.in_(["running", "queued"]),
                AgentRun.parent_run_id.is_(None),
            )
        )
        has_active_runs = active_result.first() is not None

    run_ids: list[str] = []
    if has_active_runs:
        from app.services.agent_runner import enqueue_run
        for agent_id in responders:
            run_id = enqueue_run(
                agent_id=agent_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                user_id=conv_user_id,
                model_profile_id=model_profile_id,
            )
            run_ids.append(run_id)
    else:
        runner = get_agent_runner()
        for agent_id in responders:
            handle = runner.run(
                agent_id=agent_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                user_id=conv_user_id,
                model_profile_id=model_profile_id,
            )
            run_ids.append(handle.run_id)

    return SendMessageResult(message_id=message_id, run_ids=run_ids)


async def send_message(
    *,
    conversation_id: str,
    content: str,
    mentioned_agent_ids: list[str] | None = None,
    parent_message_id: str | None = None,
    attachment_ids: list[str] | None = None,
    model_profile_id: str | None = None,
) -> SendMessageResult:
    async with _conv_locks.lock_for(conversation_id):
        return await _send_message_unlocked(
            conversation_id=conversation_id,
            content=content,
            mentioned_agent_ids=mentioned_agent_ids,
            parent_message_id=parent_message_id,
            attachment_ids=attachment_ids,
            model_profile_id=model_profile_id,
        )


def _decide_responders(
    mode: str,
    agent_ids: list[str],
    mentions: list[str],
    agent_infos: list[tuple[str, bool]],
) -> list[str]:
    # Single chat: hand it to that one agent.
    if mode == "single":
        return agent_ids
    # Group with @mentions: each mentioned agent responds.
    if mentions:
        return [mid for mid in mentions if mid in agent_ids]
    # Group with no @mention: hand it to the group's Orchestrator (if any).
    orchestrator = next((aid for aid, is_orch in agent_infos if is_orch), None)
    return [orchestrator] if orchestrator else []


# ─── Revise pending dispatch plan ───────────────────────────────────────────
async def _revise_dispatch_plan_unlocked(
    *, conversation_id: str, plan_id: str, feedback: str
) -> dict:
    """Land the user's NL feedback as a user message + broadcast, then re-plan.

    Does not start a new run — the awaiting Orchestrator run picks the feedback
    up and emits a fresh plan.
    """
    pending = pending_dispatch_plans.get(plan_id)
    if pending is None or pending.conversation_id != conversation_id:
        return {"ok": False, "error": "Pending dispatch plan not found"}

    now = now_ms()
    message_id = new_message_id()
    parts = [{"type": "text", "content": feedback}]

    async with get_local_db() as db:
        msg = Message(
            id=message_id,
            conversation_id=conversation_id,
            role="user",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            run_id=None,
            created_at=now,
        )
        msg.parts_list = parts
        msg.mentioned_agent_ids_list = []
        db.add(msg)
        conv = await _require_conversation(db, conversation_id)
        conv.updated_at = now

    event_bus.publish(
        MessageAddedEvent(
            conversation_id=conversation_id,
            timestamp=now,
            message=MessageRecord(
                id=message_id,
                conversation_id=conversation_id,
                role="user",
                agent_id=None,
                parts=parts,
                status="complete",
                parent_message_id=None,
                mentioned_agent_ids=[],
                run_id=None,
                usage=None,
                created_at=now,
            ),
        ),
        user_id=None,
    )

    ok = pending_dispatch_plans.revise(plan_id, feedback)
    return {"ok": True} if ok else {"ok": False, "error": "Failed to revise pending dispatch plan"}


async def revise_dispatch_plan(
    *, conversation_id: str, plan_id: str, feedback: str
) -> dict:
    async with _conv_locks.lock_for(conversation_id):
        return await _revise_dispatch_plan_unlocked(
            conversation_id=conversation_id, plan_id=plan_id, feedback=feedback,
        )


# ─── Abort run ──────────────────────────────────────────────────────────────
async def abort_run(run_id: str) -> bool:
    return get_agent_runner().abort(run_id)


# ─── Withdraw latest user message ───────────────────────────────────────────
async def _withdraw_latest_user_message_unlocked(
    conversation_id: str, message_id: str
) -> WithdrawResult:
    """Withdraw the latest user message plus everything it triggered downstream.

    1. assert ``message_id`` is the conversation's latest user message
    2. abort running runs (fire-and-forget)
    3. wait 500 ms so AgentRunner.finalize flushes (catches late msg_err_* rows)
    4. time-window delete messages / artifacts / runs at created_at >= the user msg
    """
    async with get_local_db() as db:
        msg = await _get_message(db, conversation_id, message_id)
        if msg is None:
            raise ValueError(f"Message not found: {message_id}")
        if msg.role != "user":
            raise ValueError("Only user messages can be withdrawn")
        msg_created_at = msg.created_at

        await _require_conversation(db, conversation_id)
        conv_user_id = None

        latest_user = await _latest_user_message(db, conversation_id)
        if latest_user is None or latest_user.id != message_id:
            raise ValueError("Only the latest user message can be withdrawn")

        runs_result = await db.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.started_at >= msg_created_at,
                AgentRun.status.in_(["running", "queued"]),
            )
        )
        run_ids_to_abort = [row[0] for row in runs_result.all()]

    if run_ids_to_abort:
        runner = get_agent_runner()
        for rid in run_ids_to_abort:
            runner.abort(rid)
        # Let finalize() flush its error visualisation into the time window.
        await asyncio.sleep(0.5)

    # Withdrawing diverges DB from the SDK's cached "user→reply" pair; reset it.
    clear_claude_code_session(conversation_id)
    clear_codex_session(conversation_id)

    deleted_message_ids, deleted_artifact_ids = await _delete_from_timewindow(
        conversation_id, msg_created_at, inclusive=True
    )

    event_bus.publish(
        MessageRemovedEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            message_ids=deleted_message_ids,
            artifact_ids=deleted_artifact_ids,
        ),
        user_id=conv_user_id,
    )

    return WithdrawResult(
        deleted_message_ids=deleted_message_ids,
        deleted_artifact_ids=deleted_artifact_ids,
    )


async def withdraw_latest_user_message(
    conversation_id: str, message_id: str
) -> WithdrawResult:
    async with _conv_locks.lock_for(conversation_id):
        return await _withdraw_latest_user_message_unlocked(conversation_id, message_id)


# ─── Regenerate latest response ─────────────────────────────────────────────
async def _regenerate_latest_response_unlocked(conversation_id: str) -> RegenerateResult:
    """Delete everything after the latest user message and re-run responders for it."""
    async with get_local_db() as db:
        conv = await _require_conversation(db, conversation_id)
        conv_agent_ids = conv.agent_ids_list
        conv_mode = conv.mode
        conv_user_id = None

        latest_user = await _latest_user_message(db, conversation_id)
        if latest_user is None:
            raise ValueError("No user message to regenerate from")
        trigger_id = latest_user.id
        trigger_created_at = latest_user.created_at
        trigger_mentions = latest_user.mentioned_agent_ids_list

        runs_result = await db.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.started_at > trigger_created_at,
                AgentRun.status.in_(["running", "queued"]),
            )
        )
        run_ids_to_abort = [row[0] for row in runs_result.all()]

    if run_ids_to_abort:
        runner = get_agent_runner()
        for rid in run_ids_to_abort:
            runner.abort(rid)
        await asyncio.sleep(0.5)

    clear_claude_code_session(conversation_id)
    clear_codex_session(conversation_id)

    deleted_message_ids, deleted_artifact_ids = await _delete_from_timewindow(
        conversation_id, trigger_created_at, inclusive=False
    )

    # Broadcast removals BEFORE starting the new run so clients remove-then-add.
    event_bus.publish(
        MessageRemovedEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            message_ids=deleted_message_ids,
            artifact_ids=deleted_artifact_ids,
        ),
        user_id=conv_user_id,
    )

    async with get_local_db() as db:
        agents_result = await db.execute(
            select(Agent.id, Agent.is_orchestrator).where(Agent.id.in_(conv_agent_ids))
        )
        agent_infos = [(row[0], row[1]) for row in agents_result.all()]

    responders = _decide_responders(
        conv_mode, conv_agent_ids, trigger_mentions, agent_infos
    )

    runner = get_agent_runner()
    run_ids: list[str] = []
    for agent_id in responders:
        handle = runner.run(
            agent_id=agent_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_id,
            user_id=conv_user_id,
        )
        run_ids.append(handle.run_id)

    return RegenerateResult(
        deleted_message_ids=deleted_message_ids,
        deleted_artifact_ids=deleted_artifact_ids,
        trigger_message_id=trigger_id,
        run_ids=run_ids,
    )


async def regenerate_latest_response(conversation_id: str) -> RegenerateResult:
    async with _conv_locks.lock_for(conversation_id):
        return await _regenerate_latest_response_unlocked(conversation_id)


# ─── Edit & resend latest user message ──────────────────────────────────────
async def edit_and_resend_latest_user_message(
    conversation_id: str, message_id: str, new_content: str
) -> EditAndResendResult:
    """Withdraw the latest user message, then resend with new content.

    Preserves the original mentions / parent / attachments.
    """
    trimmed = new_content.strip()
    if not trimmed:
        raise ValueError("Content cannot be empty")

    async with get_local_db() as db:
        original = await _get_message(db, conversation_id, message_id)
        if original is None:
            raise ValueError(f"Message not found: {message_id}")
        if original.role != "user":
            raise ValueError("Only user messages can be edited")
        original_mentions = original.mentioned_agent_ids_list
        original_parent = original.parent_message_id
        original_attachment_ids = [
            p["attachmentId"]
            for p in original.parts_list
            if p.get("type") in ("image_attachment", "file_attachment")
        ]

    # Acquire the conversation lock once for both withdraw + send to avoid
    # a self-deadlock (asyncio.Lock is non-reentrant) and to ensure atomicity.
    async with _conv_locks.lock_for(conversation_id):
        withdrawn = await _withdraw_latest_user_message_unlocked(conversation_id, message_id)

        sent = await _send_message_unlocked(
            conversation_id=conversation_id,
            content=trimmed,
            mentioned_agent_ids=original_mentions,
            parent_message_id=original_parent,
            attachment_ids=original_attachment_ids or None,
        )

    async with get_local_db() as db:
        result = await db.execute(select(Message).where(Message.id == sent.message_id))
        new_msg = result.scalar_one_or_none()
        if new_msg is None:
            raise ValueError("New message disappeared after insert")
        new_record = _message_record(new_msg)

    return EditAndResendResult(
        deleted_message_ids=withdrawn.deleted_message_ids,
        deleted_artifact_ids=withdrawn.deleted_artifact_ids,
        new_message=new_record,
        run_ids=sent.run_ids,
    )


# ─── Internal DB helpers ────────────────────────────────────────────────────
async def _require_conversation(db, conversation_id: str) -> Conversation:
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise ValueError(f"Conversation not found: {conversation_id}")
    return conv


async def _get_message(db, conversation_id: str, message_id: str) -> Message | None:
    result = await db.execute(
        select(Message).where(
            Message.id == message_id, Message.conversation_id == conversation_id
        )
    )
    return result.scalar_one_or_none()


async def _require_message_in_conversation(
    db, conversation_id: str, message_id: str
) -> Message:
    msg = await _get_message(db, conversation_id, message_id)
    if msg is None:
        raise ValueError(f"Message not found in conversation: {message_id}")
    return msg


async def _latest_user_message(db, conversation_id: str) -> Message | None:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _count(db, model, condition) -> int:
    from sqlalchemy import func

    result = await db.execute(select(func.count()).select_from(model).where(condition))
    return int(result.scalar_one())


async def _delete_from_timewindow(
    conversation_id: str, boundary_created_at: int, *, inclusive: bool
) -> tuple[list[str], list[str]]:
    """Delete messages/artifacts/runs from a time boundary; return removed ids.

    ``inclusive`` deletes at created_at >= boundary (withdraw); otherwise
    created_at > boundary (regenerate, keeping the trigger user message).
    """
    from app.db.models import Artifact

    async with get_local_db() as db:
        if inclusive:
            msg_cond = Message.created_at >= boundary_created_at
            run_cond = AgentRun.started_at >= boundary_created_at
        else:
            msg_cond = Message.created_at > boundary_created_at
            run_cond = AgentRun.started_at > boundary_created_at

        msgs_result = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id, msg_cond
            )
        )
        msgs = msgs_result.scalars().all()
        message_ids = [m.id for m in msgs]

        artifact_ids: set[str] = set()
        for m in msgs:
            for p in m.parts_list:
                if p.get("type") == "artifact_ref":
                    artifact_ids.add(p["artifactId"])

        runs_result = await db.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id, run_cond
            )
        )
        run_ids = [row[0] for row in runs_result.all()]

        if message_ids:
            await db.execute(delete(Message).where(Message.id.in_(message_ids)))
        if artifact_ids:
            await db.execute(
                delete(Artifact).where(Artifact.id.in_(list(artifact_ids)))
            )
        if run_ids:
            await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))

    return message_ids, list(artifact_ids)
