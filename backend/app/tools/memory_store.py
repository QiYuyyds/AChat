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


async def memory_recall_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Recall relevant memories using hybrid BM25 + wikilink search."""
    query = args.get("query", "").strip() if isinstance(args, dict) else str(args)
    if not query:
        return err("query is required for memory_recall")

    top_k = args.get("top_k", 5) if isinstance(args, dict) else 5

    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
        if _memory_service is None:
            return err("Memory service not initialized")
        results = await _memory_service.recall(query, top_k=top_k, agent_id=ctx.agent_id)
        memories = [
            {
                "name": r.name,
                "content": r.content,
                "score": r.score,
                "source": r.source,
                "path": r.path,
            }
            for r in results
        ]
        # Also get preference context (PG-backed, preserved)
        pref_context = _memory_service.get_preference_context()
        return ok({"memories": memories, "preferences": pref_context})
    except Exception as e:
        return err(f"Memory recall failed: {e}")


memory_recall_tool = ToolDef(
    name="memory_recall",
    description=(
        "Recall relevant memories from the file-native memory system using "
        "hybrid BM25 + wikilink search. Use this at the start of a task to "
        "check for past context, or when the user references prior work."
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
