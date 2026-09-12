"""Tests for desktop-mode stats queue, heartbeat aggregation, and reporter.

Covers (add-stats-ingest tasks 4.1–4.5):

- 埋点分流：桌面模式写本地 JSON 队列（崩溃安全、可重启恢复），不直写 DB
- 本地心跳聚合进队列（active_minutes，不入云直达路径）
- reporter：批量 POST 云端 /api/stats/batch（JWT 取自 cloud_session.json）、
  成功 ack 清队列、断网排队恢复补发、401 清缓存暂停、失败不阻塞
- 队列有界：超限丢最旧 + 日志
- 登出：清缓存 + 清队列 → 上报停止
"""

import asyncio
import json

import httpx
import pytest

from app.auth.desktop import (
    cloud_session_path,
    read_cloud_session,
    write_cloud_session,
)
from app.services import stats_queue, stats_reporter, stats_service
from app.services.stats_queue import QueueEntry, reset_queue_for_tests, snapshot_queue
from app.services.stats_service import utc_day


@pytest.fixture(autouse=True)
def _clean_queue_state():
    reset_queue_for_tests()
    yield
    reset_queue_for_tests()


def _entry(metric: str = "messages_sent", value: int = 2, day: str | None = None) -> QueueEntry:
    return QueueEntry(day=day or utc_day(), client_type="desktop", metric=metric, value=value)


# ─── 埋点分流（4.1）────────────────────────────────────────────────────────


async def test_desktop_counter_goes_to_queue_not_db(desktop_env):
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import StatsDailyCounter

    await stats_service.record_counter(None, "messages_sent", client_type="web", value=2)
    await stats_service.record_counter(None, "messages_sent", client_type="web", value=1)

    entries = await snapshot_queue()
    assert len(entries) == 1 and entries[0].value == 3
    assert entries[0].client_type == "desktop", "队列条目固定 desktop 归属"
    assert entries[0].metric == "messages_sent"

    async with get_db() as session:
        rows = (await session.execute(select(StatsDailyCounter))).scalars().all()
    assert rows == [], "桌面模式埋点 MUST NOT 直写计数表"

    # 队列文件真实落盘（data dir 下）
    raw = json.loads((desktop_env / "stats_queue.json").read_text(encoding="utf-8"))
    assert raw["entries"][0]["value"] == 3


async def test_queue_survives_process_restart(desktop_env):
    """崩溃安全：重启（内存态丢失）后从磁盘恢复聚合队列。"""
    await stats_queue.enqueue_counter(_entry(value=2))
    await stats_queue.enqueue_counter(_entry(value=1))

    reset_queue_for_tests()  # 模拟进程重启
    entries = await snapshot_queue()
    assert len(entries) == 1 and entries[0].value == 3


async def test_queue_is_bounded_drops_oldest(desktop_env, monkeypatch):
    monkeypatch.setenv("STATS_QUEUE_MAX_ENTRIES", "2")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        # now_ms 打桩成递增序列，让「最旧」可判定
        tick = iter(range(1, 10_000))
        monkeypatch.setattr(stats_queue, "now_ms", lambda: next(tick))

        await stats_queue.enqueue_counter(QueueEntry(day=utc_day(), client_type="desktop", metric="logins", value=1))
        await stats_queue.enqueue_counter(QueueEntry(day=utc_day(), client_type="desktop", metric="sessions_created", value=1))
        await stats_queue.enqueue_counter(QueueEntry(day=utc_day(), client_type="desktop", metric="messages_sent", value=1))

        entries = await snapshot_queue()
        metrics = {e.metric for e in entries}
        assert len(entries) == 2
        assert "logins" not in metrics, "超限必须丢最旧（最早入队的 logins）"
    finally:
        get_settings.cache_clear()


# ─── 本地心跳聚合（4.2）────────────────────────────────────────────────────


async def test_desktop_heartbeat_aggregates_into_queue(desktop_client, desktop_env):
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import StatsDailyCounter

    resp = await desktop_client.post("/api/stats/heartbeat", json={"intervalMinutes": 5})
    assert resp.status_code == 200
    assert resp.json()["activeMinutes"] == 5

    entries = await snapshot_queue()
    assert len(entries) == 1
    assert entries[0].metric == "active_minutes"
    assert entries[0].client_type == "desktop"
    assert entries[0].value == 5

    async with get_db() as session:
        rows = (await session.execute(select(StatsDailyCounter))).scalars().all()
    assert rows == [], "桌面心跳不入云直达路径"


# ─── reporter（4.3 / 4.4 / 4.5）────────────────────────────────────────────


@pytest.fixture
def _reset_reporter_transport():
    yield
    stats_reporter.set_test_transport(None)


def _capture_transport(captured: list, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, json={"ok": True})

    return httpx.MockTransport(handler)


async def test_reporter_sends_batch_with_cached_jwt_and_acks(desktop_env, _reset_reporter_transport):
    captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(captured))
    write_cloud_session(
        {"email": "u@example.com", "name": "U"},
        {"access_token": "cloud-jwt-abc", "refresh_token": "r"},
    )
    await stats_queue.enqueue_counter(_entry(value=2))
    await stats_queue.enqueue_counter(QueueEntry(day=utc_day(), client_type="desktop", metric="logins", value=1))

    sent = await stats_reporter.flush_once()

    assert sent == 2
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://cloud.example.com/api/stats/batch"
    assert request.headers["authorization"] == "Bearer cloud-jwt-abc"
    body = json.loads(request.content)
    # 载荷审查（任务 5.3）：上报内容仅计数键值，无任何文本字段
    assert set(body.keys()) == {"nonce", "entries"}
    for e in body["entries"]:
        assert set(e.keys()) == {"day", "clientType", "metric", "value"}
        assert isinstance(e["value"], int)
    assert {e["metric"] for e in body["entries"]} == {"messages_sent", "logins"}
    assert body["nonce"], "nonce 仅供日志排障"

    # 成功 ack 清队列
    assert await snapshot_queue() == []
    # 空队列下一轮不发送
    assert await stats_reporter.flush_once() == 0
    assert len(captured) == 1


async def test_reporter_offline_queues_then_replays_correctly(desktop_env, _reset_reporter_transport):
    """断网排队 → 恢复补发：同一条目补发且不丢不重（云端累加语义见 ingest 测试）。"""
    captured: list[httpx.Request] = []
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
    await stats_queue.enqueue_counter(_entry(value=2))

    def offline_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    stats_reporter.set_test_transport(httpx.MockTransport(offline_handler))
    assert await stats_reporter.flush_once() == 0
    entries = await snapshot_queue()
    assert len(entries) == 1 and entries[0].value == 2, "断网时队列必须保留"

    # 网络恢复 → 同一批补发成功并清队列
    stats_reporter.set_test_transport(_capture_transport(captured))
    assert await stats_reporter.flush_once() == 1
    body = json.loads(captured[0].content)
    assert body["entries"][0]["value"] == 2
    assert await snapshot_queue() == []


async def test_reporter_401_clears_cache_and_pauses(desktop_env, _reset_reporter_transport):
    captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(captured, status_code=401))
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "stale-jwt"})
    await stats_queue.enqueue_counter(_entry(value=1))

    assert await stats_reporter.flush_once() == 0
    assert read_cloud_session() is None, "401 必须清缓存暂停上报"
    assert len(await snapshot_queue()) == 1, "队列保留，等下次登录后补发"

    # 缓存已清 → 后续轮次直接跳过（不再发请求）
    assert await stats_reporter.flush_once() == 0
    assert len(captured) == 1

    # 重新登录恢复上报
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "fresh-jwt"})
    stats_reporter.set_test_transport(_capture_transport(captured))
    assert await stats_reporter.flush_once() == 1


async def test_reporter_without_login_or_cloud_config_skips(
    desktop_env, _reset_reporter_transport, monkeypatch
):
    captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(captured))
    await stats_queue.enqueue_counter(_entry(value=1))

    # 未登录：排队等待
    assert await stats_reporter.flush_once() == 0
    assert len(captured) == 0

    # 未配置云端地址：同样排队等待
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
    monkeypatch.setenv("AGENTHUB_CLOUD_API_URL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert await stats_reporter.flush_once() == 0
        assert len(captured) == 0
    finally:
        get_settings.cache_clear()


async def test_reporter_failures_never_raise_and_keep_queue(desktop_env, _reset_reporter_transport):
    """reporter 失败绝不阻塞本地链路：任意异常被吞、队列完好。"""

    def broken_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected crash inside transport")

    stats_reporter.set_test_transport(httpx.MockTransport(broken_handler))
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
    await stats_queue.enqueue_counter(_entry(value=1))

    # flush_once 只吞网络错误；运行循环兜底吞一切 → 模拟运行循环调用
    try:
        await stats_reporter.flush_once()
    except Exception:
        pass
    assert len(await snapshot_queue()) == 1


async def test_reporter_batches_large_backlog(desktop_env, _reset_reporter_transport):
    """积压超过单批上限时分批补发（补发风暴对冲）。"""
    captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(captured))
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})

    day = utc_day()
    for i in range(stats_reporter.MAX_ENTRIES_PER_BATCH + 3):
        await stats_queue.enqueue_counter(
            QueueEntry(day=day, client_type="desktop", metric=f"metric_{i}", value=1)
        )

    assert await stats_reporter.flush_once() == stats_reporter.MAX_ENTRIES_PER_BATCH
    assert len(captured) == 1 and len(json.loads(captured[0].content)["entries"]) == stats_reporter.MAX_ENTRIES_PER_BATCH

    # 余量下一批补完
    assert await stats_reporter.flush_once() == 3
    assert await snapshot_queue() == []


async def test_reporter_lifecycle_start_stop(desktop_env, _reset_reporter_transport, monkeypatch):
    """reporter 后台任务启停；运行中的异常不终止任务（异常隔离）。"""
    monkeypatch.setenv("STATS_REPORT_INTERVAL_SECONDS", "0.05")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        def broken_handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("boom")

        stats_reporter.set_test_transport(httpx.MockTransport(broken_handler))
        write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
        await stats_queue.enqueue_counter(_entry(value=1))

        reporter = stats_reporter.get_stats_reporter()
        reporter.start()
        await asyncio.sleep(0.3)  # 跑若干轮，每轮都抛 RuntimeError

        task = reporter._task
        assert task is not None and not task.done(), "循环内异常不能杀死 reporter"

        await reporter.stop()
        assert reporter._task is None
    finally:
        get_settings.cache_clear()


# ─── 登出（4.5 / desktop-backend-sidecar delta）────────────────────────────


async def test_logout_clears_session_and_queue_stops_reporting(
    desktop_client, desktop_env, _reset_reporter_transport
):
    from app.api import auth_proxy

    captured: list[httpx.Request] = []
    auth_proxy.set_test_transport(_capture_transport(captured))
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
    await stats_queue.enqueue_counter(_entry(value=5))

    resp = await desktop_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert read_cloud_session() is None, "登出必须清云端会话缓存"
    assert await snapshot_queue() == [], "登出必须清本地计数队列（防跨账号误归属）"

    # 上报停止：无 JWT → flush 不发送（新捕获列表，与登出请求区分开）
    reporter_captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(reporter_captured))
    await stats_queue.enqueue_counter(_entry(value=1))
    assert await stats_reporter.flush_once() == 0
    assert len(reporter_captured) == 0


async def test_offline_logout_still_clears_session_and_queue(desktop_client, desktop_env):
    from app.api import auth_proxy

    def offline_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    auth_proxy.set_test_transport(httpx.MockTransport(offline_handler))
    write_cloud_session({"email": "u@example.com", "name": "U"}, {"access_token": "jwt"})
    await stats_queue.enqueue_counter(_entry(value=5))

    resp = await desktop_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert read_cloud_session() is None
    assert await snapshot_queue() == []
    assert not cloud_session_path().exists()


async def test_proxy_login_caches_cloud_jwt_for_reporter(
    desktop_client, desktop_env, _reset_reporter_transport
):
    """认证代理登录响应缓存云端 JWT（desktop-backend-sidecar delta）。"""
    from app.api import auth_proxy

    def cloud_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "user": {"id": "u1", "email": "u@example.com", "name": "U", "avatarUrl": None},
                "tokens": {"access_token": "cloud-jwt-1", "refresh_token": "r1", "token_type": "bearer"},
                "config": {"allowRegistration": True, "vipLoginEnabled": False},
            },
        )

    auth_proxy.set_test_transport(httpx.MockTransport(cloud_handler))
    resp = await desktop_client.post(
        "/api/auth/login", json={"email": "u@example.com", "password": "pw"}
    )
    assert resp.status_code == 200

    session = read_cloud_session()
    assert session is not None
    assert session["accessToken"] == "cloud-jwt-1"
    assert session["refreshToken"] == "r1"

    # reporter 直接复用该缓存 JWT 上报
    captured: list[httpx.Request] = []
    stats_reporter.set_test_transport(_capture_transport(captured))
    await stats_queue.enqueue_counter(_entry(value=1))
    assert await stats_reporter.flush_once() == 1
    assert captured[0].headers["authorization"] == "Bearer cloud-jwt-1"
