"""Plan usage statistics API routes.

Exposes GET /api/plan-usage/stats for aggregated plan usage metrics.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.services.plan_usage_service import get_plan_usage_stats

router = APIRouter()


@router.get("/plan-usage/stats")
async def plan_usage_stats(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Aggregated plan usage statistics.

    Regular users see their own stats. Currently all users are treated equally
    (no admin role exists); a future admin role can be added for global stats.
    """
    stats = await get_plan_usage_stats(user_id=user.id)
    return JSONResponse(stats)
