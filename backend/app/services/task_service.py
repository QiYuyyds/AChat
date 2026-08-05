"""Task service — CRUD + OCC + comments + SSE event publishing.

All operations are user-scoped (``user_id``). Mutating operations enforce
optimistic concurrency control via the ``version`` column.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_local_db
from app.db.models import Task, TaskComment
from app.schemas.events import (
    SchedulerStatusEvent,
    TaskAssignedEvent,
    TaskCommentedEvent,
    TaskCreatedEvent,
    TaskMovedEvent,
    TaskUpdatedEvent,
)
from app.schemas.task import TaskCommentRow, TaskRow
from app.services.event_bus import event_bus
from app.utils.ids import new_task_comment_id, new_task_id

MAX_FAILURES = 5

# Priority weight for scheduler ordering
_PRIORITY_WEIGHT: dict[str, int] = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "none": 4,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _serialize_task(row: Task) -> TaskRow:
    return TaskRow(
        id=row.id,
        userId=row.user_id,
        title=row.title,
        description=row.description,
        status=row.status,
        priority=row.priority,
        labels=list(row.labels) if row.labels else [],
        assigneeAgentId=row.assignee_agent_id,
        creatorType=row.creator_type,
        creatorId=row.creator_id,
        creatorName=row.creator_name,
        conversationId=row.conversation_id,
        workspaceMode=row.workspace_mode,
        workspacePath=row.workspace_path,
        version=row.version,
        failureCount=row.failure_count,
        sortOrder=row.sort_order,
        dueDate=row.due_date,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        completedAt=row.completed_at,
    )


def _serialize_comment(row: TaskComment) -> TaskCommentRow:
    return TaskCommentRow(
        id=row.id,
        taskId=row.task_id,
        userId=row.user_id,
        body=row.body,
        authorType=row.author_type,
        authorId=row.author_id,
        authorName=row.author_name,
        version=row.version,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


class VersionConflictError(Exception):
    """Raised when OCC version check fails."""

    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(f"version_conflict: current version is {current_version}")


# ── List ───────────────────────────────────────────────────────


async def list_tasks(
    user_id: str,
    *,
    status: str | None = None,
    priority: str | None = None,
    assignee_agent_id: str | None = None,
) -> list[TaskRow]:
    async with get_local_db() as db:
        q = select(Task).where(Task.user_id == user_id)
        if status:
            q = q.where(Task.status == status)
        if priority:
            q = q.where(Task.priority == priority)
        if assignee_agent_id:
            q = q.where(Task.assignee_agent_id == assignee_agent_id)
        q = q.order_by(Task.sort_order.asc(), Task.created_at.desc())
        result = await db.execute(q)
        rows = result.scalars().all()
    return [_serialize_task(r) for r in rows]


async def get_task(user_id: str, task_id: str) -> TaskRow | None:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            return None
        return _serialize_task(row)


# ── Create ─────────────────────────────────────────────────────


async def create_task(
    user_id: str,
    *,
    title: str,
    description: str = "",
    status: str = "todo",
    priority: str = "none",
    labels: list[str] | None = None,
    assignee_agent_id: str | None = None,
    workspace_mode: str | None = None,
    workspace_path: str | None = None,
    due_date: str | None = None,
    creator_type: str = "user",
    creator_id: str = "",
    creator_name: str = "",
) -> TaskRow:
    now = _now_ms()
    row = Task(
        id=new_task_id(),
        user_id=user_id,
        title=title[:500],
        description=description,
        status=status,
        priority=priority,
        labels=labels or [],
        assignee_agent_id=assignee_agent_id,
        creator_type=creator_type,
        creator_id=creator_id,
        creator_name=creator_name,
        workspace_mode=workspace_mode,
        workspace_path=workspace_path,
        version=1,
        failure_count=0,
        sort_order=0,
        due_date=due_date,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    async with get_local_db() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskCreatedEvent(conversationId="", timestamp=now, task=task),
        user_id=user_id,
    )
    return task


# ── Update ─────────────────────────────────────────────────────


async def update_task(
    user_id: str,
    task_id: str,
    *,
    if_version: int,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    due_date: str | None = None,
) -> TaskRow:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        if title is not None:
            row.title = title[:500]
        if description is not None:
            row.description = description
        if priority is not None:
            row.priority = priority
        if labels is not None:
            row.labels = labels
        if due_date is not None:
            row.due_date = due_date

        # Editing a task resets failure_count
        row.failure_count = 0
        row.version += 1
        row.updated_at = _now_ms()
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskUpdatedEvent(conversationId="", timestamp=_now_ms(), task=task),
        user_id=user_id,
    )
    return task


# ── Move ───────────────────────────────────────────────────────


async def move_task(
    user_id: str,
    task_id: str,
    *,
    new_status: str,
    if_version: int,
    sort_order: int | None = None,
) -> TaskRow:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        from_status = row.status
        row.status = new_status
        if sort_order is not None:
            row.sort_order = sort_order
        row.version += 1
        row.updated_at = _now_ms()
        if new_status == "done" and row.completed_at is None:
            row.completed_at = _now_ms()
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskMovedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            fromStatus=from_status,
            toStatus=new_status,
            task=task,
        ),
        user_id=user_id,
    )
    return task


# ── Assign ─────────────────────────────────────────────────────


async def assign_task(
    user_id: str,
    task_id: str,
    *,
    agent_id: str | None,
    if_version: int,
) -> TaskRow:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        row.assignee_agent_id = agent_id
        row.version += 1
        row.updated_at = _now_ms()
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskAssignedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            agentId=agent_id,
            task=task,
        ),
        user_id=user_id,
    )
    return task


# ── Archive (delete) ───────────────────────────────────────────


async def archive_task(user_id: str, task_id: str) -> bool:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
    return True


# ── Complete (in_progress → in_review + auto comment) ──────────


async def complete_task(
    user_id: str,
    task_id: str,
    *,
    if_version: int,
    summary: str,
    author_type: str = "agent",
    author_id: str = "",
    author_name: str = "",
) -> tuple[TaskRow, TaskCommentRow]:
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        from_status = row.status
        row.status = "in_review"
        row.failure_count = 0  # Reset on successful completion
        row.version += 1
        row.updated_at = _now_ms()
        await db.flush()

        now = _now_ms()
        comment = TaskComment(
            id=new_task_comment_id(),
            task_id=task_id,
            user_id=user_id,
            body=summary,
            author_type=author_type,
            author_id=author_id,
            author_name=author_name,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(row)
        await db.refresh(comment)

    task = _serialize_task(row)
    comment_row = _serialize_comment(comment)

    event_bus.publish(
        TaskMovedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            fromStatus=from_status,
            toStatus="in_review",
            task=task,
        ),
        user_id=user_id,
    )
    event_bus.publish(
        TaskCommentedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            comment=comment_row,
        ),
        user_id=user_id,
    )
    return task, comment_row


# ── Comments ───────────────────────────────────────────────────


async def list_comments(user_id: str, task_id: str) -> list[TaskCommentRow]:
    async with get_local_db() as db:
        q = (
            select(TaskComment)
            .where(TaskComment.task_id == task_id)
            .where(TaskComment.user_id == user_id)
            .order_by(TaskComment.created_at.asc())
        )
        result = await db.execute(q)
        rows = result.scalars().all()
    return [_serialize_comment(r) for r in rows]


async def add_comment(
    user_id: str,
    task_id: str,
    *,
    body: str,
    author_type: str = "user",
    author_id: str = "",
    author_name: str = "",
) -> TaskCommentRow:
    now = _now_ms()
    comment = TaskComment(
        id=new_task_comment_id(),
        task_id=task_id,
        user_id=user_id,
        body=body,
        author_type=author_type,
        author_id=author_id,
        author_name=author_name,
        version=1,
        created_at=now,
        updated_at=now,
    )
    async with get_local_db() as db:
        db.add(comment)
        await db.commit()
        await db.refresh(comment)

    comment_row = _serialize_comment(comment)
    event_bus.publish(
        TaskCommentedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            comment=comment_row,
        ),
        user_id=user_id,
    )
    return comment_row


# ── Scheduler helpers ──────────────────────────────────────────


async def get_dispatchable_tasks(user_id: str) -> list[Task]:
    """Return todo tasks with failure_count < MAX_FAILURES, priority-sorted."""
    async with get_local_db() as db:
        q = (
            select(Task)
            .where(Task.user_id == user_id)
            .where(Task.status == "todo")
            .where(Task.failure_count < MAX_FAILURES)
        )
        result = await db.execute(q)
        rows = result.scalars().all()
    rows.sort(key=lambda t: (_PRIORITY_WEIGHT.get(t.priority, 4), t.created_at))
    return rows


async def bind_conversation(
    user_id: str,
    task_id: str,
    *,
    conversation_id: str,
    agent_id: str,
    if_version: int,
) -> Task:
    """Bind a conversation to a task and set in_progress (scheduler dispatch)."""
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        from_status = row.status
        row.conversation_id = conversation_id
        row.assignee_agent_id = agent_id
        row.status = "in_progress"
        row.version += 1
        row.updated_at = _now_ms()
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskMovedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            fromStatus=from_status,
            toStatus="in_progress",
            task=task,
        ),
        user_id=user_id,
    )
    event_bus.publish(
        TaskAssignedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            agentId=agent_id,
            task=task,
        ),
        user_id=user_id,
    )
    return row


async def rollback_dispatch(
    user_id: str,
    task_id: str,
    *,
    if_version: int,
) -> Task:
    """Revert a dispatched task back to todo on failure."""
    async with get_local_db() as db:
        row = await _load_task(db, user_id, task_id)
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        _check_version(row, if_version)

        from_status = row.status
        row.conversation_id = None
        row.status = "todo"
        row.failure_count += 1
        row.version += 1
        row.updated_at = _now_ms()
        await db.commit()
        await db.refresh(row)

    task = _serialize_task(row)
    event_bus.publish(
        TaskMovedEvent(
            conversationId="",
            timestamp=_now_ms(),
            taskId=task_id,
            fromStatus=from_status,
            toStatus="todo",
            task=task,
        ),
        user_id=user_id,
    )
    return row


async def count_todo_tasks(user_id: str) -> int:
    async with get_local_db() as db:
        from sqlalchemy import func

        q = (
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user_id)
            .where(Task.status == "todo")
        )
        result = await db.execute(q)
        return result.scalar() or 0


def publish_scheduler_status(
    user_id: str,
    *,
    running: bool,
    pending_count: int,
    active_count: int,
) -> None:
    event_bus.publish(
        SchedulerStatusEvent(
            conversationId="",
            timestamp=_now_ms(),
            running=running,
            pendingCount=pending_count,
            activeCount=active_count,
        ),
        user_id=user_id,
    )


# ── Internal helpers ───────────────────────────────────────────


async def _load_task(
    db: AsyncSession, user_id: str, task_id: str
) -> Task | None:
    result = await db.execute(
        select(Task).where(Task.id == task_id).where(Task.user_id == user_id)
    )
    return result.scalar_one_or_none()


def _check_version(row: Task, if_version: int) -> None:
    if row.version != if_version:
        raise VersionConflictError(row.version)
