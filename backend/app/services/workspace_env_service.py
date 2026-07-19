"""Workspace environment detection and venv creation service.

See ``specs/workspace-env-isolation``. This service is invoked after a
workspace is bound to a local directory:

1. ``detect_and_hint`` — asynchronously detect the project language and
   whether a Python ``.venv`` exists. If the project is Python and no venv
   is present AND the user hasn't already made a choice, publish a
   ``WorkspaceEnvHintEvent`` so the frontend can prompt the user.
2. ``create_project_venv`` — run ``python -m venv --upgrade-deps .venv`` in
   the bound path, emitting ``WorkspaceEnvStatusEvent`` progress / result.
   On success the project venv is picked up by the bash tool's
   ``detect_project_venv`` on the next invocation (no caching).

All detection is best-effort: failures are logged at warning level and
degrade silently — they MUST NOT block workspace creation or conversation
startup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Workspace
from app.infra.cache_helpers import invalidate_workspace_cache
from app.schemas.events import WorkspaceEnvHintEvent, WorkspaceEnvStatusEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.env_isolation import detect_project_venv
from app.utils.platform import IS_WINDOWS

logger = logging.getLogger(__name__)


# ─── Marker files ───────────────────────────────────────────────────────────

_PYTHON_MARKERS = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")
_NODE_MARKERS = ("package.json",)
_JAVA_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts")
_GO_MARKERS = ("go.mod",)

# A closed set of language strings the frontend knows how to render.
SUPPORTED_LANGUAGES = ("python", "nodejs", "java", "go", "unknown")


@dataclass
class ProjectEnvInfo:
    """Result of project environment detection.

    ``language`` is one of :data:`SUPPORTED_LANGUAGES`. ``venv_present`` is
    only meaningful for Python (other languages don't use a .venv).
    """

    language: str
    venv_present: bool = False


def detect_project_env(bound_path: str | os.PathLike[str]) -> ProjectEnvInfo:
    """Detect the project language by scanning for marker files.

    Returns a :class:`ProjectEnvInfo` with ``language`` set to the first
    matching language (Python takes priority because its env isolation is
    the most critical — a stray ``pip install`` can crash AChat). If no
    marker matches, ``language='unknown'``.

    For Python projects, ``venv_present`` is set by checking ``<bound_path>/.venv``.
    For other languages, ``venv_present`` is always ``False``.
    """
    root = Path(bound_path)
    if not root.is_dir():
        return ProjectEnvInfo(language="unknown")

    if any((root / m).is_file() for m in _PYTHON_MARKERS):
        venv_present = detect_project_venv(str(root)) is not None
        return ProjectEnvInfo(language="python", venv_present=venv_present)

    if any((root / m).is_file() for m in _NODE_MARKERS):
        return ProjectEnvInfo(language="nodejs")

    if any((root / m).is_file() for m in _JAVA_MARKERS):
        return ProjectEnvInfo(language="java")

    if any((root / m).is_file() for m in _GO_MARKERS):
        return ProjectEnvInfo(language="go")

    return ProjectEnvInfo(language="unknown")


def detect_python_venv(bound_path: str | os.PathLike[str]) -> bool:
    """Return True if a usable Python ``.venv`` exists at ``bound_path``."""
    return detect_project_venv(str(bound_path)) is not None


# ─── DB helpers ─────────────────────────────────────────────────────────────


async def _get_workspace_by_conversation(
    conversation_id: str,
) -> Workspace | None:
    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()


async def _set_env_preference(
    conversation_id: str, preference: str
) -> None:
    """Persist ``env_preference`` and invalidate the workspace cache."""
    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        ws = result.scalar_one_or_none()
        if ws is None:
            logger.warning(
                "[workspace_env] cannot set env_preference: workspace not found "
                "for conversation %s",
                conversation_id,
            )
            return
        ws.env_preference = preference
    await invalidate_workspace_cache(conversation_id)


# ─── Public API ─────────────────────────────────────────────────────────────


async def detect_and_hint(conversation_id: str, user_id: str | None) -> None:
    """Detect project env and emit a hint event if the user must decide.

    This is the entry point invoked (fire-and-forget) by
    :func:`ConversationService.create_conversation` after a local workspace
    is bound. It MUST NOT raise — failures are logged and swallowed so
    conversation creation is never blocked.

    A hint is published only when:
      - the workspace mode is ``local`` (sandbox workspaces have no user
        project to inspect);
      - the detected language is ``python``;
      - no project ``.venv`` exists;
      - ``Workspace.env_preference`` is ``None`` (user hasn't already
        chosen create / skip / system_python).
    """
    try:
        ws = await _get_workspace_by_conversation(conversation_id)
        if ws is None or ws.mode != "local" or not ws.bound_path:
            return
        if ws.env_preference is not None:
            # User already made a choice — don't re-prompt.
            return

        info = detect_project_env(ws.bound_path)
        if info.language != "python" or info.venv_present:
            return

        event_bus.publish(
            WorkspaceEnvHintEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                language="python",
                venv_present=False,
                options=["create", "skip", "system_python"],
            ),
            user_id=user_id,
        )
        logger.info(
            "[workspace_env] hint emitted for conversation=%s (python, no venv)",
            conversation_id,
        )
    except Exception:
        logger.warning(
            "[workspace_env] detect_and_hint failed for conversation=%s",
            conversation_id,
            exc_info=True,
        )


async def set_env_preference(
    conversation_id: str, preference: str, user_id: str | None
) -> None:
    """Persist the user's env choice (skip / system_python / venv_created).

    Used by the REST endpoint ``PATCH /api/workspaces/{id}/env-preference``.
    Validates ``preference`` against the closed set of allowed values.
    """
    allowed = {"venv_created", "skip", "system_python"}
    if preference not in allowed:
        raise ValueError(
            f"Invalid env preference: {preference}. Must be one of {sorted(allowed)}"
        )
    await _set_env_preference(conversation_id, preference)


async def create_project_venv(
    conversation_id: str, user_id: str | None
) -> None:
    """Create a ``.venv`` in the workspace's bound path.

    Emits ``WorkspaceEnvStatusEvent`` with ``status='creating'`` then either
    ``'ready'`` (with the venv path) or ``'failed'`` (with the error). On
    success, persists ``Workspace.env_preference='venv_created'``.

    The venv is created using the same Python interpreter that runs AChat
    — but with the env-isolation layer (see :mod:`app.utils.env_isolation`)
    scrubbed, so the venv is a fresh child of the system Python, NOT a
    clone of AChat's venv. ``--upgrade-deps`` ensures pip/setuptools inside
    the new venv are usable.
    """
    ws = await _get_workspace_by_conversation(conversation_id)
    if ws is None or ws.mode != "local" or not ws.bound_path:
        logger.warning(
            "[workspace_env] create_project_venv: no local workspace for "
            "conversation=%s",
            conversation_id,
        )
        event_bus.publish(
            WorkspaceEnvStatusEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                status="failed",
                error="No local workspace bound to this conversation",
            ),
            user_id=user_id,
        )
        return

    bound_path = ws.bound_path
    venv_root = os.path.join(bound_path, ".venv")

    # Announce creation start.
    event_bus.publish(
        WorkspaceEnvStatusEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            status="creating",
        ),
        user_id=user_id,
    )

    # ``python -m venv --upgrade-deps .venv`` — run in a thread so the event
    # loop isn't blocked. We pass a minimal env (no AChat secrets) via
    # build_tool_env so the venv's pip doesn't pick up AChat's index.
    from app.utils.env_isolation import build_tool_env

    cmd = [sys.executable, "-m", "venv", "--upgrade-deps", ".venv"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=bound_path,
            env=build_tool_env(cwd=bound_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = (
                f"python -m venv exited with code {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()[:500]}"
            )
            logger.warning(
                "[workspace_env] venv creation failed for conversation=%s: %s",
                conversation_id,
                err_msg,
            )
            event_bus.publish(
                WorkspaceEnvStatusEvent(
                    conversation_id=conversation_id,
                    timestamp=now_ms(),
                    status="failed",
                    error=err_msg,
                ),
                user_id=user_id,
            )
            return
    except Exception as exc:
        err_msg = f"Failed to spawn python -m venv: {exc}"
        logger.warning(
            "[workspace_env] venv creation failed for conversation=%s: %s",
            conversation_id,
            err_msg,
            exc_info=True,
        )
        event_bus.publish(
            WorkspaceEnvStatusEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                status="failed",
                error=err_msg,
            ),
            user_id=user_id,
        )
        return

    # Sanity-check the venv is usable (python binary exists).
    python_exe = os.path.join(
        venv_root, "Scripts" if IS_WINDOWS else "bin", "python.exe" if IS_WINDOWS else "python"
    )
    if not os.path.isfile(python_exe):
        err_msg = (
            f"venv creation reported success but {python_exe} is missing"
        )
        logger.warning("[workspace_env] %s", err_msg)
        event_bus.publish(
            WorkspaceEnvStatusEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                status="failed",
                error=err_msg,
            ),
            user_id=user_id,
        )
        return

    # Success — persist preference and announce ready.
    await _set_env_preference(conversation_id, "venv_created")
    event_bus.publish(
        WorkspaceEnvStatusEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            status="ready",
            venv_path=venv_root,
        ),
        user_id=user_id,
    )
    logger.info(
        "[workspace_env] venv created at %s for conversation=%s",
        venv_root,
        conversation_id,
    )


__all__ = [
    "ProjectEnvInfo",
    "create_project_venv",
    "detect_and_hint",
    "detect_project_env",
    "detect_python_venv",
    "set_env_preference",
]
