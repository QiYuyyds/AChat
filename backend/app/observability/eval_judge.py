"""Offline LLM-as-judge evaluation — manually triggered via API.

Pulls trace span data from Phoenix, constructs a judge prompt, calls
DashScope (or any OpenAI-compatible LLM) and writes 9 quality scores
back to Phoenix as trace-level eval annotations.

Default disabled (``eval_judge_enabled=False``).  Enabled via API
``POST /api/eval/judge/{trace_id}``.
"""

import asyncio
import json
import logging
from typing import Any

from app.config import get_settings
from app.observability.eval_rules import EvalScore

logger = logging.getLogger(__name__)

_JUDGE_PROMPT_TEMPLATE = """你是一个 Agent 系统的评测专家。请根据以下 Agent 执行 trace 的信息，对 9 个指标进行 0-1 评分（0=最差，1=最好），并给出简短理由。

## Agent 执行概要
- 用户问题: {user_query}
- 工具调用序列: {tool_calls}
- 子任务拆解: {subtasks}
- 最终回答: {final_answer}

## 评测指标
1. tool_selection_accuracy: 模型选的工具对不对
2. subtask_granularity: Orchestrator 拆的子任务粒度是否合理
3. subtask_overlap: 子任务是否有重叠
4. subtask_coverage: 子任务是否覆盖了用户需求
5. aggregation_fidelity: 最终回答是否整合了子 Agent 产出
6. information_loss: 子 Agent 产出在聚合中丢失比例（0=无丢失，1=完全丢失）
7. faithfulness: 回答是否忠于检索内容
8. answer_relevance: 回答是否切题
9. answer_quality: 综合质量

请以 JSON 数组格式返回，每个元素包含 name, score, explanation:
```json
[
  {{"name": "tool_selection_accuracy", "score": 0.8, "explanation": "..."}},
  ...
]
```"""

JUDGE_METRIC_NAMES = [
    "tool_selection_accuracy",
    "subtask_granularity",
    "subtask_overlap",
    "subtask_coverage",
    "aggregation_fidelity",
    "information_loss",
    "faithfulness",
    "answer_relevance",
    "answer_quality",
]


def _get_attr(span: Any, key: str, default: str = "") -> str:
    """Safely get a string attribute from a span-like object."""
    attrs: dict = {}
    if isinstance(span, dict):
        attrs = span.get("attributes", {}) or {}
    else:
        attrs = getattr(span, "attributes", {}) or {}
    val = attrs.get(key, default)
    return str(val) if val is not None else default


def _get_name(span: Any) -> str:
    """Safely get the span name."""
    if isinstance(span, dict):
        return str(span.get("name", ""))
    return str(getattr(span, "name", ""))


def _get_start_time(span: Any) -> str:
    """Safely get the span start time for sorting."""
    if isinstance(span, dict):
        return str(span.get("start_time", ""))
    return str(getattr(span, "start_time", ""))


def _extract_trace_summary(spans: list[Any], trace_data: dict | None = None) -> dict:
    """Extract a summary dict from trace spans for the judge prompt.

    Pulls tool call sequence, subtask dispatches, user query (from first LLM
    input or first user-visible span), and final answer (from last LLM output).
    """
    tool_calls: list[str] = []
    subtasks: list[str] = []
    user_query = ""
    final_answer = ""

    # Sort spans by start_time for chronological order
    sorted_spans = sorted(spans, key=_get_start_time)

    for span in sorted_spans:
        name = _get_name(span)

        if "tool.call" in name:
            tool_calls.append(_get_attr(span, "agenthub.tool_name") or "unknown")
        elif "tool.dispatch" in name:
            subtasks.append(_get_attr(span, "agenthub.task_id") or "unknown")
        elif "llm.generate" in name or "LLM" in name or "CHAT" in name:
            # openinference-instrumentation-openai stores input/output in
            # attributes like input.value / output.value
            if not user_query:
                user_query = _get_attr(span, "input.value") or _get_attr(span, "agenthub.user_query")
            final_answer = _get_attr(span, "output.value") or final_answer

    if trace_data:
        user_query = user_query or trace_data.get("user_query", "")
        final_answer = final_answer or trace_data.get("final_answer", "")

    return {
        "user_query": user_query[:500] or "(unavailable)",
        "tool_calls": ", ".join(tool_calls) if tool_calls else "(none)",
        "subtasks": ", ".join(subtasks) if subtasks else "(none)",
        "final_answer": final_answer[:1000] or "(unavailable)",
    }


def _call_llm_judge(prompt: str) -> list[EvalScore]:
    """Call the LLM judge and parse the 9 scores."""
    settings = get_settings()
    if not settings.llm_api_key and not settings.openai_api_key and not settings.deepseek_api_key:
        raise RuntimeError("No LLM API key configured for judge evaluation")

    import httpx

    if settings.llm_api_key:
        api_key = settings.llm_api_key
        api_url = settings.llm_api_url or "https://api.openai.com/v1"
        model = settings.llm_model or "gpt-4o-mini"
    elif settings.openai_api_key:
        api_key = settings.openai_api_key
        api_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
    else:
        api_key = settings.deepseek_api_key
        api_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"

    client = httpx.Client(timeout=60.0)
    resp = client.post(
        f"{api_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "你是 Agent 系统评测专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown code fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 1)[-1]
        if raw.startswith("json"):
            raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    parsed = json.loads(raw)
    scores: list[EvalScore] = []
    for item in parsed:
        scores.append(EvalScore(
            name=item.get("name", ""),
            score=float(item.get("score", 0.0)),
            explanation=item.get("explanation", ""),
        ))
    return scores


async def run_judge_evaluations(trace_id: str) -> list[EvalScore]:
    """Run LLM-as-judge evaluation for a given trace.

    1. Pull trace span data from Phoenix
    2. Construct judge prompt
    3. Call LLM
    4. Write results back to Phoenix
    """
    settings = get_settings()
    if not settings.eval_judge_enabled:
        raise PermissionError("LLM-as-judge evaluation is disabled")

    # 1. Pull trace data from Phoenix
    spans = await _fetch_trace_spans(trace_id)
    if not spans:
        raise FileNotFoundError(f"Trace {trace_id} not found in Phoenix")

    # 2. Construct prompt
    summary = _extract_trace_summary(spans)
    prompt = _JUDGE_PROMPT_TEMPLATE.format(**summary)

    # 3. Call LLM (sync, offload to thread)
    scores = await asyncio.to_thread(_call_llm_judge, prompt)

    # 4. Write results back to Phoenix
    await _write_judge_evals_to_phoenix(trace_id, scores)

    return scores


async def _fetch_trace_spans(trace_id: str) -> list[Any]:
    """Fetch span data for a trace from Phoenix."""
    settings = get_settings()
    try:
        import phoenix as px
        client = px.Client(endpoint=settings.phoenix_ui_url)
        # Phoenix returns spans as a dataframe; convert to list of dicts
        df = client.get_spans_dataframe(project_name="default")
        if df is None or df.empty:
            return []
        trace_spans = df[df["context.trace_id"] == trace_id]
        if trace_spans.empty:
            return []
        return trace_spans.to_dict("records")
    except ImportError:
        logger.warning("phoenix SDK not installed, cannot fetch trace spans")
        return []
    except Exception as e:
        logger.warning("Failed to fetch trace spans from Phoenix: %s", e)
        return []


async def _write_judge_evals_to_phoenix(trace_id: str, scores: list[EvalScore]) -> None:
    """Write judge evaluation scores to Phoenix."""
    settings = get_settings()
    try:
        import phoenix as px
        from phoenix.trace import SpanEvaluations

        client = px.Client(endpoint=settings.phoenix_ui_url)
        for score in scores:
            client.log_evaluations(
                SpanEvaluations(
                    eval_name=score.name,
                    scores=[(trace_id, score.score, score.explanation)],
                ),
            )
    except ImportError:
        logger.debug("phoenix SDK not installed, skipping judge eval write")
    except Exception as e:
        logger.warning("Phoenix judge eval write failed: %s", e)
