"""
Suite management routes.

GET    /suites         — List all suites
POST   /suites         — Create a suite (JSON body)
GET    /suites/{name}  — Get suite details
DELETE /suites/{name}  — Delete a suite
"""

from __future__ import annotations

from agent_eval.core.types import EvalSuite
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("")
async def list_suites():
    """列出所有评测套件"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    suites = await runner.storage.list_suites()
    return {
        "suites": [
            {
                "name": s.name,
                "description": s.description,
                "task_count": len(s.tasks),
                "metadata": s.metadata,
            }
            for s in suites
        ]
    }


@router.post("")
async def create_suite(suite: EvalSuite):
    """创建评测套件"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    await runner.storage.save_suite(suite)
    return {"name": suite.name, "task_count": len(suite.tasks)}


@router.get("/{name}")
async def get_suite(name: str):
    """获取评测套件详情"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    suite = await runner.storage.get_suite(name)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"Suite '{name}' not found")

    return suite.model_dump()


@router.delete("/{name}")
async def delete_suite(name: str):
    """删除评测套件"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    deleted = await runner.storage.delete_suite(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Suite '{name}' not found")

    return {"deleted": True}
