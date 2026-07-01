"""Adapter session caches.

Port of src/server/adapters/session-store.ts.

The Codex CLI adapter keeps its own server-side session so follow-up turns
reuse context. We cache the session id keyed by ``conversationId:agentId``.

conversation_service clears these whenever the DB history diverges from what the
SDK remembers (delete / clear / withdraw / regenerate), otherwise the SDK would
replay a now-deleted "user msg → agent reply" pair.
"""

# In-process singletons (single-user, local-first). Module globals mirror the
# TypeScript ``globalThis`` singletons.
codex_sessions: dict[str, str] = {}


def adapter_session_key(conversation_id: str, agent_id: str) -> str:
    """Build the composite key used by per-agent session stores (codex)."""
    return f"{conversation_id}:{agent_id}"


def clear_codex_session(conversation_id: str) -> None:
    """Drop every cached codex session belonging to a conversation."""
    prefix = f"{conversation_id}:"
    for key in [k for k in codex_sessions if k.startswith(prefix)]:
        del codex_sessions[key]
