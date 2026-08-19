"""Tasks API routes — CRUD + scheduler control.

All endpoints require JWT authentication and are user-scoped.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.schemas.task import (
    AddTaskCommentRequest,
    AssignTaskRequest,
    CreateTaskRequest,
    MoveTaskRequest,
    SchedulerStartRequest,
    UpdateTaskRequest,
)
from app.services import task_service
from app.services.task_service import VersionConflictError

router = APIRouter()


def _conflict_response(e: VersionConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "version_conflict", "currentVersion": e.current_version},
    )


# ── Task CRUD ──────────────────────────────────────────────────


@router.get("/tasks")
async def list_tasks(
    task_status: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    assignee_agent_id: str | None = Query(default=None, alias="assigneeAgentId"),
    user: User = Depends(get_current_user),
) -> dict:
    tasks = await task_service.list_tasks(
        user.id,
        status=task_status,
        priority=priority,
        assignee_agent_id=assignee_agent_id,
    )
    return {"tasks": [t.model_dump(by_alias=True) for t in tasks]}


@router.post("/tasks")
async def create_task(
    body: CreateTaskRequest,
    user: User = Depends(get_current_user),
) -> dict:
    task = await task_service.create_task(
        user.id,
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        labels=body.labels,
        assignee_agent_id=body.assignee_agent_id,
        workspace_mode=body.workspace_mode,
        workspace_path=body.workspace_path,
        due_date=body.due_date,
        creator_type="user",
        creator_id=user.id,
        creator_name=user.name,
    )
    return task.model_dump(by_alias=True)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    task = await task_service.get_task(user.id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    comments = await task_service.list_comments(user.id, task_id)
    result = task.model_dump(by_alias=True)
    result["comments"] = [c.model_dump(by_alias=True) for c in comments]
    return result


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        task = await task_service.update_task(
            user.id,
            task_id,
            if_version=body.if_version,
            title=body.title,
            description=body.description,
            priority=body.priority,
            labels=body.labels,
            due_date=body.due_date,
        )
    except VersionConflictError as e:
        return _conflict_response(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return task.model_dump(by_alias=True)


@router.post("/tasks/{task_id}/move")
async def move_task(
    task_id: str,
    body: MoveTaskRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        task = await task_service.move_task(
            user.id,
            task_id,
            new_status=body.status,
            if_version=body.if_version,
            sort_order=body.sort_order,
        )
    except VersionConflictError as e:
        return _conflict_response(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return task.model_dump(by_alias=True)


@router.post("/tasks/{task_id}/assign")
async def assign_task(
    task_id: str,
    body: AssignTaskRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        task = await task_service.assign_task(
            user.id,
            task_id,
            agent_id=body.agent_id,
            if_version=body.if_version,
        )
    except VersionConflictError as e:
        return _conflict_response(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return task.model_dump(by_alias=True)


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    ok = await task_service.archive_task(user.id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


# ── Comments ───────────────────────────────────────────────────


@router.get("/tasks/{task_id}/comments")
async def list_comments(
    task_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    comments = await task_service.list_comments(user.id, task_id)
    return {"comments": [c.model_dump(by_alias=True) for c in comments]}


@router.post("/tasks/{task_id}/comments")
async def add_comment(
    task_id: str,
    body: AddTaskCommentRequest,
    user: User = Depends(get_current_user),
) -> dict:
    comment = await task_service.add_comment(
        user.id,
        task_id,
        body=body.body,
        author_type=body.author_type,
        author_id=body.author_id or user.id,
        author_name=body.author_name or user.name,
    )
    return comment.model_dump(by_alias=True)


# ── Scheduler control ──────────────────────────────────────────


def _get_scheduler():
    from app.main import _task_scheduler  # type: ignore[attr-defined]

    if _task_scheduler is None:
        raise RuntimeError("TaskSchedulerService not initialized")
    return _task_scheduler


@router.post("/tasks/scheduler/start")
async def start_scheduler(
    body: SchedulerStartRequest,
    user: User = Depends(get_current_user),
) -> dict:
    scheduler = _get_scheduler()
    await scheduler.start(
        user_id=user.id,
        agent_id=body.agent_id,
        interval_seconds=body.interval_minutes * 60,
        max_concurrent=body.max_concurrent,
    )
    return {"running": True}


@router.post("/tasks/scheduler/stop")
async def stop_scheduler(
    user: User = Depends(get_current_user),
) -> dict:
    scheduler = _get_scheduler()
    scheduler.stop(user.id)
    return {"running": False}


@router.get("/tasks/scheduler/status")
async def scheduler_status(
    user: User = Depends(get_current_user),
) -> dict:
    scheduler = _get_scheduler()
    return scheduler.get_status(user.id)
