"""
Grader listing routes.

GET /graders — List available graders (name/type/description)
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def list_graders():
    """列出可用 grader (静态注册表, 不依赖 runner 注入)"""
    from agent_eval.graders import get_grader_catalog

    return {"graders": get_grader_catalog()}
