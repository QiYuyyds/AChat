"""Trace Mining source — mine eval items from real Agent traces.

Three first-version strategies (D4):
- failed_tasks:    traces containing error spans
- long_running:    traces whose duration exceeds P90 × multiplier
- diverse_sampling: deterministic hash-based uniform sampling

The prompt comes from the ROOT span's input attribute; traces without one are
counted as `skipped` in the mining report (never guessed). Every mined item
keeps trace provenance (source_type=trace_mining, source_ref=trace_id).
`user_dissatisfied` stays as an enum placeholder until a user-feedback
data channel exists (design §18, first-version scope note).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent_eval.core.contract import TraceProvider
from agent_eval.dataset.models import EvalDatasetItem, SourceType, now_ms


class MiningStrategy(str, Enum):
    """挖掘策略 (首版 3 个; user_dissatisfied 依赖用户反馈通道, 留枚举位)"""

    FAILED_TASKS = "failed_tasks"
    LONG_RUNNING = "long_running"
    DIVERSE_SAMPLING = "diverse_sampling"
    USER_DISSATISFIED = "user_dissatisfied"  # 首版未实现


# 根 span input 属性的候选键 (Phoenix/OpenInference 惯例优先)
_INPUT_ATTR_KEYS = ("input.value", "input", "agent.input", "llm.input_messages")


class TraceMiner:
    """从 TraceProvider 提供的 trace 中按策略挖掘评测条目"""

    def __init__(
        self,
        trace_provider: TraceProvider,
        long_running_multiplier: float = 2.0,
    ):
        """
        Args:
            trace_provider: trace 数据源 (get_trace_ids + get_spans)
            long_running_multiplier: long_running 策略的 P90 倍数阈值
        """
        self.trace_provider = trace_provider
        self.long_running_multiplier = max(1.0, long_running_multiplier)

    async def mine(
        self,
        strategy: str | MiningStrategy,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
        candidate_limit: int = 100,
    ) -> MiningReport:
        """
        执行挖掘。

        Args:
            strategy: 挖掘策略名
            filters: 传给 get_trace_ids 的过滤条件
            limit: 最多产出的条目数
            candidate_limit: 最多检查的候选 trace 数 (先过滤再拉 spans, 控制成本)

        Returns:
            MiningReport: items + skipped + 统计
        """
        strategy_enum = MiningStrategy(strategy)
        if strategy_enum == MiningStrategy.USER_DISSATISFIED:
            raise NotImplementedError(
                "user_dissatisfied mining requires a user-feedback data channel "
                "(not available in the first version — see design §18 scope)"
            )

        trace_ids = await self.trace_provider.get_trace_ids(
            filters=filters, limit=candidate_limit
        )

        # 先拉 spans, 再按策略筛选 (策略内限制候选数, 避免全量展开)
        candidates: list[tuple[str, list[dict[str, Any]]]] = []
        for trace_id in trace_ids:
            spans = await self.trace_provider.get_spans(trace_id)
            if spans:
                candidates.append((trace_id, spans))

        selected = self._select_by_strategy(strategy_enum, candidates, limit)

        items: list[EvalDatasetItem] = []
        skipped: list[dict[str, Any]] = []
        for trace_id, spans in selected:
            root = self._root_span(spans)
            prompt = self._extract_prompt(root) if root is not None else None
            if not prompt:
                skipped.append({
                    "trace_id": trace_id,
                    "reason": "no user input found in root span",
                })
                continue
            items.append(self._trace_to_item(trace_id, spans, strategy_enum, prompt))

        return MiningReport(
            strategy=strategy_enum.value,
            candidates=len(trace_ids),
            inspected=len(candidates),
            items=items,
            skipped=skipped,
        )

    # ── 策略筛选 ──

    def _select_by_strategy(
        self,
        strategy: MiningStrategy,
        candidates: list[tuple[str, list[dict[str, Any]]]],
        limit: int,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        if strategy == MiningStrategy.FAILED_TASKS:
            selected = [(tid, spans) for tid, spans in candidates if self._is_failed(spans)]
        elif strategy == MiningStrategy.LONG_RUNNING:
            selected = self._select_long_running(candidates)
        else:  # DIVERSE_SAMPLING
            selected = self._select_diverse([tid for tid, _ in candidates], limit)
            selected = [(tid, spans) for tid, spans in candidates if tid in selected]

        return selected[:limit]

    def _select_long_running(
        self,
        candidates: list[tuple[str, list[dict[str, Any]]]],
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        timed = [
            (tid, spans, self._trace_duration(spans))
            for tid, spans in candidates
        ]
        timed = [(tid, spans, d) for tid, spans, d in timed if d is not None]
        if not timed:
            return []
        ordered = sorted(d for _, _, d in timed)
        p90 = ordered[max(0, int(len(ordered) * 0.9) - 1)] if len(ordered) > 1 else ordered[0]
        threshold = p90 * self.long_running_multiplier
        return [(tid, spans) for tid, spans, d in timed if d > threshold]

    def _select_diverse(self, trace_ids: list[str], limit: int) -> list[str]:
        """按 trace_id 哈希排序均匀采样 (确定性, 与到达顺序无关)"""
        ranked = sorted(trace_ids, key=lambda tid: hashlib.md5(tid.encode()).hexdigest())
        return ranked[:limit]

    # ── Trace 分析 ──

    @staticmethod
    def _is_failed(spans: list[dict[str, Any]]) -> bool:
        for span in spans:
            status = span.get("status") or {}
            status_code = (
                status.get("status_code")
                if isinstance(status, dict)
                else str(status)
            )
            if str(status_code).upper() in ("ERROR", "STATUS_CODE_ERROR", "2"):
                return True
            attrs = span.get("attributes") or {}
            if attrs.get("error") is True or attrs.get("error.type"):
                return True
        return False

    @staticmethod
    def _trace_duration(spans: list[dict[str, Any]]) -> float | None:
        """trace 时长 (span start/end 解析失败返回 None)"""
        starts: list[float] = []
        ends: list[float] = []
        for span in spans:
            s = TraceMiner._parse_time(span.get("start_time"))
            e = TraceMiner._parse_time(span.get("end_time"))
            if s is not None:
                starts.append(s)
            if e is not None:
                ends.append(e)
        if not starts or not ends:
            return None
        return max(0.0, max(ends) - min(starts))

    @staticmethod
    def _parse_time(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _root_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
        """根 span = 最早开始的 span (归一化 span 不含 parent_id)"""
        if not spans:
            return None
        return min(
            spans,
            key=lambda s: TraceMiner._parse_time(s.get("start_time")) or 0.0,
        )

    @staticmethod
    def _extract_prompt(root: dict[str, Any] | None) -> str | None:
        """从根 span input 属性提取用户输入; 缺失返回 None (不猜)"""
        if root is None:
            return None
        attrs = root.get("attributes") or {}
        for key in _INPUT_ATTR_KEYS:
            value = attrs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                # llm.input_messages: [{"content": ...}] 形态兜底
                first = value[0]
                if isinstance(first, dict):
                    content = first.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
        return None

    def _trace_to_item(
        self,
        trace_id: str,
        spans: list[dict[str, Any]],
        strategy: MiningStrategy,
        prompt: str,
    ) -> EvalDatasetItem:
        root = self._root_span(spans) or {}
        failed = self._is_failed(spans)
        description = (
            f"Mined [{strategy.value}]: {root.get('name', 'unknown span')}"
            + (" (failed trace)" if failed else "")
        )
        return EvalDatasetItem(
            id=f"mined_{strategy.value}_{trace_id[:12]}",
            prompt=prompt,
            description=description,
            graders=[],
            metadata={
                "capabilities": [],
                "mining": {
                    "strategy": strategy.value,
                    "span_count": len(spans),
                    "failed": failed,
                },
            },
            source_type=SourceType.TRACE_MINING,
            source_ref=trace_id,
            created_at=now_ms(),
        )


@dataclass
class MiningReport:
    """挖掘结果报告 — 条目 + skipped 明细 + 统计"""

    strategy: str
    candidates: int = 0
    inspected: int = 0
    items: list[EvalDatasetItem] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "candidates": self.candidates,
            "inspected": self.inspected,
            "mined": len(self.items),
            "skipped_count": len(self.skipped),
            "skipped": self.skipped,
            "item_ids": [i.id for i in self.items],
        }
