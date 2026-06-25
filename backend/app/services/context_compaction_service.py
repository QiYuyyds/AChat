"""Context-compaction service.

Port of src/server/context-compaction-service.ts — but only the subset that
agent-runner imports: getLatestContextSummary / prefixPromptWithContextSummary /
renderConversationSummaryBlock. These three read/format the latest stored
ContextSummary row; none of them touch an LLM.

DEFERRED: the full ``compactConversation`` flow (load uncompacted messages →
render → call a summary model → insert a ContextSummary + system message) is NOT
ported here. It hits the Anthropic/OpenAI SDKs and is only triggered by an explicit
"compact" action, not by the runner's hot path. See the deferral note in the port
report.
"""

from __future__ import annotations

from sqlalchemy import desc, select

from app.db.engine import get_db
from app.db.models import ContextSummary


async def get_latest_context_summary(conversation_id: str) -> ContextSummary | None:
    """Most recent stored summary for a conversation, or None."""
    async with get_db() as db:
        result = await db.execute(
            select(ContextSummary)
            .where(ContextSummary.conversation_id == conversation_id)
            .order_by(desc(ContextSummary.created_at))
            .limit(1)
        )
        return result.scalars().first()


def render_conversation_summary_block(summary: ContextSummary) -> str:
    """Wrap a summary in the XML-ish tag the runner injects into prompts."""
    return "\n".join(
        [
            f'<conversation_summary covered_until_message_id="'
            f'{_escape_attr(summary.covered_until_message_id)}">',
            summary.summary,
            "</conversation_summary>",
        ]
    )


async def prefix_prompt_with_context_summary(conversation_id: str, prompt: str) -> str:
    """Prepend the latest summary block to a prompt (no-op when none exists)."""
    latest = await get_latest_context_summary(conversation_id)
    if latest is None:
        return prompt
    return "\n".join([render_conversation_summary_block(latest), "", prompt])


def _escape_attr(value: str) -> str:
    # XML attribute escaping, matching the TS escapeAttr (&, ", < only).
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
