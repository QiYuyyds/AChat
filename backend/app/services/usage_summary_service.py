"""Global token usage aggregation.

Port of src/app/api/usage/summary/route.ts. Scans all agent_runs with non-null
usage and rolls them up into today / week / all-time buckets plus per-agent,
per-model and per-conversation (top 10) aggregates. Usage JSON is stored
camelCase (see agent_runner), so keys are read with camelCase names.
"""

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, AgentRun, Conversation
from app.utils.clock import now_ms

DAY_MS = 24 * 60 * 60 * 1000


def _empty() -> dict:
    return {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheCreationTokens": 0,
        "totalTokens": 0,
        "runs": 0,
    }


def _accumulate(b: dict, u: dict) -> None:
    inp = u.get("inputTokens", 0) or 0
    out = u.get("outputTokens", 0) or 0
    cache_read = u.get("cacheReadTokens", 0) or 0
    cache_creation = u.get("cacheCreationTokens", 0) or 0
    b["inputTokens"] += inp
    b["outputTokens"] += out
    b["cacheReadTokens"] += cache_read
    b["cacheCreationTokens"] += cache_creation
    b["totalTokens"] += inp + out + cache_read + cache_creation
    b["runs"] += 1


async def get_usage_summary() -> dict:
    """Aggregate token usage across all runs. Returns a camelCase wire dict."""
    async with get_db() as db:
        run_rows = (
            await db.execute(select(AgentRun).where(AgentRun.usage.is_not(None)))
        ).scalars().all()

        now = now_ms()
        today_start = now - DAY_MS
        week_start = now - 7 * DAY_MS

        today = _empty()
        week = _empty()
        all_time = _empty()
        by_agent_map: dict[str, dict] = {}
        by_model_map: dict[str, dict] = {}
        by_conv_map: dict[str, dict] = {}

        for row in run_rows:
            u = row.usage_dict
            if not u:
                continue
            _accumulate(all_time, u)
            if row.started_at >= week_start:
                _accumulate(week, u)
            if row.started_at >= today_start:
                _accumulate(today, u)

            agent_b = by_agent_map.setdefault(row.agent_id, _empty())
            _accumulate(agent_b, u)

            model = u.get("model")
            if model:
                model_b = by_model_map.setdefault(model, _empty())
                _accumulate(model_b, u)

            conv_b = by_conv_map.setdefault(row.conversation_id, _empty())
            _accumulate(conv_b, u)

        agent_name_by_id: dict[str, str] = {}
        if by_agent_map:
            agent_rows = (
                await db.execute(
                    select(Agent).where(Agent.id.in_(list(by_agent_map.keys())))
                )
            ).scalars().all()
            agent_name_by_id = {a.id: a.name for a in agent_rows}

        top_conv_ids = [
            cid
            for cid, _ in sorted(
                by_conv_map.items(), key=lambda kv: kv[1]["totalTokens"], reverse=True
            )[:10]
        ]
        conv_by_id: dict[str, Conversation] = {}
        if top_conv_ids:
            conv_rows = (
                await db.execute(
                    select(Conversation).where(Conversation.id.in_(top_conv_ids))
                )
            ).scalars().all()
            conv_by_id = {c.id: c for c in conv_rows}

    top_conversations = []
    for cid in top_conv_ids:
        c = conv_by_id.get(cid)
        b = by_conv_map.get(cid)
        if c is None or b is None:
            continue
        top_conversations.append(
            {
                "id": cid,
                "title": c.title,
                "totalTokens": b["totalTokens"],
                "runs": b["runs"],
                "updatedAt": c.updated_at,
            }
        )

    by_agent = sorted(
        (
            {
                "agentId": agent_id,
                "name": agent_name_by_id.get(agent_id, agent_id),
                "totalTokens": b["totalTokens"],
                "runs": b["runs"],
            }
            for agent_id, b in by_agent_map.items()
        ),
        key=lambda x: x["totalTokens"],
        reverse=True,
    )

    by_model = sorted(
        (
            {"model": model, "totalTokens": b["totalTokens"], "runs": b["runs"]}
            for model, b in by_model_map.items()
        ),
        key=lambda x: x["totalTokens"],
        reverse=True,
    )

    return {
        "today": today,
        "week": week,
        "allTime": all_time,
        "topConversations": top_conversations,
        "byAgent": by_agent,
        "byModel": by_model,
    }
