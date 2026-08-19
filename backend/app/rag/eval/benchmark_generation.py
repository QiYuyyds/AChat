"""Benchmark dataset generation — sample chunks → LLM generates questions.

generate_dataset() samples chunks from rag_chunks, uses a dataset-generation
LLM to create a question for each chunk, and persists the results as an
EvalDataset + EvalDatasetItems.
"""

import logging
import random
from collections.abc import Callable

from app.db.engine import get_remote_db
from app.db.models import EvalDataset, EvalDatasetItem
from app.utils.clock import now_ms
from app.utils.ids import _gen_id

logger = logging.getLogger(__name__)

_GEN_QUESTION_SYSTEM_PROMPT = (
    "You are a question generator. Given the following text passage, generate "
    "a clear, specific question that can be answered from the passage. "
    "Return ONLY the question text, no preamble or explanation."
)


async def generate_dataset(
    chunk_count: int,
    gen_fn: Callable[[str, str], str] | None,
    user_id: str,
    name: str = "",
    description: str = "",
) -> dict:
    """Sample chunks from rag_chunks → LLM generates questions → persist dataset.

    Args:
        chunk_count: target number of items to generate.
        gen_fn: dataset generation LLM function (system_prompt, user_msg) -> str.
        user_id: owner for isolation.
        name: dataset name (auto-generated if empty).
        description: optional description.

    Returns: dict representation of the created EvalDataset.
    """
    from sqlalchemy import select

    from app.db.models import RagChunk

    async with get_remote_db() as session:
        stmt = select(RagChunk.id, RagChunk.content).where(RagChunk.content.isnot(None))
        if user_id:
            stmt = stmt.where(RagChunk.user_id == user_id)
        result = await session.execute(stmt)
        all_chunks = result.all()

    if not all_chunks:
        raise ValueError("No chunks found in knowledge base. Ingest documents first.")

    sample_size = min(chunk_count, len(all_chunks))
    sampled = random.sample(all_chunks, sample_size) if sample_size < len(all_chunks) else all_chunks

    items: list[dict] = []
    for chunk_row in sampled:
        chunk_id, chunk_content = chunk_row[0], chunk_row[1] or ""
        if not chunk_content.strip():
            continue
        if gen_fn is None:
            question = f"What is described in chunk {chunk_id}?"
        else:
            try:
                question = gen_fn(_GEN_QUESTION_SYSTEM_PROMPT, chunk_content[:2000]).strip()
            except Exception as e:
                logger.warning("Question generation failed for chunk %s: %s", chunk_id, e)
                question = f"What is described in chunk {chunk_id}?"
        if not question:
            question = f"What is described in chunk {chunk_id}?"
        items.append({
            "query_text": question,
            "gold_chunk_ids": [chunk_id],
            "gold_answer": None,
        })

    if not items:
        raise ValueError("Failed to generate any questions from sampled chunks.")

    ts = float(now_ms())
    dataset_id = _gen_id("evds_")
    has_gold_chunks = any(it.get("gold_chunk_ids") for it in items)
    has_gold_answers = any(it.get("gold_answer") for it in items)

    async with get_remote_db() as session:
        dataset = EvalDataset(
            id=dataset_id,
            user_id=user_id,
            name=name or f"Auto-generated {sample_size} items",
            description=description,
            item_count=len(items),
            has_gold_chunks=has_gold_chunks,
            has_gold_answers=has_gold_answers,
            build_metadata={"source": "auto_generated", "chunk_count": sample_size},
            created_by="user",
            created_at=ts,
            updated_at=ts,
        )
        session.add(dataset)

        for idx, item in enumerate(items):
            di = EvalDatasetItem(
                id=_gen_id("evdi_"),
                dataset_id=dataset_id,
                item_index=idx,
                query_text=item["query_text"],
                gold_chunk_ids=item.get("gold_chunk_ids"),
                gold_answer=item.get("gold_answer"),
            )
            session.add(di)

    logger.info("Generated dataset %s with %d items", dataset_id, len(items))
    return {
        "id": dataset_id,
        "name": dataset.name,
        "description": dataset.description,
        "item_count": len(items),
        "has_gold_chunks": has_gold_chunks,
        "has_gold_answers": has_gold_answers,
        "created_by": "user",
        "created_at": ts,
        "updated_at": ts,
    }
