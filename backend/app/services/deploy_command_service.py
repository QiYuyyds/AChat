"""Deploy slash-command handling.

Port of src/server/deploy-command-service.ts.

A bare ``/deploy`` (or ``部署`` / ``发布`` / ``上线``) message is intercepted in
``conversation_service.send_message`` instead of being routed to an agent. We
resolve a deployable web_app artifact (or a local static build dir) and insert a
system message describing the result.

The actual deployment mechanics (``deploy_artifact`` / ``deploy_workspace``) are
tools that arrive in 阶段 3. To avoid pulling that forward, those two steps go
through a small handler registry (:func:`set_deploy_handlers`). Everything else
— command parsing, candidate listing, the selection / no-candidate system
messages — is fully implemented here now. Until the handlers are registered, an
explicit ``/deploy`` falls back to a "not available yet" system message.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Artifact, Conversation, Message, Workspace
from app.schemas.events import MessageRecord
from app.schemas.messages import DeployCandidateRecord, DeployStatusRecord
from app.utils.clock import now_ms
from app.utils.ids import new_message_id
from app.utils.workspace_utils import get_effective_cwd

logger = logging.getLogger(__name__)

DEPLOY_COMMAND_RE = re.compile(
    r"^(?:/deploy|部署|发布|上线)(?:\s+(art_[0-9A-Za-z]+))?$",
    re.IGNORECASE,
)

NO_CANDIDATES_TEXT = (
    "当前会话还没有可部署的网页产物，也没有找到常见的本地静态输出目录"
    "（dist/build/out/client/dist）。请先让 Agent 生成 web_app 产物，或构建本地项目后再部署。"
)
DEPLOY_UNAVAILABLE_TEXT = "部署功能将在工具阶段（阶段 3）接入后可用。"

# Local static build dirs probed when there is no web_app artifact.
WORKSPACE_DEPLOY_CANDIDATES = [
    "dist",
    "build",
    "out",
    "public",
    "client/dist",
    "client/build",
    "client/out",
    "apps/web/dist",
    "apps/web/build",
    "apps/web/out",
]


# ─── Deferred deploy handlers (registered by phase 3 tools) ──────────────────
DeployArtifactFn = Callable[[str, str], Awaitable[DeployStatusRecord]]
DeployWorkspaceFn = Callable[[str, dict[str, str]], Awaitable[DeployStatusRecord]]

_deploy_artifact_fn: DeployArtifactFn | None = None
_deploy_workspace_fn: DeployWorkspaceFn | None = None


def set_deploy_handlers(
    *,
    artifact_fn: DeployArtifactFn | None = None,
    workspace_fn: DeployWorkspaceFn | None = None,
) -> None:
    """Register the concrete deploy tools (called from phase 3 wiring)."""
    global _deploy_artifact_fn, _deploy_workspace_fn
    if artifact_fn is not None:
        _deploy_artifact_fn = artifact_fn
    if workspace_fn is not None:
        _deploy_workspace_fn = workspace_fn


# ─── Results ────────────────────────────────────────────────────────────────
@dataclass
class DeployCommandIntent:
    artifact_id: str | None = None


@dataclass
class _DeployDecision:
    kind: str  # 'no_candidates' | 'deploy' | 'select'
    artifact_id: str | None = None
    candidates: list[DeployCandidateRecord] | None = None


@dataclass
class DeployCommandResult:
    kind: str  # 'no_candidates' | 'candidate_selection' | 'deployed'
    message: MessageRecord
    candidates: list[DeployCandidateRecord] | None = None
    deployment: DeployStatusRecord | None = None


# ─── Parsing / decision (pure) ──────────────────────────────────────────────
def parse_deploy_command(content: str) -> DeployCommandIntent | None:
    """Return an intent if ``content`` is a bare deploy command, else None."""
    match = DEPLOY_COMMAND_RE.match(content.strip())
    if not match:
        return None
    return DeployCommandIntent(artifact_id=match.group(1) or None)


def decide_deploy_command(
    candidates: list[DeployCandidateRecord], artifact_id: str | None
) -> _DeployDecision:
    if artifact_id:
        return _DeployDecision(kind="deploy", artifact_id=artifact_id)
    if not candidates:
        return _DeployDecision(kind="no_candidates")
    if len(candidates) == 1:
        return _DeployDecision(kind="deploy", artifact_id=candidates[0].artifact_id)
    return _DeployDecision(kind="select", candidates=candidates)


# ─── DB-backed helpers ──────────────────────────────────────────────────────
async def list_deploy_candidates(conversation_id: str) -> list[DeployCandidateRecord]:
    """All web_app artifacts in a conversation, newest first."""
    async with get_db() as db:
        result = await db.execute(
            select(Artifact)
            .where(Artifact.conversation_id == conversation_id, Artifact.type == "web_app")
            .order_by(Artifact.created_at.desc())
        )
        rows = result.scalars().all()
    return [
        DeployCandidateRecord(
            artifact_id=row.id,
            title=row.title,
            version=row.version,
            created_by_agent_id=row.created_by_agent_id,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def handle_deploy_command(
    *,
    conversation_id: str,
    artifact_id: str | None = None,
    after_created_at: int | None = None,
) -> DeployCommandResult:
    """Resolve a deploy command into a deployment / selection / no-candidate result."""
    candidates = [] if artifact_id else await list_deploy_candidates(conversation_id)
    decision = decide_deploy_command(candidates, artifact_id)

    if decision.kind == "no_candidates":
        workspace_deploy = await _deploy_first_workspace_candidate(
            conversation_id, after_created_at
        )
        if workspace_deploy is not None:
            return workspace_deploy
        message = await _insert_system_message(
            conversation_id,
            [{"type": "text", "content": NO_CANDIDATES_TEXT}],
            after_created_at,
        )
        return DeployCommandResult(kind="no_candidates", message=message, candidates=[])

    if decision.kind == "select":
        assert decision.candidates is not None
        message = await _insert_system_message(
            conversation_id,
            [
                {
                    "type": "deploy_candidates",
                    "candidates": [c.model_dump(by_alias=True) for c in decision.candidates],
                }
            ],
            after_created_at,
        )
        return DeployCommandResult(
            kind="candidate_selection", message=message, candidates=decision.candidates
        )

    # decision.kind == "deploy"
    assert decision.artifact_id is not None
    return await _deploy_selected_artifact(
        conversation_id, decision.artifact_id, after_created_at
    )


async def _deploy_first_workspace_candidate(
    conversation_id: str, after_created_at: int | None
) -> DeployCommandResult | None:
    if _deploy_workspace_fn is None:
        return None
    candidate = await _find_workspace_deploy_candidate(conversation_id)
    if candidate is None:
        return None
    deployment = await _deploy_workspace_fn(conversation_id, candidate)
    message = await _insert_system_message(
        conversation_id,
        [{"type": "deploy_status", "deployment": deployment.model_dump(by_alias=True)}],
        after_created_at,
    )
    return DeployCommandResult(kind="deployed", message=message, deployment=deployment)


async def _find_workspace_deploy_candidate(
    conversation_id: str,
) -> dict[str, str] | None:
    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        workspace = result.scalar_one_or_none()
    if workspace is None:
        return None
    cwd = get_effective_cwd(workspace)
    for rel_path in WORKSPACE_DEPLOY_CANDIDATES:
        abs_path = os.path.abspath(os.path.join(cwd, rel_path))
        if not os.path.isdir(abs_path):
            continue
        if not os.path.exists(os.path.join(abs_path, "index.html")):
            continue
        return {"path": rel_path, "title": f"Workspace {rel_path}"}
    return None


async def _deploy_selected_artifact(
    conversation_id: str, artifact_id: str, after_created_at: int | None
) -> DeployCommandResult:
    if _deploy_artifact_fn is None:
        # Phase 3 tool not wired yet — degrade gracefully with a clear message.
        logger.warning(
            "deploy requested for %s but deploy_artifact handler is unregistered "
            "(phase 3 pending)",
            artifact_id,
        )
        message = await _insert_system_message(
            conversation_id,
            [{"type": "text", "content": DEPLOY_UNAVAILABLE_TEXT}],
            after_created_at,
        )
        return DeployCommandResult(kind="no_candidates", message=message, candidates=[])

    deployment = await _deploy_artifact_fn(conversation_id, artifact_id)
    message = await _insert_system_message(
        conversation_id,
        [{"type": "deploy_status", "deployment": deployment.model_dump(by_alias=True)}],
        after_created_at,
    )
    return DeployCommandResult(kind="deployed", message=message, deployment=deployment)


async def _insert_system_message(
    conversation_id: str,
    parts: list[dict[str, Any]],
    after_created_at: int | None,
) -> MessageRecord:
    """Insert a role='system' message and bump the conversation's updated_at.

    Matches the TS helper: no event is broadcast — the sender reconciles via the
    POST response, other clients pick it up on their next REST fetch.
    """
    # Ensure the system message sorts after the triggering user message.
    created_at = max(now_ms(), (after_created_at or 0) + 1)
    message_id = new_message_id()

    async with get_db() as db:
        msg = Message(
            id=message_id,
            conversation_id=conversation_id,
            role="system",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            run_id=None,
            created_at=created_at,
        )
        msg.parts_list = parts
        msg.mentioned_agent_ids_list = []
        db.add(msg)

        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = conv_result.scalar_one_or_none()
        if conv is not None:
            conv.updated_at = created_at

    return MessageRecord(
        id=message_id,
        conversation_id=conversation_id,
        role="system",
        agent_id=None,
        parts=parts,
        status="complete",
        parent_message_id=None,
        mentioned_agent_ids=[],
        run_id=None,
        usage=None,
        created_at=created_at,
    )
