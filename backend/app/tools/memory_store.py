"""memory_store tool — Agent-initiated memory write to digest/ files.

Agents with ``memory_enabled=true`` receive this tool. Writes go to
digest/{bucket}/ Markdown files with frontmatter. Uses the file-native
storage layer (no PG/embedding).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.rate_limiter import _rate_limiter

logger = logging.getLogger(__name__)

MAX_WRITES_PER_RUN = 3
MAX_CONTENT_LENGTH = 500


async def memory_store_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Store a memory by writing a digest Markdown file."""
    if not isinstance(args, dict):
        return err("memory_store requires a dict of arguments")

    name = str(args.get("name", "")).strip()
    if not name:
        return err("name is required")

    content = str(args.get("content", "")).strip()
    if not content or len(content) > MAX_CONTENT_LENGTH:
        return err(f"content must be 1-{MAX_CONTENT_LENGTH} characters")

    bucket = str(args.get("bucket", "procedure")).strip()
    if bucket not in ("procedure", "wiki"):
        return err("bucket must be 'procedure' or 'wiki'")

    try:
        importance = float(args.get("importance", 0.5))
    except (TypeError, ValueError):
        return err("importance must be a number between 0 and 1.0")
    if importance < 0 or importance > 1.0:
        return err("importance must be between 0 and 1.0")

    tags = args.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    # Per-run rate limiting
    rate_key = f"mem_writes:{ctx.agent_id}:{ctx.run_id}"
    count = await _rate_limiter.incr(rate_key, ttl=300)
    if count > MAX_WRITES_PER_RUN:
        return err(f"memory_store rate limit: max {MAX_WRITES_PER_RUN} writes per agent run")

    # Access memory service
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")

    if _memory_service is None:
        return err("Memory service not initialized")

    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    today = date.today().isoformat()
    fm = MemoryFrontmatter(
        name=name,
        description=str(args.get("description", "")),
        agent_id=ctx.agent_id or None,
        tags=[str(t) for t in tags if t][:10],
        importance=importance,
        bucket=bucket,
        created_at=today,
        updated_at=today,
        source=f"agent:{ctx.agent_id}",
    )

    filepath = _memory_service.workspace.digest_path(bucket, name, agent_id=ctx.agent_id or None)
    write_markdown(filepath, fm, content)

    # Reindex the file
    _memory_service.auto_index.index_file(filepath)

    return ok({
        "stored": True,
        "path": str(filepath.relative_to(_memory_service.workspace.root)),
        "bucket": bucket,
    })


memory_store_tool = ToolDef(
    name="memory_store",
    description=(
        "Store a long-term memory as a Markdown file in the digest. "
        "ONLY store facts that are: "
        "(1) long-lived and stable (tech stack, project constraints), "
        "(2) affect future tasks (deployment failures, API quirks), "
        "(3) have long-term learning value. "
        "DO NOT store: temporary conversation details, "
        "information derivable from code, single-use operation results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Concise title for the memory (will be the filename).",
            },
            "content": {
                "type": "string",
                "description": "Memory content in Markdown. Must be self-contained.",
            },
            "bucket": {
                "type": "string",
                "enum": ["procedure", "wiki"],
                "description": "procedure=how-to experience, wiki=knowledge node",
            },
            "importance": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0.3=minor, 0.5=normal, 0.8=critical",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for filtering.",
            },
            "description": {
                "type": "string",
                "description": "Optional short description for the memory.",
            },
        },
        "required": ["name", "content", "bucket", "importance"],
    },
    handler=memory_store_handler,
)


# ─── memory_recall tool ────────────────────────────────────────────────────

MAX_RELATED_PER_SIDE = 5


def _slim_related(expansion: dict) -> dict:
    """Slim expansion metadata into the `related` shape for tool output.

    Keeps {path, name, predicate} only (no description — token economy),
    capped at MAX_RELATED_PER_SIDE entries per side.
    """
    def slim(entries: list[dict]) -> list[dict]:
        return [
            {
                "path": e.get("path", ""),
                "name": e.get("name", ""),
                "predicate": e.get("predicate"),
            }
            for e in entries[:MAX_RELATED_PER_SIDE]
        ]

    return {
        "outlinks": slim(expansion.get("outlinks", [])),
        "inlinks": slim(expansion.get("inlinks", [])),
    }


async def memory_recall_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Recall relevant memories using hybrid BM25 + vector search."""
    query = args.get("query", "").strip() if isinstance(args, dict) else str(args)
    if not query:
        return err("query is required for memory_recall")

    top_k = args.get("top_k", 5) if isinstance(args, dict) else 5

    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
        if _memory_service is None:
            return err("Memory service not initialized")
        results = await _memory_service.recall(
            query, top_k=top_k, agent_id=ctx.agent_id, user_id=ctx.user_id,
        )
        memories = [
            {
                "name": r.name,
                "content": r.content,
                "score": r.score,
                "source": r.source,
                "path": r.path,
                "related": _slim_related(_memory_service.build_expansion(r.path)),
            }
            for r in results
        ]
        # Preferences are scoped to the calling user (same as 沉淀 UI).
        pref_context = await _memory_service.get_preference_context(user_id=ctx.user_id)
        return ok({"memories": memories, "preferences": pref_context})
    except Exception as e:
        return err(f"Memory recall failed: {e}")


memory_recall_tool = ToolDef(
    name="memory_recall",
    description=(
        "Search long-term memory with keyword (BM25) + vector semantic recall. "
        "Use this at the start of a task to check for past context, or when "
        "the user references prior work. Each returned memory includes a "
        "`related` field: outlinks (cards it points to) and inlinks (cards "
        "pointing to it), each entry with {path, name, predicate} and capped "
        "at 5 per side. Follow provenance links with memory_read — e.g. a "
        "digest card's derived_from outlink leads to the daily card with the "
        "full history, and a daily card's inlinks reveal the digest card "
        "holding the latest distilled conclusion."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The query to search for in memory.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of memories to return (default: 5).",
            },
        },
        "required": ["query"],
    },
    handler=memory_recall_handler,
)


# ─── memory_read tool ──────────────────────────────────────────────────────

MAX_READ_CONTENT_LENGTH = 2000


async def memory_read_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Read a single memory card by workspace-relative path."""
    if not isinstance(args, dict):
        return err("memory_read requires a dict of arguments")

    path = str(args.get("path", "")).strip()
    if not path:
        return err("path is required for memory_read")

    normalized = path.replace("\\", "/").lstrip("/")
    if not normalized.endswith(".md"):
        return err(
            "memory_read expects a workspace-relative Markdown path ending "
            "with .md (e.g. digest/wiki/my-card.md or daily/2026-08-01/session-1.md)"
        )

    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")
    if _memory_service is None:
        return err("Memory service not initialized")
    svc = _memory_service

    from app.memory.file_store.markdown_io import read_markdown

    root = svc.workspace.root
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except (ValueError, OSError):
        return err("path escapes the memory workspace")

    mem = read_markdown(root / normalized)
    if mem is None:
        return err(f"Memory card not found: {normalized}")

    fm = mem.frontmatter
    content = mem.body
    truncated = False
    if len(content) > MAX_READ_CONTENT_LENGTH:
        content = content[:MAX_READ_CONTENT_LENGTH]
        truncated = True

    return ok({
        "path": normalized,
        "name": fm.name,
        "content": content,
        "status": fm.status,
        "agent_id": fm.agent_id,
        "bucket": fm.bucket,
        "importance": fm.importance,
        "related": _slim_related(svc.build_expansion(normalized)),
        "truncated": truncated,
    })


memory_read_tool = ToolDef(
    name="memory_read",
    description=(
        "Read a single memory card by its workspace-relative path. Paths come "
        "from memory_recall results' related field (outlinks/inlinks) or from "
        "the [[wikilink]] targets inside card content — this is how you follow "
        "provenance across cards (e.g. digest → derived_from → daily card with "
        "the full history). Returns name, content, status "
        "(active/archived/superseded), owning agent_id, and related "
        "outlinks/inlinks for the next hop. Content is capped at 2000 "
        "characters; the truncated flag is set when cut."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative memory file path ending with .md, "
                    "e.g. digest/wiki/my-card.md or daily/2026-08-01/session-1.md."
                ),
            },
        },
        "required": ["path"],
    },
    handler=memory_read_handler,
)


# ─── memory_proactive tool ─────────────────────────────────────────────────


async def memory_proactive_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Get proactive interest topics for the current session."""
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
        if _memory_service is None:
            return err("Memory service not initialized")
        topics = _memory_service.proactive.get_topics()
        return ok({"topics": topics, "total": len(topics)})
    except Exception as e:
        return err(f"Proactive memory failed: {e}")


memory_proactive_tool = ToolDef(
    name="memory_proactive",
    description=(
        "Retrieve proactive interest topics that the memory system has "
        "identified as potentially relevant. Topics are generated by the "
        "auto_dream pipeline from recent conversations."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=memory_proactive_handler,
)
