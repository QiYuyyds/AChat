"""Integration tests for the service-based RAG evaluation system.

Covers the manual test scenarios from rag-eval-system tasks 8.3–8.6:
- 8.3: Upload a JSONL dataset → dataset created correctly
- 8.4: Start an eval run (limit=5) → run completes, metrics computed
- 8.5: No eval LLM configured → clear error message
- 8.6: Auto-generate dataset → questions generated from chunks

Uses SQLite (via the db fixture) + mock LLM functions, so no PG/LLM needed.
"""

import json
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.rag.eval.evaluator import aggregate_metrics, evaluate_question
from app.rag.eval.metrics import f1_at_k, llm_judge, precision_at_k, recall_at_k
from app.rag.eval.service import RAGEvalService

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_settings(**overrides) -> Settings:
    """Create Settings with eval LLM config overrides."""
    base = {
        "eval_llm_api_key": "test-key",
        "eval_llm_api_url": "https://test.example.com/v1",
        "eval_llm_model": "test-model",
        "eval_dataset_llm_api_key": "test-key",
        "eval_dataset_llm_api_url": "https://test.example.com/v1",
        "eval_dataset_llm_model": "test-model",
    }
    base.update(overrides)
    s = MagicMock(spec=Settings)
    for k, v in base.items():
        setattr(s, k, v)
    return s


def _make_mock_judge_fn(score: float = 0.8):
    """Create a mock judge function that returns a fixed score."""

    def judge(system_prompt: str, user_msg: str) -> str:
        return str(score)

    return judge


def _make_mock_gen_fn():
    """Create a mock dataset-generation function."""

    def gen(system_prompt: str, user_msg: str) -> str:
        return f"What is described in the following text? {user_msg[:50]}"

    return gen


def _make_mock_rag_service(retrieved_ids: list[int] | None = None, answer: str = "mock answer"):
    """Create a mock RAGService with a search method."""
    mock = MagicMock()
    chunks = []
    for rid in (retrieved_ids or [1, 2, 3]):
        chunks.append({"pg_id": rid, "content": f"chunk {rid}", "score": 0.9})

    async def search(query, user_id=None, retrieval_config=None):
        return answer, chunks

    mock.search = search
    return mock


# ─── Unit tests for metrics ───────────────────────────────────────────────────


class TestMetrics:
    def test_recall_at_k_hit(self):
        assert recall_at_k([1, 2, 3], [2, 4], k=3) == pytest.approx(0.5)

    def test_recall_at_k_all_hit(self):
        assert recall_at_k([1, 2, 3], [1, 2], k=3) == pytest.approx(1.0)

    def test_recall_at_k_no_hit(self):
        assert recall_at_k([1, 2, 3], [5, 6], k=3) == pytest.approx(0.0)

    def test_recall_at_k_empty_gold(self):
        assert recall_at_k([1, 2, 3], [], k=3) == pytest.approx(0.0)

    def test_precision_at_k(self):
        assert precision_at_k([1, 2, 3], [2, 4], k=3) == pytest.approx(1 / 3)

    def test_precision_at_k_all_gold(self):
        assert precision_at_k([1, 2], [1, 2], k=3) == pytest.approx(1.0)

    def test_precision_at_k_empty_retrieved(self):
        assert precision_at_k([], [1, 2], k=3) == pytest.approx(0.0)

    def test_f1_at_k(self):
        r = recall_at_k([1, 2, 3], [2, 4], k=3)
        p = precision_at_k([1, 2, 3], [2, 4], k=3)
        expected = 2 * r * p / (r + p) if (r + p) > 0 else 0.0
        assert f1_at_k([1, 2, 3], [2, 4], k=3) == pytest.approx(expected)

    def test_f1_at_k_zero(self):
        assert f1_at_k([1, 2], [5, 6], k=2) == pytest.approx(0.0)

    def test_llm_judge_with_fn(self):
        fn = _make_mock_judge_fn(0.75)
        score = llm_judge("question?", "gen answer", "gold answer", fn)
        assert score == pytest.approx(0.75)

    def test_llm_judge_no_fn(self):
        score = llm_judge("question?", "gen answer", "gold answer", None)
        assert score == pytest.approx(0.0)

    def test_llm_judge_empty_generated(self):
        fn = _make_mock_judge_fn(0.75)
        score = llm_judge("question?", "", "gold answer", fn)
        assert score == pytest.approx(0.0)

    def test_llm_judge_empty_gold(self):
        fn = _make_mock_judge_fn(0.75)
        score = llm_judge("question?", "gen answer", "", fn)
        assert score == pytest.approx(0.0)

    def test_llm_judge_parses_float_from_text(self):
        def judge(sp, um):
            return "The score is 0.85 out of 1.0"
        assert llm_judge("q", "a", "g", judge) == pytest.approx(0.85)


# ─── Unit tests for evaluator ─────────────────────────────────────────────────


class TestEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_question_with_gold_chunks(self):
        rag = _make_mock_rag_service(retrieved_ids=[1, 2, 3])
        result = await evaluate_question(
            query="test query",
            gold_chunk_ids=[1, 5],
            gold_answer=None,
            rag_service=rag,
            judge_fn=None,
        )
        assert result["query"] == "test query"
        assert "recall" in result["metrics"]
        assert "precision" in result["metrics"]
        assert "f1" in result["metrics"]
        assert "judge_score" not in result["metrics"]

    @pytest.mark.asyncio
    async def test_evaluate_question_with_gold_answer(self):
        rag = _make_mock_rag_service()
        judge_fn = _make_mock_judge_fn(0.9)
        result = await evaluate_question(
            query="test query",
            gold_chunk_ids=None,
            gold_answer="gold answer",
            rag_service=rag,
            judge_fn=judge_fn,
        )
        assert "judge_score" in result["metrics"]
        assert result["metrics"]["judge_score"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_evaluate_question_no_gold(self):
        rag = _make_mock_rag_service()
        result = await evaluate_question(
            query="test query",
            gold_chunk_ids=None,
            gold_answer=None,
            rag_service=rag,
            judge_fn=None,
        )
        assert result["metrics"] == {}

    def test_aggregate_metrics_empty(self):
        result = aggregate_metrics([])
        assert result["item_count"] == 0
        assert result["has_retrieval_metrics"] is False
        assert result["has_judge_metrics"] is False

    def test_aggregate_metrics_with_data(self):
        items = [
            {"metrics": {"recall": 0.5, "precision": 0.5, "f1": 0.5, "judge_score": 0.8}},
            {"metrics": {"recall": 1.0, "precision": 0.5, "f1": 0.667, "judge_score": 0.6}},
        ]
        result = aggregate_metrics(items)
        assert result["item_count"] == 2
        assert result["has_retrieval_metrics"] is True
        assert result["has_judge_metrics"] is True
        assert result["recall"] == pytest.approx(0.75)
        assert result["judge_score"] == pytest.approx(0.7)
        assert "overall_score" in result


# ─── Task 8.3: Upload JSONL dataset ─────────────────────────────────────────


class TestUploadDataset:
    @pytest.mark.asyncio
    async def test_upload_dataset_creates_records(self, db, test_user):
        """8.3: Upload a JSONL dataset → dataset created correctly."""
        settings = _make_settings()
        svc = RAGEvalService(settings)
        items = [
            {"query_text": "What is RAG?", "gold_chunk_ids": [1, 2], "gold_answer": "Retrieval Augmented Generation"},
            {"query_text": "What is Milvus?", "gold_chunk_ids": [3], "gold_answer": None},
            {"query_text": "What is Neo4j?", "gold_chunk_ids": None, "gold_answer": "Graph database"},
        ]
        result = await svc.upload_dataset(
            items=items, name="Test Dataset", description="test desc",
            user_id=test_user["id"],
        )
        assert result["item_count"] == 3
        assert result["name"] == "Test Dataset"
        assert result["has_gold_chunks"] is True
        assert result["has_gold_answers"] is True

        datasets = await svc.list_datasets(user_id=test_user["id"])
        assert len(datasets) == 1
        assert datasets[0]["name"] == "Test Dataset"

        detail = await svc.get_dataset(result["id"], user_id=test_user["id"])
        assert detail is not None
        assert detail["item_count"] == 3
        assert len(detail["items"]) == 3
        assert detail["items"][0]["query_text"] == "What is RAG?"


# ─── Task 8.5: No eval LLM → clear error ────────────────────────────────────


class TestNoEvalLLM:
    @pytest.mark.asyncio
    async def test_run_without_eval_llm_raises_error(self, db, test_user):
        """8.5: No eval LLM configured → clear error message."""
        settings = _make_settings(eval_llm_api_key=None, eval_dataset_llm_api_key=None)
        svc = RAGEvalService(settings)
        assert svc.eval_llm_available() is False

        # First upload a dataset so we have something to run against
        items = [{"query_text": "test", "gold_chunk_ids": [1], "gold_answer": "answer"}]
        ds = await svc.upload_dataset(
            items=items, name="No LLM Test", description="", user_id=test_user["id"],
        )

        with pytest.raises(RuntimeError, match="EVAL_LLM_API_KEY"):
            await svc.run_evaluation(
                dataset_id=ds["id"], retrieval_config=None, user_id=test_user["id"],
            )


# ─── Task 8.4: Start eval run → completes, metrics computed ─────────────────


class TestRunEvaluation:
    @pytest.mark.asyncio
    async def test_run_evaluation_completes(self, db, test_user):
        """8.4: Start an eval run (limit=5) → run completes, metrics computed."""
        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_judge_fn(_make_mock_judge_fn(0.85))

        # Create a mock RAG service
        mock_rag = _make_mock_rag_service(retrieved_ids=[1, 2, 3], answer="generated answer")
        svc.set_rag_service(mock_rag)

        # Upload a dataset with 5 items
        items = [
            {"query_text": f"Question {i}?", "gold_chunk_ids": [i], "gold_answer": f"Answer {i}"}
            for i in range(1, 6)
        ]
        ds = await svc.upload_dataset(
            items=items, name="Eval Run Test", description="", user_id=test_user["id"],
        )

        # Start eval run with limit=5
        run_id = await svc.run_evaluation(
            dataset_id=ds["id"], retrieval_config=None, user_id=test_user["id"], limit=5,
        )
        assert run_id is not None

        # Wait for async run to complete (poll status)
        import asyncio
        for _ in range(30):
            await asyncio.sleep(0.1)
            runs = await svc.list_runs(user_id=test_user["id"])
            if runs and runs[0]["status"] == "completed":
                break

        runs = await svc.list_runs(user_id=test_user["id"])
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed"
        assert run["total_items"] == 5
        assert run["completed_items"] == 5
        assert run["metrics"] is not None
        assert run["metrics"]["item_count"] == 5
        assert run["metrics"]["has_retrieval_metrics"] is True
        assert run["metrics"]["has_judge_metrics"] is True
        assert run["overall_score"] is not None

        # Check per-item results
        results = await svc.get_run_results(run_id, user_id=test_user["id"])
        assert results is not None
        assert len(results["items"]) == 5
        for item in results["items"]:
            assert item["generated_answer"] == "generated answer"
            assert "metrics" in item
            assert "judge_score" in item["metrics"]


# ─── Task 8.6: Auto-generate dataset from chunks ────────────────────────────


class TestGenerateDataset:
    @pytest.mark.asyncio
    async def test_generate_dataset_from_chunks(self, db, test_user):
        """8.6: Auto-generate dataset → questions generated from chunks."""
        from app.db.engine import get_remote_db
        from app.db.models import RagChunk
        from app.utils.clock import now_ms

        # Seed some chunks
        ts = now_ms()
        async with get_remote_db() as session:
            for i in range(5):
                chunk = RagChunk(
                    doc_hash=f"hash_{i}",
                    chunk_idx=i,
                    content=f"This is chunk content number {i} about topic {i}.",
                    created_at=ts,
                    user_id=test_user["id"],
                    chunk_token_count=10,
                )
                session.add(chunk)

        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_dataset_gen_fn(_make_mock_gen_fn())

        result = await svc.generate_dataset(
            chunk_count=3, name="Auto Gen", description="auto-generated",
            user_id=test_user["id"],
        )
        assert result["item_count"] == 3
        assert result["has_gold_chunks"] is True
        assert result["has_gold_answers"] is False

        detail = await svc.get_dataset(result["id"], user_id=test_user["id"])
        assert detail is not None
        assert len(detail["items"]) == 3
        for item in detail["items"]:
            assert item["query_text"]
            assert item["gold_chunk_ids"] is not None
            assert len(item["gold_chunk_ids"]) == 1

    @pytest.mark.asyncio
    async def test_generate_dataset_no_chunks_raises(self, db, test_user):
        """Auto-generation with no chunks should raise a clear error."""
        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_dataset_gen_fn(_make_mock_gen_fn())

        with pytest.raises(ValueError, match="No chunks found"):
            await svc.generate_dataset(
                chunk_count=5, name="Empty", description="", user_id=test_user["id"],
            )

    @pytest.mark.asyncio
    async def test_generate_dataset_without_llm_uses_fallback(self, db, test_user):
        """Without dataset gen LLM, falls back to template questions."""
        from app.db.engine import get_remote_db
        from app.db.models import RagChunk
        from app.utils.clock import now_ms

        ts = now_ms()
        async with get_remote_db() as session:
            chunk = RagChunk(
                doc_hash="hash_single",
                chunk_idx=0,
                content="Single chunk content for fallback test.",
                created_at=ts,
                user_id=test_user["id"],
                chunk_token_count=10,
            )
            session.add(chunk)

        settings = _make_settings(eval_dataset_llm_api_key=None)
        svc = RAGEvalService(settings)
        assert svc.dataset_llm_available() is False

        result = await svc.generate_dataset(
            chunk_count=1, name="Fallback", description="", user_id=test_user["id"],
        )
        assert result["item_count"] == 1
        detail = await svc.get_dataset(result["id"], user_id=test_user["id"])
        assert detail["items"][0]["query_text"]  # has a fallback question


# ─── Dataset CRUD tests ──────────────────────────────────────────────────────


class TestDatasetCRUD:
    @pytest.mark.asyncio
    async def test_delete_dataset(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        items = [{"query_text": "q1", "gold_chunk_ids": None, "gold_answer": None}]
        ds = await svc.upload_dataset(
            items=items, name="Delete Me", description="", user_id=test_user["id"],
        )
        deleted = await svc.delete_dataset(ds["id"], user_id=test_user["id"])
        assert deleted is True
        result = await svc.get_dataset(ds["id"], user_id=test_user["id"])
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_dataset(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        deleted = await svc.delete_dataset("nonexistent", user_id=test_user["id"])
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_datasets_user_isolation(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        items = [{"query_text": "q1", "gold_chunk_ids": None, "gold_answer": None}]
        await svc.upload_dataset(
            items=items, name="User1 Dataset", description="", user_id=test_user["id"],
        )
        # Other user's dataset should not be visible
        other_datasets = await svc.list_datasets(user_id="other_user")
        assert len(other_datasets) == 0

    @pytest.mark.asyncio
    async def test_get_dataset_pagination(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        items = [
            {"query_text": f"q{i}", "gold_chunk_ids": None, "gold_answer": None}
            for i in range(10)
        ]
        ds = await svc.upload_dataset(
            items=items, name="Paged", description="", user_id=test_user["id"],
        )
        page1 = await svc.get_dataset(ds["id"], user_id=test_user["id"], page=1, page_size=5)
        assert len(page1["items"]) == 5
        assert page1["pagination"]["total"] == 10
        page2 = await svc.get_dataset(ds["id"], user_id=test_user["id"], page=2, page_size=5)
        assert len(page2["items"]) == 5
        assert page2["items"][0]["item_index"] == 5


# ─── Run CRUD tests ──────────────────────────────────────────────────────────


class TestRunCRUD:
    @pytest.mark.asyncio
    async def test_delete_run(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_judge_fn(_make_mock_judge_fn(0.8))
        svc.set_rag_service(_make_mock_rag_service())

        items = [{"query_text": "q1", "gold_chunk_ids": [1], "gold_answer": "a1"}]
        ds = await svc.upload_dataset(
            items=items, name="Delete Run Test", description="", user_id=test_user["id"],
        )
        run_id = await svc.run_evaluation(
            dataset_id=ds["id"], retrieval_config=None, user_id=test_user["id"],
        )
        import asyncio
        for _ in range(20):
            await asyncio.sleep(0.1)
            runs = await svc.list_runs(user_id=test_user["id"])
            if runs and runs[0]["status"] == "completed":
                break

        deleted = await svc.delete_run(run_id, user_id=test_user["id"])
        assert deleted is True
        result = await svc.get_run_results(run_id, user_id=test_user["id"])
        assert result is None

    @pytest.mark.asyncio
    async def test_run_nonexistent_dataset(self, db, test_user):
        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_judge_fn(_make_mock_judge_fn(0.8))
        svc.set_rag_service(_make_mock_rag_service())

        with pytest.raises(ValueError, match="not found"):
            await svc.run_evaluation(
                dataset_id="nonexistent", retrieval_config=None, user_id=test_user["id"],
            )


# ─── API endpoint tests ─────────────────────────────────────────────────────


class TestRAGEvalAPI:
    @pytest.mark.asyncio
    async def test_api_upload_dataset(self, api_client, test_user):
        """8.3 via API: Upload JSONL → 201 with dataset info."""
        jsonl_lines = [
            json.dumps({"query": "What is RAG?", "gold_chunk_ids": [1, 2], "gold_answer": "RAG is..."}),
            json.dumps({"query": "What is Milvus?", "gold_chunk_ids": [3]}),
        ]
        jsonl_content = "\n".join(jsonl_lines)

        # Patch the service into main
        import app.main
        settings = _make_settings()
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc  # type: ignore[attr-defined]

        resp = await api_client.post(
            "/api/rag/eval/datasets",
            data={"name": "API Test Dataset", "description": "via API"},
            files={"file": ("test.jsonl", jsonl_content.encode("utf-8"), "application/json")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["item_count"] == 2
        assert body["name"] == "API Test Dataset"
        assert body["has_gold_chunks"] is True
        assert body["has_gold_answers"] is True

    @pytest.mark.asyncio
    async def test_api_list_datasets(self, api_client, test_user):
        import app.main
        settings = _make_settings()
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc

        items = [{"query_text": "q1", "gold_chunk_ids": None, "gold_answer": None}]
        await svc.upload_dataset(
            items=items, name="List Test", description="", user_id=test_user["id"],
        )

        resp = await api_client.get("/api/rag/eval/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["datasets"]) == 1
        assert body["datasets"][0]["name"] == "List Test"

    @pytest.mark.asyncio
    async def test_api_get_dataset(self, api_client, test_user):
        import app.main
        settings = _make_settings()
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc

        items = [{"query_text": "detail q", "gold_chunk_ids": [1], "gold_answer": "a"}]
        ds = await svc.upload_dataset(
            items=items, name="Detail Test", description="", user_id=test_user["id"],
        )

        resp = await api_client.get(f"/api/rag/eval/datasets/{ds['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Detail Test"
        assert len(body["items"]) == 1

    @pytest.mark.asyncio
    async def test_api_delete_dataset(self, api_client, test_user):
        import app.main
        settings = _make_settings()
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc

        items = [{"query_text": "del q", "gold_chunk_ids": None, "gold_answer": None}]
        ds = await svc.upload_dataset(
            items=items, name="Del Test", description="", user_id=test_user["id"],
        )

        resp = await api_client.delete(f"/api/rag/eval/datasets/{ds['id']}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_api_start_run_no_llm(self, api_client, test_user):
        """8.5 via API: No eval LLM → 400 with clear error."""
        import app.main
        settings = _make_settings(eval_llm_api_key=None, eval_dataset_llm_api_key=None)
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc

        # Upload a dataset first
        items = [{"query_text": "q1", "gold_chunk_ids": [1], "gold_answer": "a1"}]
        ds = await svc.upload_dataset(
            items=items, name="No LLM API", description="", user_id=test_user["id"],
        )

        resp = await api_client.post(
            "/api/rag/eval/runs",
            json={"datasetId": ds["id"], "searchMode": "hybrid", "limit": 5},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "EVAL_LLM_API_KEY" in body["error"]

    @pytest.mark.asyncio
    async def test_api_generate_dataset(self, api_client, test_user):
        """8.6 via API: Auto-generate dataset from chunks."""
        import app.main
        from app.db.engine import get_remote_db
        from app.db.models import RagChunk
        from app.utils.clock import now_ms

        settings = _make_settings()
        svc = RAGEvalService(settings)
        svc.set_dataset_gen_fn(_make_mock_gen_fn())
        app.main._rag_eval_service = svc

        # Seed chunks
        ts = now_ms()
        async with get_remote_db() as session:
            for i in range(3):
                chunk = RagChunk(
                    doc_hash=f"api_hash_{i}",
                    chunk_idx=i,
                    content=f"API test chunk {i} content.",
                    created_at=ts,
                    user_id=test_user["id"],
                    chunk_token_count=10,
                )
                session.add(chunk)

        resp = await api_client.post(
            "/api/rag/eval/datasets/generate",
            json={"chunkCount": 3, "name": "API Auto Gen", "description": "via API"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["item_count"] == 3
        assert body["has_gold_chunks"] is True

    @pytest.mark.asyncio
    async def test_api_list_runs(self, api_client, test_user):
        import app.main
        settings = _make_settings()
        svc = RAGEvalService(settings)
        app.main._rag_eval_service = svc

        resp = await api_client.get("/api/rag/eval/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert "runs" in body
