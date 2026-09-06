"""Usage-stats service (usage-stats capability).

三个职责（design add-stats-ingest）：

1. **计数存储**：`stats_daily_counters` 复合主键 upsert 累加（行数按
   用户 × 日有界，绝不追加事件明细）。
2. **统一埋点入口**：`record_counter` 按 D4 双模式分流——web（云端部署）
   直写云端 PG；桌面模式写本地 JSON 队列，由后台 reporter 批量上报。
   埋点异常完全隔离（任务 2.3）：计数失败只记日志，绝不阻塞业务主路径。
3. **心跳折算**：前台活跃心跳 → `active_minutes`（间隔值服务端钳制，
   防伪造超大值）。

隐私边界：本模块与 `stats_daily_counters` 表不存在任何自由文本 / 内容 /
token 字段；指标名白名单在载荷模型（app.schemas.stats）层强制。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.desktop import is_desktop_mode
from app.config import get_settings
from app.db.engine import get_remote_db
from app.db.models import StatsDailyCounter
from app.services.stats_queue import QueueEntry, enqueue_counter

logger = logging.getLogger(__name__)

METRICS = ("logins", "active_minutes", "sessions_created", "messages_sent")


def utc_day() -> str:
    """计数键的自然日口径：UTC 日期（design D7）。"""
    return datetime.now(UTC).strftime("%Y-%m-%d")


# ─── Upsert 累加 ───────────────────────────────────────────────────────────


async def upsert_daily_counters(
    user_id: str, rows: Iterable[QueueEntry]
) -> None:
    """按 (user_id, day, client_type, metric) upsert 累加计数值。

    同键多条先聚合——PG 的 ON CONFLICT DO UPDATE 不允许单语句两次命中同一行。
    """
    merged: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        merged[(row.day, row.client_type, row.metric)] += row.value
    if not merged:
        return

    async with get_remote_db() as db:
        dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
        if dialect == "postgresql":
            insert = pg_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            insert = sqlite_insert

        for (day, client_type, metric), value in merged.items():
            stmt = insert(StatsDailyCounter).values(
                user_id=user_id,
                day=day,
                client_type=client_type,
                metric=metric,
                value=value,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "day", "client_type", "metric"],
                set_={"value": StatsDailyCounter.value + stmt.excluded.value},
            )
            await db.execute(stmt)


# ─── 统一埋点入口（D4 双模式 + 异常隔离） ──────────────────────────────────


async def record_counter(
    user_id: str | None,
    metric: str,
    *,
    client_type: str,
    value: int = 1,
) -> None:
    """业务路径埋点入口（登录 / 建会话 / 发消息共用，按部署形态分流）。

    web（云端部署）→ 直写云端 PG；桌面模式 → 本地队列（user 归属由
    reporter 上报时的云端 JWT 决定）。任何异常只记日志，不向调用方传播。
    """
    try:
        if metric not in METRICS:
            raise ValueError(f"metric not in whitelist: {metric}")
        if is_desktop_mode():
            await enqueue_counter(
                QueueEntry(day=utc_day(), client_type="desktop", metric=metric, value=value)
            )
        else:
            if not user_id:
                return
            await upsert_daily_counters(
                user_id,
                [QueueEntry(day=utc_day(), client_type=client_type, metric=metric, value=value)],
            )
    except Exception:
        logger.warning("stats record_counter failed (metric=%s) — ignored", metric, exc_info=True)


async def record_heartbeat(user_id: str | None, interval_minutes: int) -> int:
    """前台活跃心跳 → active_minutes。

    间隔值先经服务端钳制（防伪造超大值），web 直写云端，桌面聚合进本地
    队列随批量上报。返回折算分钟数。异常隔离同 record_counter。
    """
    try:
        clamped = max(1, min(interval_minutes, get_settings().stats_heartbeat_max_minutes))
        await record_counter(user_id, "active_minutes", client_type="web", value=clamped)
        return clamped
    except Exception:
        logger.warning("stats record_heartbeat failed — ignored", exc_info=True)
        return 0


# ─── 简单限流（进程内滑动窗口，超限 429 不落库） ───────────────────────────


class RateLimiter:
    """同用户上报速率上限（design: 补发风暴对冲之一，进程内即可）。"""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        """True = 放行；False = 超限（调用方返回 429 且 MUST NOT 落库）。"""
        now = time.monotonic()
        async with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) > 10_000:  # 防用户数无界增长
                    self._hits.clear()
                hits = deque()
                self._hits[key] = hits
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


def build_stats_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        max_requests=settings.stats_rate_limit_per_minute,
        window_seconds=60.0,
    )
