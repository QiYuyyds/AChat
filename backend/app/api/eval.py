"""Evaluation API — manually trigger LLM-as-judge evaluation."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.observability.eval_judge import run_judge_evaluations

logger = logging.getLogger(__name__)

router = APIRouter()


class JudgeResponse(BaseModel):
    trace_id: str
    scores: list[dict]


@router.post("/eval/judge/{trace_id}", response_model=JudgeResponse)
async def trigger_judge_eval(trace_id: str) -> JudgeResponse:
    """Trigger LLM-as-judge evaluation for a given trace.

    - 403 if ``eval_judge_enabled=False``
    - 404 if trace not found in Phoenix
    - 200 with scores JSON on success
    """
    settings = get_settings()
    if not settings.eval_judge_enabled:
        raise HTTPException(status_code=403, detail="LLM-as-judge evaluation is disabled")

    try:
        scores = await run_judge_evaluations(trace_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Judge evaluation failed for trace %s: %s", trace_id, e)
        raise HTTPException(status_code=500, detail=f"Judge evaluation failed: {e}")

    return JudgeResponse(
        trace_id=trace_id,
        scores=[
            {"name": s.name, "score": s.score, "explanation": s.explanation}
            for s in scores
        ],
    )
