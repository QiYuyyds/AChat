"""进程内 run_id → trace_id 桥 (设计文档 §14.1.2, 任务 1.2 决策)。

AChat 的 AgentRun 表与任何 HTTP 端点都不暴露 OTel trace_id; trace_id 仅
存在于进程内 span。根 span ``agent.run`` 携带 ``agenthub.run_id`` 属性
(``agent_runner.execute_run``), 本模块向全局 TracerProvider 注册一个
SpanProcessor, 在 span 结束时捕获 ``run_id → trace_id`` 映射 — 零侵入,
不修改 AChat 代码。

降级通道: 按 ``attributes["agenthub.run_id"]`` 过滤 Phoenix
``get_spans_dataframe`` (BatchSpanProcessor 异步导出有延迟, 需重试)。

生命周期: `install_trace_bridge()` 在装配时调用一次 (幂等);
`wait_for_trace_id()` 供 runner 短轮询等待 (run 结束事件先于根 span 退出
到达, 需等 span 收尾); `reset_bridge()` 仅供测试。
"""

from __future__ import annotations

import asyncio
import logging
import threading

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor

logger = logging.getLogger(__name__)

_RUN_ID_ATTR = "agenthub.run_id"


class RunTraceBridge(SpanProcessor):
    """捕获 span 属性 ``agenthub.run_id`` → OTel trace_id 的映射。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, str] = {}

    # ── SpanProcessor 协议 ───────────────────────────────────────────────
    def on_start(self, span, parent_context=None) -> None:  # noqa: ARG002
        pass

    def on_end(self, span) -> None:
        try:
            attrs = getattr(span, "attributes", None) or {}
            # AChat start_span 原样设置 kwargs → 真实属性是裸名 run_id;
            # agenthub.run_id 仅为设计文档约定, 两者都接受。
            run_id = attrs.get("run_id") or attrs.get(_RUN_ID_ATTR)
            if not run_id:
                return
            ctx = None
            if hasattr(span, "get_span_context"):
                ctx = span.get_span_context()
            elif hasattr(span, "context"):
                ctx = span.context
            tid = getattr(ctx, "trace_id", 0) if ctx is not None else 0
            if not tid:
                return
            with self._lock:
                self._map[str(run_id)] = trace.format_trace_id(tid)
        except Exception:  # noqa: BLE001 - 桥绝不影响被观测进程
            logger.debug("RunTraceBridge.on_end failed", exc_info=True)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    # ── 查询 ────────────────────────────────────────────────────────────
    def get(self, run_id: str) -> str | None:
        with self._lock:
            return self._map.get(run_id)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()


_bridge = RunTraceBridge()
_installed = False


def install_trace_bridge() -> bool:
    """向全局 TracerProvider 注册桥 (幂等)。

    Returns:
        True 表示已注册 (或此前已注册); False 表示全局 provider 不支持
        add_span_processor (trace 未初始化 / OTel 默认 provider)。
    """
    global _installed
    if _installed:
        return True
    provider = trace.get_tracer_provider()
    add = getattr(provider, "add_span_processor", None)
    if add is None:
        return False
    add(_bridge)
    _installed = True
    logger.info("RunTraceBridge installed on global TracerProvider")
    return True


def bridge_installed() -> bool:
    return _installed


def get_trace_id_for_run(run_id: str) -> str | None:
    """立即查询映射 (无等待)。"""
    return _bridge.get(run_id)


async def wait_for_trace_id(
    run_id: str,
    timeout: float = 10.0,
    interval: float = 0.25,
) -> str | None:
    """短轮询等待 span 收尾后出现映射 (run 结束事件先于根 span 退出)。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        tid = _bridge.get(run_id)
        if tid:
            return tid
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(interval)


def reset_bridge() -> None:
    """清空映射并卸载 (仅供测试)。"""
    global _installed
    _bridge.clear()
    _installed = False
