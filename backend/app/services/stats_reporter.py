"""桌面统计后台 reporter（usage-stats，design D4/D5）。

桌面模式启动时随 lifespan 启动的后台任务：周期性（带随机 jitter）把本地
计数队列批量 POST 到云端 `/api/stats/batch`。

- **JWT 复用 ③ 的会话缓存**：只读 `cloud_session.json` 的 accessToken，
  不新增缓存文件；未登录 / 未配置云端 → 排队等待
- **成功 ack 清队列**：subtract 扣减已上报值，期间新增的计数保留
- **401 → 清缓存暂停上报**：等下次代理登录/刷新时更新缓存后自动恢复
- **断网排队**：网络失败 / 429 / 5xx 保留队列下轮再报；队列有界
- **异常隔离**：任何失败只记日志，绝不阻塞本地消息主链路
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets

import httpx

from app.auth.desktop import clear_cloud_session, read_cloud_session
from app.config import get_settings
from app.services.stats_queue import QueueEntry, snapshot_queue, subtract_entries

logger = logging.getLogger(__name__)

# 单次上报条数上限（补发风暴对冲：大量键积压时分批补发）
MAX_ENTRIES_PER_BATCH = 500

# 测试注入点：替换 httpx transport（MockTransport 模拟云端）
_test_transport: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _test_transport
    _test_transport = transport


class StatsReporter:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="stats-reporter")
        logger.info("stats reporter started (desktop mode)")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        task = self._task
        self._task = None
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()

    async def _run(self) -> None:
        base_interval = get_settings().stats_report_interval_seconds
        while not self._stop_event.is_set():
            # jitter：报告时刻随机化，对冲大量设备同时恢复网络的补发风暴
            jitter = random.uniform(0, base_interval * 0.2)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=base_interval + jitter)
                break  # stop requested
            except TimeoutError:
                pass
            try:
                await flush_once()
            except Exception:
                logger.warning("stats reporter cycle failed — ignored", exc_info=True)

    # reporter 自身不持有状态；flush_once 为无状态函数便于测试直接调用


async def flush_once() -> int:
    """尝试上报一轮。返回成功上报的条目数；失败返回 0（队列保留）。"""
    settings = get_settings()
    entries = await snapshot_queue()
    if not entries:
        return 0

    session = read_cloud_session()
    token = (session or {}).get("accessToken")
    base = (settings.cloud_api_url or "").rstrip("/")
    if not token or not base:
        logger.debug("stats reporter skipping (logged-out or cloud unconfigured); %d queued", len(entries))
        return 0

    sent: list[QueueEntry] = entries[:MAX_ENTRIES_PER_BATCH]
    payload = {
        "nonce": secrets.token_hex(8),  # 仅供云端日志排障（design: 接受重复近似）
        "entries": [
            {
                "day": e.day,
                "clientType": e.client_type,
                "metric": e.metric,
                "value": e.value,
            }
            for e in sent
        ],
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=_test_transport,
        ) as client:
            resp = await client.post(
                f"{base}/api/stats/batch",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError:
        logger.info("stats report failed (network) — %d entries stay queued", len(sent))
        return 0

    if 200 <= resp.status_code < 300:
        await subtract_entries(sent)
        logger.debug("stats report acked (%d entries)", len(sent))
        return len(sent)
    if resp.status_code == 401:
        # token 失效：清缓存暂停上报，等下次代理登录/刷新时更新（design D5）
        logger.info("stats report rejected (401) — clearing cached cloud session")
        clear_cloud_session()
        return 0
    # 429 / 5xx 等：保留队列下轮再报
    logger.info("stats report not accepted (http %d) — %d entries stay queued", resp.status_code, len(sent))
    return 0


# 模块级单例（lifespan start/stop）
_reporter: StatsReporter | None = None


def get_stats_reporter() -> StatsReporter:
    global _reporter
    if _reporter is None:
        _reporter = StatsReporter()
    return _reporter
