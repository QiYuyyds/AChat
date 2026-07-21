"""Tests for GET /api/usage/timeseries — daily token usage aggregation.

Uses the shared ``api_client`` fixture (authenticated) and ``agents`` fixture
from conftest. Conversations are created via the API so ``user_id`` is set
correctly; AgentRun rows are seeded directly in the DB.
"""

DAY_MS = 24 * 60 * 60 * 1000


async def _create_conversation(api_client, agent_id: str, title: str = "TS Conv") -> str:
    """Create a conversation via the API and return its id."""
    resp = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agent_id], "title": title},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["conversation"]["id"]


async def _seed_run(conv_id: str, agent_id: str, usage: dict, started_at: int) -> str:
    """Insert an AgentRun row directly into the DB."""
    from app.db.engine import get_db
    from app.db.models import AgentRun
    from app.utils.ids import new_run_id

    rid = new_run_id()
    async with get_db() as session:
        run = AgentRun(
            id=rid,
            conversation_id=conv_id,
            agent_id=agent_id,
            status="finished",
            started_at=started_at,
            finished_at=started_at,
        )
        run.usage_dict = usage
        session.add(run)
    return rid


# ─── empty data ──────────────────────────────────────────────────────────────
async def test_timeseries_empty_returns_all_zero(api_client):
    resp = await api_client.get("/api/usage/timeseries", params={"days": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 7
    for point in body:
        assert point["inputTokens"] == 0
        assert point["outputTokens"] == 0
        assert point["cacheReadTokens"] == 0
        assert point["cacheCreationTokens"] == 0
        assert point["totalTokens"] == 0
        assert point["runs"] == 0
        assert "date" in point


# ─── aggregation ─────────────────────────────────────────────────────────────
async def test_timeseries_aggregates_by_day(api_client, agents):
    conv_id = await _create_conversation(api_client, agents["alice"])
    from app.utils.clock import now_ms

    now = now_ms()

    # Two runs today
    await _seed_run(
        conv_id,
        agents["alice"],
        {"inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 10, "cacheCreationTokens": 5},
        now,
    )
    await _seed_run(
        conv_id,
        agents["alice"],
        {"inputTokens": 200, "outputTokens": 30, "cacheReadTokens": 0, "cacheCreationTokens": 0},
        now - 1000,
    )
    # One run yesterday
    await _seed_run(
        conv_id,
        agents["alice"],
        {"inputTokens": 500, "outputTokens": 100, "cacheReadTokens": 50, "cacheCreationTokens": 20},
        now - DAY_MS,
    )

    resp = await api_client.get("/api/usage/timeseries", params={"days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3

    today_point = body[-1]
    assert today_point["inputTokens"] == 300
    assert today_point["outputTokens"] == 80
    assert today_point["cacheReadTokens"] == 10
    assert today_point["cacheCreationTokens"] == 5
    assert today_point["totalTokens"] == 395
    assert today_point["runs"] == 2

    yesterday_point = body[-2]
    assert yesterday_point["inputTokens"] == 500
    assert yesterday_point["outputTokens"] == 100
    assert yesterday_point["totalTokens"] == 670
    assert yesterday_point["runs"] == 1


# ─── ascending date order ────────────────────────────────────────────────────
async def test_timeseries_dates_ascending(api_client):
    resp = await api_client.get("/api/usage/timeseries", params={"days": 7})
    assert resp.status_code == 200
    body = resp.json()
    dates = [p["date"] for p in body]
    assert dates == sorted(dates)
    assert len(dates) == 7


# ─── days clamp ──────────────────────────────────────────────────────────────
async def test_timeseries_days_clamp_low(api_client):
    resp = await api_client.get("/api/usage/timeseries", params={"days": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1


async def test_timeseries_days_clamp_high(api_client):
    resp = await api_client.get("/api/usage/timeseries", params={"days": 200})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 90


async def test_timeseries_days_default(api_client):
    resp = await api_client.get("/api/usage/timeseries")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 14


# ─── old runs excluded ───────────────────────────────────────────────────────
async def test_timeseries_excludes_old_runs(api_client, agents):
    conv_id = await _create_conversation(api_client, agents["alice"])
    from app.utils.clock import now_ms

    now = now_ms()

    # A run from 30 days ago — should not appear in a 7-day window
    await _seed_run(
        conv_id,
        agents["alice"],
        {"inputTokens": 999, "outputTokens": 999, "cacheReadTokens": 0, "cacheCreationTokens": 0},
        now - 30 * DAY_MS,
    )

    resp = await api_client.get("/api/usage/timeseries", params={"days": 7})
    assert resp.status_code == 200
    body = resp.json()
    for point in body:
        assert point["totalTokens"] == 0
        assert point["runs"] == 0
