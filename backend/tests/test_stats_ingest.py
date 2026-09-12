"""Tests for the cloud stats ingest endpoints and web server-side counting.

Covers (add-stats-ingest tasks 1.2–1.4, 2.1–2.3, 3.1):

- POST /api/stats/batch: legal batch accumulates (repeat = accumulate, not
  overwrite), non-whitelisted metric 422, unauthenticated 401, rate limit 429
- Privacy boundary: content/token-ish payload fields all rejected (parametrized)
- Web counting: login / conversation create / message send server-side counters
- Heartbeat: active_minutes upsert with server-side clamp
- Isolation: stats failures never break the business path
"""

import pytest

from app.services import stats_service
from app.services.stats_service import RateLimiter, utc_day


async def _count_rows(user_id: str | None = None) -> list:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import StatsDailyCounter

    async with get_db() as session:
        query = select(StatsDailyCounter)
        if user_id is not None:
            query = query.where(StatsDailyCounter.user_id == user_id)
        result = await session.execute(query)
        return result.scalars().all()


def _entry(metric: str = "messages_sent", value: int = 3, client_type: str = "desktop", day: str | None = None):
    return {
        "day": day or utc_day(),
        "clientType": client_type,
        "metric": metric,
        "value": value,
    }


# ─── POST /api/stats/batch ───────────────────────────────────────────────────


async def test_legal_batch_accumulates_and_repeats_add_up(api_client):
    day = utc_day()
    resp = await api_client.post(
        "/api/stats/batch", json={"entries": [_entry(value=3, day=day)]}
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1

    resp = await api_client.post(
        "/api/stats/batch", json={"entries": [_entry(value=2, day=day)]}
    )
    assert resp.status_code == 200

    rows = await _count_rows(user_id="test_user_1")
    assert len(rows) == 1, "same key must upsert into one row, not duplicate rows"
    assert rows[0].value == 5, "repeat reports must accumulate, not overwrite"
    assert rows[0].day == day
    assert rows[0].client_type == "desktop"
    assert rows[0].metric == "messages_sent"


async def test_multiple_entries_in_one_batch_aggregate(api_client):
    resp = await api_client.post(
        "/api/stats/batch",
        json={
            "entries": [
                _entry(metric="logins", value=1),
                _entry(metric="logins", value=2),  # same key in-batch → merge
                _entry(metric="sessions_created", value=1),
            ]
        },
    )
    assert resp.status_code == 200
    rows = await _count_rows(user_id="test_user_1")
    by_metric = {r.metric: r.value for r in rows}
    assert by_metric == {"logins": 3, "sessions_created": 1}


async def test_non_whitelisted_metric_rejected_422_not_stored(api_client):
    resp = await api_client.post(
        "/api/stats/batch", json={"entries": [_entry(metric="tokens", value=1000)]}
    )
    assert resp.status_code == 422
    assert await _count_rows(user_id="test_user_1") == []


async def test_unauthenticated_batch_returns_401(raw_client):
    resp = await raw_client.post("/api/stats/batch", json={"entries": [_entry()]})
    assert resp.status_code == 401


async def test_unauthenticated_heartbeat_returns_401(raw_client):
    resp = await raw_client.post(
        "/api/stats/heartbeat", json={"intervalMinutes": 5}
    )
    assert resp.status_code == 401


async def test_rate_limit_returns_429_and_does_not_store(api_client, monkeypatch):
    from app.api import stats as stats_api

    monkeypatch.setattr(stats_api, "_rate_limiter", RateLimiter(max_requests=1, window_seconds=60))

    resp = await api_client.post(
        "/api/stats/batch", json={"entries": [_entry(value=1)]}
    )
    assert resp.status_code == 200

    resp = await api_client.post(
        "/api/stats/batch", json={"entries": [_entry(value=1)]}
    )
    assert resp.status_code == 429
    rows = await _count_rows(user_id="test_user_1")
    assert len(rows) == 1 and rows[0].value == 1, "429'ed batch must not be stored"


# ─── 隐私边界（任务 1.4）：内容 / token 类字段全部在载荷校验层拒绝 ────────────


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "tokens",
        "token_usage",
        "content",
        "message_text",
        "messageText",
        "prompt",
        "user_input",
        "conversationTitle",
        "text",
        "transcript",
        "payload",
    ],
)
async def test_content_like_fields_rejected_parametrized(api_client, forbidden_field):
    entry = _entry(value=1)
    entry[forbidden_field] = "some secret user content that must never be stored"
    resp = await api_client.post("/api/stats/batch", json={"entries": [entry]})
    assert resp.status_code == 422, f"{forbidden_field} must be rejected by the payload model"
    assert await _count_rows(user_id="test_user_1") == []


async def test_extra_top_level_field_rejected(api_client):
    resp = await api_client.post(
        "/api/stats/batch",
        json={"entries": [_entry()], "userId": "someone", "email": "x@y.z"},
    )
    assert resp.status_code == 422


async def test_stats_schema_has_no_text_columns():
    """Schema-level privacy assertion: the counter table has no free-text columns."""
    from app.db.models import StatsDailyCounter

    columns = {c.name: c.type for c in StatsDailyCounter.__table__.columns}
    assert set(columns) == {"user_id", "day", "client_type", "metric", "value"}
    textish = [
        name
        for name, ctype in columns.items()
        if "TEXT" in str(ctype).upper() and name not in ("day", "client_type", "metric", "user_id")
    ]
    assert textish == []


# ─── web 服务端计数（任务 2.1 / 2.2）────────────────────────────────────────


async def test_login_counts_logins_client_web(api_client, raw_client):
    # register + login via the real auth endpoints
    resp = await raw_client.post(
        "/api/auth/register",
        json={"email": "counter@example.com", "name": "Counter", "password": "pass12345"},
    )
    assert resp.status_code == 200

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "counter@example.com", "password": "pass12345"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["user"]["id"]

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "counter@example.com", "password": "pass12345"},
    )
    assert resp.status_code == 200

    rows = await _count_rows(user_id=user_id)
    logins = [r for r in rows if r.metric == "logins"]
    assert len(logins) == 1 and logins[0].value == 2
    assert logins[0].client_type == "web"


async def test_desktop_proxied_login_counted_as_desktop(api_client, raw_client):
    """认证代理转发携带 X-AgentHub-Client: desktop → 云端计入 desktop。"""
    resp = await raw_client.post(
        "/api/auth/register",
        json={"email": "dt@example.com", "name": "DT", "password": "pass12345"},
    )
    assert resp.status_code == 200

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "dt@example.com", "password": "pass12345"},
        headers={"X-AgentHub-Client": "desktop"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["user"]["id"]

    rows = await _count_rows(user_id=user_id)
    logins = [r for r in rows if r.metric == "logins"]
    assert len(logins) == 1 and logins[0].client_type == "desktop"


async def test_conversation_and_message_counting(api_client, agents):
    from app.services import conversation_service as cs

    conv = await cs.create_conversation(
        mode="single", agent_ids=[agents["alice"]], user_id="test_user_1"
    )
    await cs.send_message(
        conversation_id=conv.id, content="hello", user_id="test_user_1"
    )
    await cs.send_message(
        conversation_id=conv.id, content="again", user_id="test_user_1"
    )

    rows = await _count_rows(user_id="test_user_1")
    by_metric = {r.metric: r.value for r in rows}
    assert by_metric.get("sessions_created") == 1
    assert by_metric.get("messages_sent") == 2
    assert "logins" not in by_metric
    for r in rows:
        assert r.client_type == "web"


# ─── 心跳（任务 3.1）────────────────────────────────────────────────────────


async def test_heartbeat_converts_interval_to_active_minutes(api_client):
    resp = await api_client.post("/api/stats/heartbeat", json={"intervalMinutes": 5})
    assert resp.status_code == 200
    assert resp.json()["activeMinutes"] == 5

    resp = await api_client.post("/api/stats/heartbeat", json={"intervalMinutes": 5})
    assert resp.status_code == 200

    rows = await _count_rows(user_id="test_user_1")
    active = [r for r in rows if r.metric == "active_minutes"]
    assert len(active) == 1 and active[0].value == 10
    assert active[0].client_type == "web"


async def test_heartbeat_interval_clamped_server_side(api_client):
    """防伪造：超大间隔被服务端上限钳制（默认 30 分钟）。"""
    resp = await api_client.post(
        "/api/stats/heartbeat", json={"intervalMinutes": 100000}
    )
    assert resp.status_code == 200
    clamped = resp.json()["activeMinutes"]

    rows = await _count_rows(user_id="test_user_1")
    active = [r for r in rows if r.metric == "active_minutes"]
    assert len(active) == 1 and active[0].value == clamped
    assert clamped <= 30


# ─── 异常隔离（任务 2.3）：统计失败不阻塞业务 ────────────────────────────────


async def test_stats_failure_does_not_block_login(api_client, raw_client, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("stats backend down")

    monkeypatch.setattr(stats_service, "upsert_daily_counters", _boom)

    resp = await raw_client.post(
        "/api/auth/register",
        json={"email": "isolated@example.com", "name": "Iso", "password": "pass12345"},
    )
    assert resp.status_code == 200
    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "isolated@example.com", "password": "pass12345"},
    )
    assert resp.status_code == 200, "login must succeed even when stats write raises"
    assert await _count_rows(user_id=None) == []


async def test_stats_failure_does_not_block_conversation_paths(
    api_client, agents, monkeypatch
):
    from app.services import conversation_service as cs

    async def _boom(*args, **kwargs):
        raise RuntimeError("stats backend down")

    monkeypatch.setattr(stats_service, "upsert_daily_counters", _boom)

    conv = await cs.create_conversation(
        mode="single", agent_ids=[agents["alice"]], user_id="test_user_1"
    )
    sent = await cs.send_message(
        conversation_id=conv.id, content="hello", user_id="test_user_1"
    )
    assert sent.message_id
    assert await _count_rows(user_id="test_user_1") == []
