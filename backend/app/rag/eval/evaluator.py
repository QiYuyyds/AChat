"""Evaluator — single-question evaluation and metric aggregation.

evaluate_question() runs RAG search → computes retrieval metrics →
optionally runs LLM judge → returns per-item result dict.
aggregate_metrics() rolls up per-item results into overall averages.
"""

import logging
from collections.abc import Callable

from app.infra.hybrid import RetrievalConfig
from app.rag.eval.metrics import f1_at_k, llm_judge, precision_at_k, recall_at_k

logger = logging.getLogger(__name__)

_JUDGE_K = 5


async def evaluate_question(
    query: str,
    gold_chunk_ids: list[int | str] | None,
    gold_answer: str | None,
    rag_service,
    retrieval_config: RetrievalConfig | None = None,
    judge_fn: Callable[[str, str], str] | None = None,
    *,
    user_id: str | None = None,
) -> dict:
    """Evaluate a single question: search → metrics → LLM judge.

    Returns a dict with keys:
        query, retrieved_chunks, generated_answer,
        recall, precision, f1, judge_score, metrics
    """
    answer, retrieved = await rag_service.search(
        query, user_id=user_id, retrieval_config=retrieval_config
    )

    retrieved_ids = [r.get("pg_id") for r in retrieved if r.get("pg_id") is not None]

    item_metrics: dict[str, float] = {}

    if gold_chunk_ids:
        item_metrics["recall"] = recall_at_k(retrieved_ids, gold_chunk_ids, _JUDGE_K)
        item_metrics["precision"] = precision_at_k(retrieved_ids, gold_chunk_ids, _JUDGE_K)
        item_metrics["f1"] = f1_at_k(retrieved_ids, gold_chunk_ids, _JUDGE_K)

    judge_score: float | None = None
    if gold_answer and judge_fn:
        judge_score = llm_judge(query, answer, gold_answer, judge_fn)
        item_metrics["judge_score"] = judge_score

    return {
        "query": query,
        "retrieved_chunks": retrieved,
        "generated_answer": answer,
        "gold_chunk_ids": gold_chunk_ids,
        "gold_answer": gold_answer,
        "metrics": item_metrics,
    }


def aggregate_metrics(item_results: list[dict]) -> dict:
    """Aggregate per-item metrics into overall averages.

    Returns a dict like:
        {"recall": avg, "precision": avg, "f1": avg, "judge_score": avg,
         "item_count": N, "has_retrieval_metrics": bool, "has_judge_metrics": bool}
    """
    if not item_results:
        return {"item_count": 0, "has_retrieval_metrics": False, "has_judge_metrics": False}

    recall_vals: list[float] = []
    precision_vals: list[float] = []
    f1_vals: list[float] = []
    judge_vals: list[float] = []

    for item in item_results:
        m = item.get("metrics") or {}
        if "recall" in m:
            recall_vals.append(m["recall"])
        if "precision" in m:
            precision_vals.append(m["precision"])
        if "f1" in m:
            f1_vals.append(m["f1"])
        if "judge_score" in m:
            judge_vals.append(m["judge_score"])

    def _avg(vals: list[float]) -> float | None:
        if not vals:
            return None
        return sum(vals) / len(vals)

    result: dict = {
        "item_count": len(item_results),
        "has_retrieval_metrics": bool(recall_vals),
        "has_judge_metrics": bool(judge_vals),
    }

    if recall_vals:
        result["recall"] = _avg(recall_vals)
        result["precision"] = _avg(precision_vals)
        result["f1"] = _avg(f1_vals)

    if judge_vals:
        result["judge_score"] = _avg(judge_vals)

    retrieval_vals = [v for v in f1_vals]
    if retrieval_vals and judge_vals:
        f1_avg = sum(retrieval_vals) / len(retrieval_vals)
        judge_avg = sum(judge_vals) / len(judge_vals)
        result["overall_score"] = (f1_avg + judge_avg) / 2.0
    elif retrieval_vals:
        result["overall_score"] = sum(retrieval_vals) / len(retrieval_vals)
    elif judge_vals:
        result["overall_score"] = sum(judge_vals) / len(judge_vals)

    return result
