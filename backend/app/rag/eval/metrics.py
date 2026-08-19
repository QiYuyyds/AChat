"""Retrieval and generation metrics for RAG evaluation.

Retrieval metrics (computed when gold_chunk_ids are available):
- recall_at_k: fraction of gold chunks found in top-K retrieved
- precision_at_k: fraction of top-K retrieved that are gold
- f1_at_k: harmonic mean of recall and precision

Generation metric (computed when gold_answer is available):
- llm_judge: LLM-as-judge score (0.0–1.0) via a provided judge function
"""

from collections.abc import Callable


def recall_at_k(retrieved_ids: list[int | str], gold_ids: list[int | str], k: int) -> float:
    """Fraction of gold IDs that appear in the top-K retrieved IDs."""
    if not gold_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    gold_set = set(gold_ids)
    if not gold_set:
        return 0.0
    hits = sum(1 for rid in top_k if rid in gold_set)
    return hits / len(gold_set)


def precision_at_k(retrieved_ids: list[int | str], gold_ids: list[int | str], k: int) -> float:
    """Fraction of top-K retrieved IDs that are in the gold set."""
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    gold_set = set(gold_ids) if gold_ids else set()
    if not gold_set:
        return 0.0
    hits = sum(1 for rid in top_k if rid in gold_set)
    return hits / len(top_k)


def f1_at_k(retrieved_ids: list[int | str], gold_ids: list[int | str], k: int) -> float:
    """Harmonic mean of recall@k and precision@k."""
    r = recall_at_k(retrieved_ids, gold_ids, k)
    p = precision_at_k(retrieved_ids, gold_ids, k)
    if r + p == 0.0:
        return 0.0
    return 2.0 * r * p / (r + p)


def llm_judge(
    question: str,
    generated_answer: str,
    gold_answer: str,
    judge_fn: Callable[[str, str], str] | None,
) -> float:
    """LLM-as-judge: ask the judge LLM to score the generated answer.

    judge_fn receives (system_prompt, user_msg) and returns the LLM text.
    The LLM is expected to return a single float in [0, 1].
    Returns 0.0 if judge_fn is None or parsing fails.
    """
    if judge_fn is None:
        return 0.0
    if not generated_answer or not gold_answer:
        return 0.0

    system_prompt = (
        "You are an answer quality judge. Compare the generated answer to the gold answer "
        "for the given question. Return ONLY a single number from 0.0 to 1.0, "
        "where 1.0 means perfect match and 0.0 means completely wrong. "
        "Do not include any other text."
    )
    user_msg = (
        f"Question: {question}\n\n"
        f"Gold answer:\n{gold_answer}\n\n"
        f"Generated answer:\n{generated_answer}\n\n"
        f"Score (0.0-1.0):"
    )

    try:
        raw = judge_fn(system_prompt, user_msg)
        text = raw.strip()
        for token in text.split():
            try:
                return max(0.0, min(1.0, float(token)))
            except ValueError:
                continue
        return 0.0
    except Exception:
        return 0.0
