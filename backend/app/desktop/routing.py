"""Desktop routing table: local engine vs official cloud.

This module is the backend companion to `src/lib/desktop.ts` and
`apps/desktop/docs/ROUTING.md`. It does not rewrite HTTP itself; it documents
and exposes prefixes for future gateway middleware / frontend helpers.
"""

from __future__ import annotations

# Paths the desktop frontend should call on the local engine (with X-Engine-Token).
LOCAL_ENGINE_PREFIXES: tuple[str, ...] = (
    "/api/messages",
    "/api/stream",
    "/api/fs",
    "/api/pending",
    "/api/runs",
    "/api/desktop",
    "/healthz",
)

# Paths that remain on the official cloud API (user JWT / cookies).
OFFICIAL_CLOUD_PREFIXES: tuple[str, ...] = (
    "/api/auth",
    "/api/profile",
    "/api/settings",
    "/api/conversations",
    "/api/agents",
    "/api/documents",
    "/api/memory",
    "/api/skills",
    "/api/mcp",
    "/api/obsidian",
)


def is_local_engine_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in LOCAL_ENGINE_PREFIXES)


def is_official_cloud_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in OFFICIAL_CLOUD_PREFIXES)
