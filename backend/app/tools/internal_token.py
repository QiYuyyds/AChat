"""Per-run internal tool token store for the Codex MCP bridge.

The CodexCLIAdapter generates a random token per run and passes it to the
MCP bridge script via the ``AGENTHUB_INTERNAL_TOOL_TOKEN`` env var. The
bridge sends it back as ``Authorization: Bearer <token>`` when calling
the internal ``/api/internal/agenthub-tools`` endpoint. This module
provides the token store that the endpoint validates against.

Tokens are in-memory and scoped to a run_id — they are revoked when the
run finishes (in the adapter's ``finally`` block).
"""

from __future__ import annotations

import secrets
import threading

# Thread-safe in-memory store: run_id → token
_tokens: dict[str, str] = {}
_lock = threading.Lock()


def generate_tool_token(run_id: str) -> str:
    """Generate a per-run internal tool token and store it.

    Returns the token string to pass to the MCP bridge via env var.
    """
    token = secrets.token_urlsafe(32)
    with _lock:
        _tokens[run_id] = token
    return token


def validate_tool_token(run_id: str, token: str) -> bool:
    """Validate that the given token matches the stored token for run_id.

    Uses ``secrets.compare_digest`` to prevent timing attacks.
    Returns ``False`` if no token is registered for the run.
    """
    with _lock:
        expected = _tokens.get(run_id)
    if expected is None:
        return False
    return secrets.compare_digest(token, expected)


def revoke_tool_token(run_id: str) -> None:
    """Remove the token for a finished run (best-effort)."""
    with _lock:
        _tokens.pop(run_id, None)
