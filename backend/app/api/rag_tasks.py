"""RAG Tasks API routes — list / detail / retry.

All endpoints require JWT authentication and are user-scoped.
Independent from the global Task Board ``/api/tasks`` routes.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.db.engine import get_local_db
from app.db.models import RagTask, User
from app.schemas.rag_task import RagTaskResponse

router = APIRouter()


def _task_to_response(task: RagTask) -> RagTaskResponse:
    """Convert RagTask ORM row to RagTaskResponse."""
    return RagTaskResponse(
        id=task.id,
        user_id=task.user_id,
        task_type=task.task_type,
        document_id=task.document_id,
        version_id=task.version_id,
        status=task.status,
        payload=task.payload or {},
        result=task.result,
        error_message=task.error_message,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


@router.get("/rag-tasks")
async def list_rag_tasks(
    task_status: str | None = Query(default=None, alias="status"),
    document_id: str | None = Query(default=None, alias="documentId"),
    task_type: str | None = Query(default=None, alias="taskType"),
    user: User = Depends(get_current_user),
) -> dict:
    """List RAG tasks for the current user, optionally filtered."""
    async with get_local_db() as session:
        query = select(RagTask).where(RagTask.user_id == user.id)
        if task_status:
            query = query.where(RagTask.status == task_status)
        if document_id:
            query = query.where(RagTask.document_id == document_id)
        if task_type:
            query = query.where(RagTask.task_type == task_type)
        query = query.order_by(RagTask.created_at.desc()).limit(100)
        result = await session.execute(query)
        tasks = result.scalars().all()

    return {"tasks": [_task_to_response(t).model_dump(by_alias=True) for t in tasks]}


@router.get("/rag-tasks/{task_id}")
async def get_rag_task(
    task_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Get a single RAG task by ID."""
    async with get_local_db() as session:
        result = await session.execute(
            select(RagTask).where(RagTask.id == task_id, RagTask.user_id == user.id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RAG task not found: {task_id}",
            )

    return _task_to_response(task).model_dump(by_alias=True)


@router.post("/rag-tasks/{task_id}/retry")
async def retry_rag_task(
    task_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Retry a failed/failed_permanent RAG task.

    Resets status to ``pending`` and ``retry_count`` to 0.
    Only ``failed`` and ``failed_permanent`` tasks can be retried.
    """
    now = time.time()
    async with get_local_db() as session:
        result = await session.execute(
            select(RagTask).where(RagTask.id == task_id, RagTask.user_id == user.id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RAG task not found: {task_id}",
            )

        if task.status not in ("failed", "failed_permanent"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot retry task in status '{task.status}'",
            )

        task.status = "pending"
        task.retry_count = 0
        task.error_message = None
        task.started_at = None
        task.completed_at = None
        task.updated_at = now

    return _task_to_response(task).model_dump(by_alias=True)
