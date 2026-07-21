"""Global token usage aggregation.

Port of src/app/api/usage/summary/route.ts. Scans all agent_runs with non-null
usage and rolls them up into today / week / all-time buckets plus per-agent,
per-model and per-conversation (top 10) aggregates. Usage JSON is stored
camelCase (see agent_runner), so keys are read with camelCase names.
"""

from datetime import datetime, timedelta

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


async def get_usage_timeseries(days: int) -> list[dict]:
    """Aggregate token usage by natural day for the last ``days`` days.

    Returns a list of daily buckets sorted ascending by date. Days with no
    runs are filled with zero-value buckets to ensure chart continuity.
    Each bucket has: date (YYYY-MM-DD), inputTokens, outputTokens,
    cacheReadTokens, cacheCreationTokens, totalTokens, runs.
    """
    now = now_ms()
    today_date = datetime.fromtimestamp(now / 1000).date()

    date_keys: list[str] = []
    for i in range(days - 1, -1, -1):
        d = today_date - timedelta(days=i)
        date_keys.append(d.strftime("%Y-%m-%d"))

    buckets: dict[str, dict] = {dk: _empty_with_date(dk) for dk in date_keys}

    async with get_db() as db:
        run_rows = (
            await db.execute(select(AgentRun).where(AgentRun.usage.is_not(None)))
        ).scalars().all()

        for row in run_rows:
            u = row.usage_dict
            if not u:
                continue
            row_date = datetime.fromtimestamp(row.started_at / 1000).date()
            dk = row_date.strftime("%Y-%m-%d")
            if dk in buckets:
                _accumulate(buckets[dk], u)

    return [buckets[dk] for dk in date_keys]


def _empty_with_date(date: str) -> dict:
    b = _empty()
    b["date"] = date
    return b
