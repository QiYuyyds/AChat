"""宿主自有的评测指标 (change ④ 采用路径)。

框架内置指标没有一个声明读轨迹 —— 「judge 看轨迹」要在真流量上被验收,
就得有一个真的读 ``ctx.observations`` 的指标。这个文件就是那个指标:
宿主按 ④ 的宽签名自己实现并注册进 runner 的 metrics_registry,
验收套件 ``metric-acceptance-suite.yaml`` 引用它。
"""

from __future__ import annotations

from typing import Any

from agent_eval.metrics.base import BaseLLMMetric, MetricResult
from agent_eval.metrics.llm_judge import MetricError
from agent_eval.core.types import MeasurementContext

_MAX_OBSERVATIONS = 40
_MAX_VALUE_CHARS = 400


class ProcessQualityMetric(BaseLLMMetric):
    """过程质量 judge：读完整轨迹 + 最终输出，评「做得是否连贯靠谱」。

    - evidence_levels 声明读 transcript —— 框架按声明交付带来源分级的观测;
      未声明通道不进上下文 (④ spec: 未声明的通道不交付)。
    - 评分维度: 是否沿任务目标推进、有没有自相矛盾、工具/上下文使用是否合理。
    """

    name = "process_quality"
    threshold = 0.5
    evidence_levels: tuple[str, ...] | None = ("transcript",)

    async def measure(self, ctx: MeasurementContext) -> MetricResult:
        observations = ctx.observations[-_MAX_OBSERVATIONS:]
        lines: list[str] = []
        for o in observations:
            value = str(o.value) if o.value is not None else ""
            if o.absent_reason:
                value = f"(缺失: {o.absent_reason})"
            lines.append(
                f"[{o.observed_by.value}|{o.kind.value}|{o.channel}] {value[:_MAX_VALUE_CHARS]}"
            )
        trajectory = "\n".join(lines) if lines else "(无轨迹读数)"

        system = (
            "你是严格但公正的评测评审。根据任务输入、执行轨迹与最终输出，"
            "评估被评 agent 的过程质量：是否沿任务目标推进、是否自相矛盾、"
            "对上下文与工具的使用是否合理。只输出 JSON: "
            '{"score": <0.0-1.0>, "reason": "<一句话理由，可引用轨迹中的具体事件>"}'
        )
        user = (
            f"# 任务输入\n{ctx.prompt}\n\n"
            f"# 执行轨迹 (按采集顺序, [来源|通道|通道名] 前缀)\n{trajectory}\n\n"
            f"# 最终输出\n{ctx.actual_output}"
        )
        data = await self._llm_judge(system, user)
        try:
            score = self._score_of(data)
        except MetricError as e:
            raise MetricError(f"process_quality: {e}") from e
        reason = str(data.get("reason", ""))
        return MetricResult(
            name=self.name,
            score=score,
            reason=reason,
            threshold=self.threshold,
            success=score >= self.threshold,
            details={"observed_count": len(observations)},
        )


def build_host_metrics_registry(llm_fn: Any) -> dict[str, Any]:
    """默认注册表 + 宿主自有指标 —— create_aeval_runner 的注入源。"""
    from agent_eval.metrics import build_default_metrics_registry

    registry = build_default_metrics_registry(llm_fn=llm_fn)
    registry["process_quality"] = ProcessQualityMetric(llm_fn=llm_fn)
    return registry
