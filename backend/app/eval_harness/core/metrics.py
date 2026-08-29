"""
Statistical metrics for the Aeval evaluation framework.

Provides pass@k and pass^k calculations for aggregating trial results,
as well as helper functions for metric aggregation.
"""

from __future__ import annotations

from typing import Any

from eval_harness.core.types import TrialResult


# ─── pass@k / pass^k ──────────────────────────────────────────────────────────


def pass_at_k(trials: list[TrialResult], k: int) -> float:
    """
    pass@k: k 次尝试中至少成功一次的任务比例。

    用于能力评估 — "Agent 有没有机会完成这个任务?"

    计算逻辑 (设计文档 §4.3 修正版):
    - k <= n (实际 trial 数): 直接判定, 至少一次成功即 1.0
    - k > n: 二项分布外推 P(至少一次成功) = 1 - (1-p)^k,
      其中 p = successes / n (单次成功概率的极大似然估计)

    Args:
        trials: trial 结果列表
        k: 尝试次数

    Returns:
        0.0-1.0 之间的通过率

    Examples:
        >>> pass_at_k([fail, success, success], k=1)  # 有成功即通过
        1.0
        >>> pass_at_k([fail, fail, fail], k=3)
        0.0
        >>> pass_at_k([success, fail, fail], k=5)  # 1/3 成功, 外推
        0.868...
    """
    n = len(trials)
    if n == 0 or k <= 0:
        return 0.0

    successes = sum(1 for t in trials if t.success)

    if k <= n:
        return 1.0 if successes > 0 else 0.0

    # k > n: 二项分布外推
    p = successes / n
    if p == 0:
        return 0.0
    if p == 1:
        return 1.0
    return 1.0 - (1.0 - p) ** k


def pass_power_k(trials: list[TrialResult], k: int) -> float:
    """
    pass^k: k 次尝试全部成功的任务比例。

    用于回归评估 — "Agent 每次都能可靠完成吗?"

    计算逻辑 (设计文档 §4.3 修正版):
    - k <= n (实际 trial 数): 直接判定, 前 k 次全部成功即 1.0
    - k > n: 二项分布外推 P(全部成功) = p^k

    Args:
        trials: trial 结果列表
        k: 尝试次数

    Returns:
        0.0-1.0 之间的通过率

    Examples:
        >>> pass_power_k([success, success, success], k=3)  # 全部成功
        1.0
        >>> pass_power_k([success, fail, success], k=3)    # 有失败
        0.0
        >>> pass_power_k([success, success, fail], k=5)    # 2/3 成功, 外推
        0.132...
    """
    n = len(trials)
    if n == 0 or k <= 0:
        return 0.0

    successes = sum(1 for t in trials if t.success)

    if k <= n:
        k_trials = trials[:k]
        return 1.0 if all(t.success for t in k_trials) else 0.0

    # k > n: 二项分布外推
    p = successes / n
    if p == 0:
        return 0.0
    return p**k


# ─── Metric Aggregation ───────────────────────────────────────────────────────


def aggregate_metrics(trials: list[TrialResult]) -> dict[str, float]:
    """
    聚合多个 trial 的过程指标。

    对每个指标计算 avg/min/max。

    Args:
        trials: trial 结果列表

    Returns:
        聚合后的指标字典，格式: {metric_name_{avg|min|max}: value}
    """
    if not trials:
        return {}

    # 收集所有指标 key
    all_keys: set[str] = set()
    for t in trials:
        all_keys.update(t.metrics.keys())

    result: dict[str, float] = {}
    for key in all_keys:
        values = [t.metrics[key] for t in trials if key in t.metrics]
        if values:
            result[f"{key}_avg"] = sum(values) / len(values)
            result[f"{key}_min"] = min(values)
            result[f"{key}_max"] = max(values)

    return result


def extract_metrics(
    spans: list[dict[str, Any]],
    tracked_metrics: list[str],
) -> dict[str, float]:
    """
    从 trace span 中提取过程指标。

    Args:
        spans: trace span 列表
        tracked_metrics: 要提取的指标名称列表

    Returns:
        提取到的指标字典
    """
    metrics: dict[str, float] = {}

    # 基础统计
    n_turns = 0
    n_toolcalls = 0
    n_total_tokens = 0

    for span in spans:
        name = span.get("name", "")
        attrs = span.get("attributes", {})

        # 对话轮次
        if "turn" in name.lower() or "message" in name.lower():
            n_turns += 1

        # 工具调用
        if "tool.call" in name or "tool_call" in name:
            n_toolcalls += 1

        # Token 用量
        tokens = attrs.get("agenthub.total_tokens") or attrs.get("llm.usage.total_tokens")
        if tokens:
            try:
                n_total_tokens += int(tokens)
            except (ValueError, TypeError):
                pass

    if "n_turns" in tracked_metrics:
        metrics["n_turns"] = float(n_turns)
    if "n_toolcalls" in tracked_metrics:
        metrics["n_toolcalls"] = float(n_toolcalls)
    if "n_total_tokens" in tracked_metrics:
        metrics["n_total_tokens"] = float(n_total_tokens)

    return metrics
