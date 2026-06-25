"""Message search.

Port of src/server/search-service.ts. The Python persistence layer has no FTS5
virtual table, so search runs the SQL ``LIKE`` path (the TS ``fallback='like'``
branch) for every request. The wire contract (hits / total / tookMs) is
unchanged; ``snippetHtml`` is a raw substring of the serialised message parts,
matching the TS LIKE branch byte-for-byte.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

from app.db.engine import get_db
from app.utils.clock import now_ms


@dataclass(frozen=True)
class SearchHitRow:
    message_id: str
    conversation_id: str
    conversation_title: str
    role: str
    agent_id: str | None
    agent_name: str | None
    agent_avatar: str | None
    created_at: int
    snippet_html: str


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHitRow]
    total: int
    took_ms: int
    error: Literal["INVALID_QUERY"] | None = None


_LIKE_SQL = text(
    """
    SELECT
      m.id AS messageId,
      m.conversation_id AS conversationId,
      m.role AS role,
      m.agent_id AS agentId,
      m.created_at AS createdAt,
      substr(m.parts, max(1, instr(m.parts, :q) - 30), 80) AS snippetHtml,
      c.title AS conversationTitle,
      a.name AS agentName,
      a.avatar AS agentAvatar
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    LEFT JOIN agents a   ON a.id = m.agent_id
    WHERE m.parts LIKE '%' || :q || '%'
      AND (:conversationId IS NULL OR m.conversation_id = :conversationId)
      AND (:role IS NULL OR m.role = :role)
    ORDER BY m.created_at DESC
    LIMIT :limit OFFSET :offset
    """
)


async def search_messages(
    *,
    query: str,
    limit: int = 20,
    offset: int = 0,
    conversation_id: str | None = None,
    role: str | None = None,
    fallback: str | None = None,  # noqa: ARG001  (parity with TS; LIKE is the only path)
) -> SearchResult:
    """Search messages by substring over the serialised parts JSON."""
    trimmed = query.strip()
    if not trimmed:
        return SearchResult(hits=[], total=0, took_ms=0)

    capped_limit = min(max(limit, 1), 100)
    capped_offset = max(offset, 0)
    start = now_ms()

    async with get_db() as db:
        result = await db.execute(
            _LIKE_SQL,
            {
                "q": trimmed,
                "conversationId": conversation_id,
                "role": role,
                "limit": capped_limit,
                "offset": capped_offset,
            },
        )
        rows = result.mappings().all()

    hits = [
        SearchHitRow(
            message_id=r["messageId"],
            conversation_id=r["conversationId"],
            conversation_title=r["conversationTitle"],
            role=r["role"],
            agent_id=r["agentId"],
            agent_name=r["agentName"],
            agent_avatar=r["agentAvatar"],
            created_at=r["createdAt"],
            snippet_html=r["snippetHtml"],
        )
        for r in rows
    ]
    return SearchResult(hits=hits, total=len(hits), took_ms=now_ms() - start)
