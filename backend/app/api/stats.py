"""Stats ingest API router (usage-stats capability).

- `POST /api/stats/batch`     聚合计数批量上报（桌面 reporter 用；JWT 认证）
- `POST /api/stats/heartbeat` 前台活跃心跳（web 直达云端；桌面聚合进本地队列）

两个端点共用同用户进程内限流：超限 429 且 MUST NOT 落库。载荷白名单与
封闭字段集见 app.schemas.stats（隐私边界在模型层强制）。

桌面模式例外：get_current_user 解析为固定本地用户（platform-security
delta）；heartbeat 的 active_minutes 聚合进本地队列随批量上报，不入云
直达路径。batch 端点在桌面部署上无人调用（reporter 直连云端）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.schemas.stats import StatsBatchRequest, StatsHeartbeatRequest
from app.services.stats_service import (
    build_stats_rate_limiter,
    record_heartbeat,
    upsert_daily_counters,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 进程内限流器（模块级单例；重启清零——限流语义是近似对冲，非精确配额）
_rate_limiter = build_stats_rate_limiter()


async def _enforce_rate_limit(user_id: str) -> None:
    if not await _rate_limiter.allow(user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Stats upload rate limit exceeded",
        )


@router.post("/stats/batch")
async def ingest_batch(
    payload: StatsBatchRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """接收聚合计数值并 upsert 累加（重复上报同一键 = 数值累加）。"""
    await _enforce_rate_limit(user.id)

    if payload.nonce:
        logger.info(
            "stats batch nonce=%s entries=%d user=%s", payload.nonce, len(payload.entries), user.id
        )

    await upsert_daily_counters(user.id, payload.entries)
    return {"ok": True, "accepted": len(payload.entries)}


@router.post("/stats/heartbeat")
async def heartbeat(
    payload: StatsHeartbeatRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """前台活跃心跳 → active_minutes（间隔值服务端钳制）。"""
    await _enforce_rate_limit(user.id)

    clamped = await record_heartbeat(user.id, payload.interval_minutes)
    return {"ok": True, "activeMinutes": clamped}
