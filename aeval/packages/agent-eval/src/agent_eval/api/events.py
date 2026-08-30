"""Run 事件总线 — SSE 实时进度的进程内 fan-out (任务 3.1)。

协议 (设计文档 D3 / §17.3):
    - EvalRunner 进度回调 → per-run 订阅者队列扇出; 事件不落库, 断线恢复
      完全依赖快照重拉 (GET /runs/{run_id}) + 重订阅
    - 事件类型: task_start / trial_start / trial_complete / task_complete /
      run_complete / error; 载荷含 task_id 与 trial_index (适用时), 按
      (task_id, trial_index) 幂等 — 重复事件由客户端快照自愈兜底
    - run 生命周期由服务端后台任务持有, 与观察连接解耦

终态保留: run_complete 事件缓存于进程内 (有界, LIFO 淘汰), 供 run 结束后
订阅的客户端立即收终态并关流; 进程重启后缓存消失, 客户端以快照为准。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

# 有界队列: 慢订阅者丢最旧事件 (快照可自愈), 不阻塞 run 主流程
_QUEUE_MAXSIZE = 256
# 终态事件缓存上限 (防止长进程内存无界增长)
_TERMINAL_CACHE_MAX = 500

TERMINAL_EVENT_TYPES = {"run_complete"}


class RunEventBus:
    """run_id → 订阅者队列集合的事件扇出枢纽 (单事件循环, 无锁)。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._terminal: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ── 发布 ─────────────────────────────────────────────────────────────

    def publish(self, run_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
        """发布一条 run 事件 (非阻塞)。"""
        event: dict[str, Any] = {
            "type": event_type,
            "run_id": run_id,
            "timestamp": time.time() * 1000,
            **(data or {}),
        }
        if event_type in TERMINAL_EVENT_TYPES:
            self._terminal[run_id] = event
            self._terminal.move_to_end(run_id)
            while len(self._terminal) > _TERMINAL_CACHE_MAX:
                self._terminal.popitem(last=False)

        for queue in self._subscribers.get(run_id, set()):
            _offer(queue, event)

    # ── 订阅 ─────────────────────────────────────────────────────────────

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """注册订阅者队列; run 已有缓存终态时立即注入终态事件。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.setdefault(run_id, set()).add(queue)
        terminal = self._terminal.get(run_id)
        if terminal is not None:
            _offer(queue, terminal)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        """注销订阅者 (SSE 断开时调用, 防泄漏)。"""
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, set()))

    def terminal_event(self, run_id: str) -> dict[str, Any] | None:
        return self._terminal.get(run_id)


def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    """put_nowait + 最旧丢弃溢出策略 (与 AChat event_bus 行为一致)。"""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - 理论不可达
            pass


# 全局单例 (与 eval API 子应用同生命周期)
run_event_bus = RunEventBus()
