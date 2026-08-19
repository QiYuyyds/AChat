"""RAG Evaluation API — dataset CRUD + eval runs + results.

Routes:
  POST   /rag/eval/datasets            — upload JSONL dataset
  POST   /rag/eval/datasets/generate   — auto-generate dataset from KB chunks
  GET    /rag/eval/datasets             — list datasets
  GET    /rag/eval/datasets/{id}        — dataset detail (paginated)
  DELETE /rag/eval/datasets/{id}        — delete dataset
  POST   /rag/eval/runs                 — start eval run
  GET    /rag/eval/runs                 — list runs
  GET    /rag/eval/runs/{id}/results     — run results (paginated)
  DELETE /rag/eval/runs/{id}            — delete run
"""

import json
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_service():
    """Lazy import to avoid circular dependency; returns the global RAGEvalService."""
    from app.main import _rag_eval_service  # type: ignore[attr-defined]
    if _rag_eval_service is None:
        raise HTTPException(status_code=503, detail="RAGEvalService not initialized")
    return _rag_eval_service


# ─── Request/Response models ────────────────────────────────────────────────


class GenerateDatasetRequest(BaseModel):
    chunk_count: int = Field(default=10, alias="chunkCount")
    name: str = ""
    description: str = ""


class StartRunRequest(BaseModel):
    dataset_id: str = Field(alias="datasetId")
    name: str = ""
    search_mode: str = "hybrid"
    final_top_k: int | None = Field(default=None, alias="finalTopK")
    vector_weight: float | None = Field(default=None, alias="vectorWeight")
    bm25_weight: float | None = Field(default=None, alias="bm25Weight")
    kg_weight: float | None = Field(default=None, alias="kgWeight")
    limit: int | None = None


# ─── Dataset endpoints ──────────────────────────────────────────────────────


@router.post("/rag/eval/datasets")
async def upload_dataset(
    file: UploadFile,
    name: str = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Upload a JSONL dataset file.

    Each line should be a JSON object with:
        query (str, required), gold_chunk_ids (list[int], optional), gold_answer (str, optional)
    """
    svc = _get_service()
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"error": "File encoding must be UTF-8"}, status_code=400)

    items: list[dict] = []
    for line_num, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            return JSONResponse(
                {"error": f"JSON parse error on line {line_num}: {e}"},
                status_code=400,
            )
        query = obj.get("query") or obj.get("query_text") or obj.get("question")
        if not query:
            return JSONResponse(
                {"error": f"Line {line_num}: missing 'query' field"},
                status_code=400,
            )
        gold_chunk_ids = obj.get("gold_chunk_ids") or obj.get("goldChunkIds")
        gold_answer = obj.get("gold_answer") or obj.get("goldAnswer")
        items.append({
            "query_text": str(query),
            "gold_chunk_ids": gold_chunk_ids,
            "gold_answer": gold_answer,
        })

    if not items:
        return JSONResponse({"error": "No valid items found in JSONL file"}, status_code=400)

    try:
        result = await svc.upload_dataset(
            items=items, name=name, description=description, user_id=user.id
        )
    except Exception as e:
        logger.exception("Dataset upload failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse(result, status_code=201)


@router.post("/rag/eval/datasets/generate")
async def generate_dataset(
    req: GenerateDatasetRequest,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Auto-generate a dataset from knowledge base chunks."""
    svc = _get_service()
    try:
        result = await svc.generate_dataset(
            chunk_count=req.chunk_count,
            name=req.name,
            description=req.description,
            user_id=user.id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("Dataset generation failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse(result, status_code=201)


@router.get("/rag/eval/datasets")
async def list_datasets(user: User = Depends(get_current_user)) -> JSONResponse:
    """List all datasets for the current user."""
    svc = _get_service()
    datasets = await svc.list_datasets(user_id=user.id)
    return JSONResponse({"datasets": datasets})


@router.get("/rag/eval/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get dataset detail with paginated items."""
    svc = _get_service()
    result = await svc.get_dataset(dataset_id, user.id, page, page_size)
    if result is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return JSONResponse(result)


@router.delete("/rag/eval/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Delete a dataset and all its items/runs."""
    svc = _get_service()
    deleted = await svc.delete_dataset(dataset_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return JSONResponse({"ok": True})


# ─── Eval run endpoints ─────────────────────────────────────────────────────


@router.post("/rag/eval/runs")
async def start_eval_run(
    req: StartRunRequest,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Start an evaluation run. Returns run_id immediately (async execution)."""
    svc = _get_service()
    from app.infra.hybrid import RetrievalConfig

    retrieval_config = RetrievalConfig(
        search_mode=req.search_mode,
        final_top_k=req.final_top_k,
        vector_weight=req.vector_weight,
        bm25_weight=req.bm25_weight,
        kg_weight=req.kg_weight,
    )

    try:
        run_id = await svc.run_evaluation(
            dataset_id=req.dataset_id,
            retrieval_config=retrieval_config,
            user_id=user.id,
            name=req.name,
            limit=req.limit,
        )
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        logger.exception("Eval run start failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"run_id": run_id, "status": "running"}, status_code=201)


@router.get("/rag/eval/runs")
async def list_runs(user: User = Depends(get_current_user)) -> JSONResponse:
    """List all eval runs for the current user."""
    svc = _get_service()
    runs = await svc.list_runs(user_id=user.id)
    return JSONResponse({"runs": runs})


@router.get("/rag/eval/runs/{run_id}/results")
async def get_run_results(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get eval run results with paginated items."""
    svc = _get_service()
    result = await svc.get_run_results(run_id, user.id, page, page_size)
    if result is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return JSONResponse(result)


@router.delete("/rag/eval/runs/{run_id}")
async def delete_run(
    run_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Delete an eval run and all its items."""
    svc = _get_service()
    deleted = await svc.delete_run(run_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return JSONResponse({"ok": True})
