"""桌面模式本地计数队列（usage-stats，design D4）。

埋点在桌面模式下写入本文件管理的 JSON 队列（data dir 下），后台 reporter
批量上报云端。特点：

- **聚合存储**：按 (day, client_type, metric) 键聚合计数值，条目数天然有界
- **崩溃安全**：临时文件 + os.replace 原子替换，半写状态不会损坏队列
- **有界**：键数上限（默认 10k），超限丢最旧（updatedAt 最小）并记日志
- **进程内互斥**：asyncio.Lock 保护读改写；快照 / 扣减（ack）为原子操作

队列条目不含任何用户归属字段——归属由 reporter 上报时的云端 JWT 决定。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from app.config import get_settings
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

QUEUE_FILE_NAME = "stats_queue.json"
QUEUE_VERSION = 1


@dataclass
class QueueEntry:
    """一条聚合计数（无用户归属——由上报时 JWT 决定）。"""

    day: str
    client_type: str
    metric: str
    value: int


_lock = asyncio.Lock()
# key -> [value, updated_at_ms]
_counters: dict[tuple[str, str, str], list] = {}
_loaded = False


def queue_path():
    return get_settings().data_path / QUEUE_FILE_NAME


def _key(entry: QueueEntry) -> tuple[str, str, str]:
    return (entry.day, entry.client_type, entry.metric)


async def _ensure_loaded() -> None:
    """首次访问时从磁盘加载（损坏文件按空队列处理，不抛错）。"""
    global _loaded
    if _loaded:
        return
    try:
        raw = queue_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("version") == QUEUE_VERSION:
            for item in data.get("entries", []):
                try:
                    key = (item["day"], item["clientType"], item["metric"])
                    value = int(item["value"])
                    updated_at = int(item.get("updatedAt", 0))
                except (KeyError, TypeError, ValueError):
                    continue
                existing = _counters.get(key)
                if existing is None:
                    _counters[key] = [value, updated_at]
                else:
                    existing[0] += value
    except (OSError, ValueError):
        pass
    _loaded = True


def _persist_locked() -> None:
    """原子写盘（caller 持锁）。写失败仅记日志——内存态仍是权威，下次操作重写。"""
    entries = [
        {
            "day": day,
            "clientType": client_type,
            "metric": metric,
            "value": value,
            "updatedAt": updated_at,
        }
        for (day, client_type, metric), (value, updated_at) in _counters.items()
    ]
    payload = {"version": QUEUE_VERSION, "entries": entries}
    path = queue_path()
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("stats queue persist failed", exc_info=True)


def _enforce_bound_locked() -> None:
    max_entries = get_settings().stats_queue_max_entries
    while len(_counters) > max_entries:
        oldest = min(_counters.items(), key=lambda kv: kv[1][1])[0]
        del _counters[oldest]
        logger.warning("stats queue over limit (%d) — dropped oldest key %s", max_entries, oldest)


async def enqueue_counter(entry: QueueEntry) -> None:
    """聚合入队（埋点入口）。超界丢最旧；每次落盘。"""
    async with _lock:
        await _ensure_loaded()
        slot = _counters.get(_key(entry))
        if slot is None:
            _counters[_key(entry)] = [entry.value, now_ms()]
        else:
            slot[0] += entry.value
            slot[1] = now_ms()
        _enforce_bound_locked()
        _persist_locked()


async def snapshot_queue() -> list[QueueEntry]:
    """当前队列快照（reporter 上报用；不改变队列内容）。"""
    async with _lock:
        await _ensure_loaded()
        return [
            QueueEntry(day=day, client_type=client_type, metric=metric, value=value)
            for (day, client_type, metric), (value, _updated) in _counters.items()
        ]


async def subtract_entries(entries: list[QueueEntry]) -> None:
    """成功 ack 后扣减已上报值（并发新增的计数保留）。扣到 0 的键移除。"""
    if not entries:
        return
    async with _lock:
        await _ensure_loaded()
        for entry in entries:
            slot = _counters.get(_key(entry))
            if slot is None:
                continue
            slot[0] -= entry.value
            if slot[0] <= 0:
                del _counters[_key(entry)]
        _persist_locked()


async def clear_queue() -> None:
    """清空内存态与磁盘文件（登出防跨账号误归属）。文件不存在时静默。"""
    global _loaded
    async with _lock:
        _counters.clear()
        _loaded = True
        try:
            queue_path().unlink()
        except OSError:
            pass


def reset_queue_for_tests() -> None:
    """测试隔离：清内存态（disk 由 tmp_path 隔离）。"""
    global _loaded
    _counters.clear()
    _loaded = False
