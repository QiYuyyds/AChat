"""
Task management routes.

GET  /tasks         — List all tasks (across all suites)
GET  /tasks/{id}    — Get task details
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("")
async def list_tasks():
    """列出所有任务 (跨 suite)"""
    from eval_harness.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    suites = await runner.storage.list_suites()
    all_tasks = []
    for suite in suites:
        for task in suite.tasks:
            all_tasks.append({
                "id": task.id,
                "description": task.description,
                "suite_name": suite.name,
                "max_trials": task.max_trials,
                "grader_count": len(task.graders),
            })

    return {"tasks": all_tasks, "total": len(all_tasks)}


@router.get("/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    from eval_harness.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    # Search across all suites
    suites = await runner.storage.list_suites()
    for suite in suites:
        for task in suite.tasks:
            if task.id == task_id:
                return {
                    "task": task.model_dump(),
                    "suite_name": suite.name,
                }

    raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
