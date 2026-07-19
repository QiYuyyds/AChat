"""CLI presence detection for Claude / Codex (not bundled in the package)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class CliPresence:
    name: str
    command: str
    available: bool
    path: str | None
    install_hint: str


CLAUDE_HINT = (
    "Claude Code CLI not found on PATH. Install from https://docs.anthropic.com/en/docs/claude-code "
    "then restart AChat."
)
CODEX_HINT = (
    "Codex CLI not found on PATH. Install the OpenAI Codex CLI and ensure `codex` is available, "
    "then restart AChat."
)


def detect_cli(command: str, name: str, hint: str) -> CliPresence:
    path = shutil.which(command)
    return CliPresence(
        name=name,
        command=command,
        available=path is not None,
        path=path,
        install_hint=hint if path is None else "",
    )


def detect_claude() -> CliPresence:
    return detect_cli("claude", "Claude Code", CLAUDE_HINT)


def detect_codex() -> CliPresence:
    return detect_cli("codex", "Codex", CODEX_HINT)


def missing_cli_error(kind: str) -> str:
    if kind == "claude":
        return detect_claude().install_hint or CLAUDE_HINT
    if kind == "codex":
        return detect_codex().install_hint or CODEX_HINT
    return f"CLI '{kind}' not found on PATH"
