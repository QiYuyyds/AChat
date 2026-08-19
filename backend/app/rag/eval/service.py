"""RAGEvalService — service-based RAG evaluation with DB persistence.

Wires together metrics, evaluator, benchmark generation, and independent
eval LLM configuration. Provides dataset CRUD, evaluation run management,
and result retrieval — all with user_id isolation.
"""

import asyncio
import json
import logging
from collections.abc import Callable

from app.config import Settings
from app.db.engine import get_remote_db
from app.db.models import EvalDataset, EvalDatasetItem, EvalRun, EvalRunItem
from app.infra.hybrid import RetrievalConfig
from app.rag.eval.benchmark_generation import generate_dataset as _generate_benchmark
from app.rag.eval.evaluator import aggregate_metrics, evaluate_question
from app.utils.clock import now_ms
from app.utils.ids import _gen_id

logger = logging.getLogger(__name__)


def _make_eval_llm_fn(settings: Settings) -> Callable[[str, str], str] | None:
    """Create judge LLM function from independent eval_llm_* config."""
    api_key = settings.eval_llm_api_key
    if not api_key:
        return None
    api_url = settings.eval_llm_api_url or "https://api.openai.com/v1"
    model = settings.eval_llm_model or "gpt-4o-mini"
    return _make_llm_fn(api_key, api_url, model)


def _make_dataset_llm_fn(settings: Settings) -> Callable[[str, str], str] | None:
    """Create dataset-generation LLM function from eval_dataset_llm_* config."""
    api_key = settings.eval_dataset_llm_api_key
    if not api_key:
        return None
    api_url = settings.eval_dataset_llm_api_url or "https://api.openai.com/v1"
    model = settings.eval_dataset_llm_model or "gpt-4o-mini"
    return _make_llm_fn(api_key, api_url, model)


def _make_llm_fn(api_key: str, api_url: str, model: str) -> Callable[[str, str], str]:
    """Create a synchronous LLM generate function via OpenAI-compatible API."""
    import httpx

    client = httpx.Client(timeout=60.0)

    def generate(system_prompt: str, user_msg: str) -> str:
        resp = client.post(
            f"{api_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return generate


class RAGEvalService:
    """Service for RAG evaluation: dataset CRUD + eval runs + metrics.

    Uses independent eval LLM configuration (eval_llm_* / eval_dataset_llm_*)
    that is NOT shared with the RAG system's llm_api_key.
    """

    def __init__(self, settings: Settings, rag_service=None):
        self.settings = settings
        self._rag_service = rag_service
        self._judge_fn: Callable[[str, str], str] | None = _make_eval_llm_fn(settings)
        self._dataset_gen_fn: Callable[[str, str], str] | None = _make_dataset_llm_fn(settings)

    def set_rag_service(self, rag_service) -> None:
        self._rag_service = rag_service

    def set_judge_fn(self, fn: Callable[[str, str], str]) -> None:
        self._judge_fn = fn

    def set_dataset_gen_fn(self, fn: Callable[[str, str], str]) -> None:
        self._dataset_gen_fn = fn

    def eval_llm_available(self) -> bool:
        return self._judge_fn is not None

    def dataset_llm_available(self) -> bool:
        return self._dataset_gen_fn is not None

    # ─── Dataset CRUD ──────────────────────────────────────────────────

    async def upload_dataset(
        self,
        items: list[dict],
        name: str,
        description: str,
        user_id: str,
    ) -> dict:
        """Create a dataset from uploaded items.

        Each item should have: query_text (str), gold_chunk_ids (list, optional),
        gold_answer (str, optional).
        """
        ts = float(now_ms())
        dataset_id = _gen_id("evds_")
        has_gold_chunks = any(it.get("gold_chunk_ids") for it in items)
        has_gold_answers = any(it.get("gold_answer") for it in items)

        async with get_remote_db() as session:
            dataset = EvalDataset(
                id=dataset_id,
                user_id=user_id,
                name=name,
                description=description,
                item_count=len(items),
                has_gold_chunks=has_gold_chunks,
                has_gold_answers=has_gold_answers,
                build_metadata={"source": "upload"},
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

        logger.info("Uploaded dataset %s with %d items", dataset_id, len(items))
        return {
            "id": dataset_id,
            "name": name,
            "description": description,
            "item_count": len(items),
            "has_gold_chunks": has_gold_chunks,
            "has_gold_answers": has_gold_answers,
            "created_by": "user",
            "created_at": ts,
            "updated_at": ts,
        }

    async def generate_dataset(
        self,
        chunk_count: int,
        name: str,
        description: str,
        user_id: str,
    ) -> dict:
        """Auto-generate a dataset from knowledge base chunks."""
        return await _generate_benchmark(
            chunk_count=chunk_count,
            gen_fn=self._dataset_gen_fn,
            user_id=user_id,
            name=name,
            description=description,
        )

    async def list_datasets(self, user_id: str) -> list[dict]:
        """List all datasets for a user."""
        from sqlalchemy import select

        async with get_remote_db() as session:
            result = await session.execute(
                select(EvalDataset)
                .where(EvalDataset.user_id == user_id)
                .order_by(EvalDataset.created_at.desc())
            )
            datasets = result.scalars().all()

        return [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "item_count": d.item_count,
                "has_gold_chunks": d.has_gold_chunks,
                "has_gold_answers": d.has_gold_answers,
                "created_by": d.created_by,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in datasets
        ]

    async def get_dataset(
        self, dataset_id: str, user_id: str, page: int = 1, page_size: int = 50
    ) -> dict | None:
        """Get dataset detail with paginated items."""
        from sqlalchemy import func, select

        async with get_remote_db() as session:
            ds_result = await session.execute(
                select(EvalDataset).where(
                    EvalDataset.id == dataset_id,
                    EvalDataset.user_id == user_id,
                )
            )
            dataset = ds_result.scalar_one_or_none()
            if dataset is None:
                return None

            count_result = await session.execute(
                select(func.count()).select_from(EvalDatasetItem).where(
                    EvalDatasetItem.dataset_id == dataset_id
                )
            )
            total = count_result.scalar() or 0

            offset = (page - 1) * page_size
            items_result = await session.execute(
                select(EvalDatasetItem)
                .where(EvalDatasetItem.dataset_id == dataset_id)
                .order_by(EvalDatasetItem.item_index)
                .offset(offset)
                .limit(page_size)
            )
            items = items_result.scalars().all()

        return {
            "id": dataset.id,
            "name": dataset.name,
            "description": dataset.description,
            "item_count": dataset.item_count,
            "has_gold_chunks": dataset.has_gold_chunks,
            "has_gold_answers": dataset.has_gold_answers,
            "build_metadata": dataset.build_metadata,
            "created_by": dataset.created_by,
            "created_at": dataset.created_at,
            "updated_at": dataset.updated_at,
            "items": [
                {
                    "id": it.id,
                    "item_index": it.item_index,
                    "query_text": it.query_text,
                    "gold_chunk_ids": it.gold_chunk_ids,
                    "gold_answer": it.gold_answer,
                }
                for it in items
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }

    async def delete_dataset(self, dataset_id: str, user_id: str) -> bool:
        """Delete a dataset and all its items/runs."""
        from sqlalchemy import delete, select

        async with get_remote_db() as session:
            result = await session.execute(
                select(EvalDataset).where(
                    EvalDataset.id == dataset_id,
                    EvalDataset.user_id == user_id,
                )
            )
            dataset = result.scalar_one_or_none()
            if dataset is None:
                return False

            await session.execute(
                delete(EvalDataset).where(EvalDataset.id == dataset_id)
            )

        logger.info("Deleted dataset %s", dataset_id)
        return True

    # ─── Evaluation Runs ───────────────────────────────────────────────

    async def run_evaluation(
        self,
        dataset_id: str,
        retrieval_config: RetrievalConfig | None,
        user_id: str,
        name: str = "",
        limit: int | None = None,
    ) -> str:
        """Start an async evaluation run. Returns run_id immediately."""
        if not self.eval_llm_available():
            raise RuntimeError(
                "Evaluation LLM not configured (EVAL_LLM_API_KEY required)"
            )
        if self._rag_service is None:
            raise RuntimeError("RAGService not initialized")

        from sqlalchemy import select

        ts = float(now_ms())
        run_id = _gen_id("evrun_")

        rc_dict = None
        if retrieval_config is not None:
            rc_dict = {
                "search_mode": retrieval_config.search_mode,
                "final_top_k": retrieval_config.final_top_k,
                "similarity_threshold": retrieval_config.similarity_threshold,
                "bm25_top_k": retrieval_config.bm25_top_k,
                "vector_weight": retrieval_config.vector_weight,
                "bm25_weight": retrieval_config.bm25_weight,
                "kg_weight": retrieval_config.kg_weight,
            }

        async with get_remote_db() as session:
            ds_result = await session.execute(
                select(EvalDataset).where(
                    EvalDataset.id == dataset_id,
                    EvalDataset.user_id == user_id,
                )
            )
            dataset = ds_result.scalar_one_or_none()
            if dataset is None:
                raise ValueError(f"Dataset {dataset_id} not found")

            items_result = await session.execute(
                select(EvalDatasetItem)
                .where(EvalDatasetItem.dataset_id == dataset_id)
                .order_by(EvalDatasetItem.item_index)
            )
            all_items = items_result.scalars().all()

            items_to_eval = all_items[:limit] if limit else all_items

            run = EvalRun(
                id=run_id,
                user_id=user_id,
                name=name or f"Eval of {dataset.name}",
                dataset_id=dataset_id,
                status="running",
                retrieval_config=rc_dict,
                total_items=len(items_to_eval),
                completed_items=0,
                started_at=ts,
                created_by="user",
                created_at=ts,
            )
            session.add(run)

        asyncio.ensure_future(
            self._execute_run(run_id, items_to_eval, retrieval_config, user_id)
        )

        logger.info("Started eval run %s with %d items", run_id, len(items_to_eval))
        return run_id

    async def _execute_run(
        self,
        run_id: str,
        items: list[EvalDatasetItem],
        retrieval_config: RetrievalConfig | None,
        user_id: str,
    ) -> None:
        """Background task: evaluate each item and persist results."""
        item_results: list[dict] = []
        completed = 0

        for item in items:
            try:
                result = await evaluate_question(
                    query=item.query_text,
                    gold_chunk_ids=item.gold_chunk_ids,
                    gold_answer=item.gold_answer,
                    rag_service=self._rag_service,
                    retrieval_config=retrieval_config,
                    judge_fn=self._judge_fn,
                    user_id=user_id,
                )
                item_results.append(result)
            except Exception as e:
                logger.warning("Eval item %d failed: %s", item.item_index, e)
                item_results.append({
                    "query": item.query_text,
                    "gold_chunk_ids": item.gold_chunk_ids,
                    "gold_answer": item.gold_answer,
                    "retrieved_chunks": [],
                    "generated_answer": "",
                    "metrics": {},
                    "error": str(e),
                })

            completed += 1
            async with get_remote_db() as session:
                from sqlalchemy import update

                await session.execute(
                    update(EvalRun)
                    .where(EvalRun.id == run_id)
                    .values(completed_items=completed)
                )

        metrics = aggregate_metrics(item_results)
        overall_score = metrics.get("overall_score")
        end_ts = float(now_ms())

        async with get_remote_db() as session:
            from sqlalchemy import update

            await session.execute(
                update(EvalRun)
                .where(EvalRun.id == run_id)
                .values(
                    status="completed",
                    metrics=metrics,
                    overall_score=overall_score,
                    completed_items=completed,
                    completed_at=end_ts,
                )
            )

            for idx, result in enumerate(item_results):
                run_item = EvalRunItem(
                    id=_gen_id("evri_"),
                    run_id=run_id,
                    item_index=idx,
                    dataset_item_id=items[idx].id if idx < len(items) else None,
                    query_text=result.get("query", ""),
                    gold_chunk_ids=result.get("gold_chunk_ids"),
                    gold_answer=result.get("gold_answer"),
                    generated_answer=result.get("generated_answer"),
                    retrieved_chunks=result.get("retrieved_chunks"),
                    metrics=result.get("metrics"),
                )
                session.add(run_item)

        logger.info("Eval run %s completed: %s", run_id, json.dumps(metrics, default=str))

    async def list_runs(self, user_id: str) -> list[dict]:
        """List all eval runs for a user."""
        from sqlalchemy import select

        async with get_remote_db() as session:
            result = await session.execute(
                select(EvalRun)
                .where(EvalRun.user_id == user_id)
                .order_by(EvalRun.created_at.desc())
            )
            runs = result.scalars().all()

        return [
            {
                "id": r.id,
                "name": r.name,
                "dataset_id": r.dataset_id,
                "status": r.status,
                "total_items": r.total_items,
                "completed_items": r.completed_items,
                "overall_score": r.overall_score,
                "metrics": r.metrics,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "created_at": r.created_at,
            }
            for r in runs
        ]

    async def get_run_results(
        self, run_id: str, user_id: str, page: int = 1, page_size: int = 50
    ) -> dict | None:
        """Get eval run results with paginated items."""
        from sqlalchemy import func, select

        async with get_remote_db() as session:
            run_result = await session.execute(
                select(EvalRun).where(
                    EvalRun.id == run_id,
                    EvalRun.user_id == user_id,
                )
            )
            run = run_result.scalar_one_or_none()
            if run is None:
                return None

            count_result = await session.execute(
                select(func.count()).select_from(EvalRunItem).where(
                    EvalRunItem.run_id == run_id
                )
            )
            total = count_result.scalar() or 0

            offset = (page - 1) * page_size
            items_result = await session.execute(
                select(EvalRunItem)
                .where(EvalRunItem.run_id == run_id)
                .order_by(EvalRunItem.item_index)
                .offset(offset)
                .limit(page_size)
            )
            items = items_result.scalars().all()

        return {
            "id": run.id,
            "name": run.name,
            "dataset_id": run.dataset_id,
            "status": run.status,
            "retrieval_config": run.retrieval_config,
            "metrics": run.metrics,
            "overall_score": run.overall_score,
            "total_items": run.total_items,
            "completed_items": run.completed_items,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "created_at": run.created_at,
            "items": [
                {
                    "id": it.id,
                    "item_index": it.item_index,
                    "query_text": it.query_text,
                    "gold_chunk_ids": it.gold_chunk_ids,
                    "gold_answer": it.gold_answer,
                    "generated_answer": it.generated_answer,
                    "retrieved_chunks": it.retrieved_chunks,
                    "metrics": it.metrics,
                }
                for it in items
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }

    async def delete_run(self, run_id: str, user_id: str) -> bool:
        """Delete an eval run and all its items."""
        from sqlalchemy import delete, select

        async with get_remote_db() as session:
            result = await session.execute(
                select(EvalRun).where(
                    EvalRun.id == run_id,
                    EvalRun.user_id == user_id,
                )
            )
            run = result.scalar_one_or_none()
            if run is None:
                return False

            await session.execute(
                delete(EvalRun).where(EvalRun.id == run_id)
            )

        logger.info("Deleted eval run %s", run_id)
        return True
