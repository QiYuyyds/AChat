"""
Task management routes.

GET  /tasks                — List all tasks (across all suites)
GET  /tasks/{id}           — Get task details
GET  /tasks/{id}/history   — Aggregate trial results of a task across runs
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


# history 覆盖的最近 run 数上限 (与列表页 limit 一致)
HISTORY_RUN_LIMIT = 50


@router.get("/{task_id}/history")
async def get_task_history(task_id: str):
    """跨 run 聚合该 task 的 trial 结果 (倒序; 只读, 不改变持久化行为)"""
    from eval_harness.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    # 404 语义: task 不存在于任何 suite
    suites = await runner.storage.list_suites()
    suite_name = next(
        (
            s.name
            for s in suites
            if any(t.id == task_id for t in s.tasks)
        ),
        None,
    )
    if suite_name is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    history = []
    runs = await runner.storage.list_runs(limit=HISTORY_RUN_LIMIT)
    for run in runs:
        trials = run.trials.get(task_id)
        if not trials:
            continue
        grader_scores: dict[str, list[float]] = {}
        for trial in trials:
            for gr in trial.grader_results:
                grader_scores.setdefault(gr.grader_name, []).append(gr.score)
        history.append({
            "run_id": run.run_id,
            "suite_name": run.suite_name,
            "started_at": run.started_at,
            "trials_passed": sum(1 for t in trials if t.success),
            "trials_total": len(trials),
            "avg_score": (
                round(sum(t.avg_score() for t in trials) / len(trials), 4)
            ),
            "graders": {
                name: round(sum(scores) / len(scores), 4)
                for name, scores in grader_scores.items()
            },
        })

    history.sort(key=lambda h: h["started_at"], reverse=True)
    return {"task_id": task_id, "suite_name": suite_name, "history": history}
