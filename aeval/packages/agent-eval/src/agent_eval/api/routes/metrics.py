"""
Metric batch evaluation routes.

POST /metrics/batch — Batch-score pre-existing outputs (historical
conversations / logs) with the runner-injected metrics registry.

指标名经 runner.metrics_registry 解析 (与 metric grader 同源装配);
未注册指标名返回 422 并列出无效指标; runner 未装配返回 503。
成本提示: 每条用例 × 每指标一次 judge LLM 调用 (并发上限 4, 同 prompt
经 judge 层缓存命中则跳过)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_eval.metrics.batch_evaluation import (
    BatchEvaluationRequest,
    BatchEvaluator,
    UnknownMetricsError,
)

router = APIRouter()


@router.post("/batch")
async def batch_evaluate(request: BatchEvaluationRequest):
    """批量评测已有输出 (不运行 Agent; 对历史对话/日志补测)。"""
    from agent_eval.api.app import _get_runner
    from agent_eval.metrics.llm_judge import LLMNotConfiguredError

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")
    if not runner.metrics_registry:
        raise HTTPException(
            status_code=503, detail="Metrics registry not configured"
        )

    evaluator = BatchEvaluator(runner.metrics_registry, llm_fn=runner.llm_fn)
    try:
        return await evaluator.evaluate(request)
    except UnknownMetricsError as e:
        raise HTTPException(
            status_code=422,
            detail={"message": str(e), "unknown_metrics": e.unknown},
        )
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
