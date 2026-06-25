"""Bash command approval classification + wait.

Port of src/server/bash-command-approval.ts. Commands matching the heuristics
(package installs, recursive deletes, permission changes, docker, ...) require
explicit user approval before running.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.services.pending_bash_commands import pending_bash_commands
from app.utils.approval import await_pending_decision


@dataclass
class BashApproval:
    required: bool
    reason: str


_BASE_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add|remove|rm|uninstall|update|upgrade)\b",
            re.IGNORECASE,
        ),
        "package manager changes dependencies or downloads packages",
    ),
    (
        re.compile(r"\b(?:npx|bunx)\b|\b(?:pnpm|yarn)\s+dlx\b", re.IGNORECASE),
        "package runner may download and execute packages",
    ),
    (
        re.compile(
            r"\b(?:pip|pip3|uv)\s+(?:install|add|remove|sync)\b|"
            r"\bpython(?:3)?\s+-m\s+pip\s+install\b",
            re.IGNORECASE,
        ),
        "Python package command may download or change dependencies",
    ),
    (re.compile(r"\bgit\s+(?:reset|clean)\b", re.IGNORECASE), "git command may discard local changes"),
    (
        re.compile(r"\bgit\s+(?:checkout|restore)\b[\s\S]*(?:--|\s)\.", re.IGNORECASE),
        "git command may overwrite workspace files",
    ),
    (
        re.compile(r"\brm\s+-(?:[A-Za-z]*r[A-Za-z]*f|[A-Za-z]*f[A-Za-z]*r)\b", re.IGNORECASE),
        "recursive force delete command",
    ),
    (re.compile(r"\bfind\b[\s\S]*\s-delete\b", re.IGNORECASE), "find -delete may remove many files"),
    (re.compile(r"\b(?:chmod|chown)\b", re.IGNORECASE), "permission or ownership change"),
    (
        re.compile(
            r"\bdocker\s+(?:run|compose|build|push|pull|system|volume|network)\b",
            re.IGNORECASE,
        ),
        "Docker command may affect local containers, images, or network",
    ),
]

_WINDOWS_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bRemove-Item\b[\s\S]*-(?:Recurse|Force)\b", re.IGNORECASE),
        "PowerShell recursive or forced removal",
    ),
    (
        re.compile(
            r"\b(?:npm|pnpm|yarn|bun)\.cmd\s+(?:install|i|ci|add|remove|rm|uninstall|update|upgrade)\b",
            re.IGNORECASE,
        ),
        "package manager changes dependencies or downloads packages",
    ),
]


def classify_bash_approval(command: str, platform: str) -> BashApproval:
    normalized = command.strip()
    checks = list(_BASE_CHECKS)
    if platform == "windows":
        checks.extend(_WINDOWS_CHECKS)
    for pattern, reason in checks:
        if pattern.search(normalized):
            return BashApproval(required=True, reason=reason)
    return BashApproval(required=False, reason="")


async def wait_for_bash_approval(
    *,
    conversation_id: str,
    agent_id: str,
    run_id: str,
    command: str,
    cwd: str,
    reason: str,
    cancel_event: asyncio.Event,
) -> bool:
    pending = pending_bash_commands.register(
        conversation_id=conversation_id,
        agent_id=agent_id,
        run_id=run_id,
        command=command,
        cwd=cwd,
        reason=reason,
    )

    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_bash_commands.attach_resolver(pending.id, r),
        cancel=lambda: pending_bash_commands.cancel(pending.id),
        cancel_event=cancel_event,
        cancelled_value={"approved": False},
    )
    return bool(decision.get("approved")) if isinstance(decision, dict) else False
