"""manage_tasks tool — Guide Agent management tool for the task board.

Actions: list / create / update / move / assign / delete /
         scheduler_start / scheduler_stop / scheduler_status

All mutating actions emit ``guide_side_effect`` SSE events so the frontend
auto-refreshes the Kanban board.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect


async def _manage_tasks_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.user_id is None:
        return err("No user context")
    action = args.get("action", "")

    if action == "list":
        return await _list_tasks(args, ctx)
    elif action == "create":
        return await _create_task(args, ctx)
    elif action == "update":
        return await _update_task(args, ctx)
    elif action == "move":
        return await _move_task(args, ctx)
    elif action == "assign":
        return await _assign_task(args, ctx)
    elif action == "delete":
        return await _delete_task(args, ctx)
    elif action == "scheduler_start":
        return await _scheduler_start(args, ctx)
    elif action == "scheduler_stop":
        return await _scheduler_stop(ctx)
    elif action == "scheduler_status":
        return await _scheduler_status(ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_tasks(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    tasks = await task_service.list_tasks(
        ctx.user_id,
        status=args.get("status"),
        priority=args.get("priority"),
    )
    return ok({"tasks": [t.model_dump(by_alias=True) for t in tasks]})


async def _create_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    task = await task_service.create_task(
        ctx.user_id,
        title=args["title"],
        description=args.get("description", ""),
        priority=args.get("priority", "none"),
        labels=args.get("labels", []),
        creator_type="user",
        creator_id=ctx.user_id,
    )
    emit_guide_side_effect(ctx=ctx, target="tasks", action="create")
    return ok(task.model_dump(by_alias=True))


async def _update_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    try:
        task = await task_service.update_task(
            ctx.user_id,
            args["taskId"],
            if_version=args["ifVersion"],
            title=args.get("title"),
            description=args.get("description"),
            priority=args.get("priority"),
            labels=args.get("labels"),
        )
    except VersionConflictError as e:
        return err(f"版本冲突：当前版本为 {e.current_version}")
    except ValueError as e:
        return err(str(e))

    emit_guide_side_effect(ctx=ctx, target="tasks", action="update")
    return ok(task.model_dump(by_alias=True))


async def _move_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    try:
        task = await task_service.move_task(
            ctx.user_id,
            args["taskId"],
            new_status=args["status"],
            if_version=args["ifVersion"],
        )
    except VersionConflictError as e:
        return err(f"版本冲突：当前版本为 {e.current_version}")
    except ValueError as e:
        return err(str(e))

    emit_guide_side_effect(ctx=ctx, target="tasks", action="update")
    return ok(task.model_dump(by_alias=True))


async def _assign_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    try:
        task = await task_service.assign_task(
            ctx.user_id,
            args["taskId"],
            agent_id=args.get("agentId"),
            if_version=args["ifVersion"],
        )
    except VersionConflictError as e:
        return err(f"版本冲突：当前版本为 {e.current_version}")
    except ValueError as e:
        return err(str(e))

    emit_guide_side_effect(ctx=ctx, target="tasks", action="update")
    return ok(task.model_dump(by_alias=True))


async def _delete_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    ok_result = await task_service.archive_task(ctx.user_id, args["taskId"])
    if not ok_result:
        return err("Task not found")
    emit_guide_side_effect(ctx=ctx, target="tasks", action="delete")
    return ok({"deleted": True})


async def _scheduler_start(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.main import _task_scheduler  # type: ignore[attr-defined]

    if _task_scheduler is None:
        return err("TaskSchedulerService not initialized")
    await _task_scheduler.start(
        user_id=ctx.user_id,
        agent_id=args.get("agentId"),
        interval_seconds=args.get("intervalMinutes", 5) * 60,
        max_concurrent=args.get("maxConcurrent", 3),
    )
    emit_guide_side_effect(ctx=ctx, target="tasks", action="update")
    return ok({"running": True})


async def _scheduler_stop(ctx: ToolContext) -> ToolResult:
    from app.main import _task_scheduler  # type: ignore[attr-defined]

    if _task_scheduler is None:
        return err("TaskSchedulerService not initialized")
    _task_scheduler.stop(ctx.user_id)
    emit_guide_side_effect(ctx=ctx, target="tasks", action="update")
    return ok({"running": False})


async def _scheduler_status(ctx: ToolContext) -> ToolResult:
    from app.main import _task_scheduler  # type: ignore[attr-defined]

    if _task_scheduler is None:
        return ok({"running": False, "pendingCount": 0, "activeCount": 0})
    return ok(_task_scheduler.get_status(ctx.user_id))


_manage_tasks_params: dict[str, Any] = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "list", "create", "update", "move", "assign",
                "delete", "scheduler_start", "scheduler_stop", "scheduler_status",
            ],
        },
        "taskId": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "priority": {
            "type": "string",
            "enum": ["none", "urgent", "high", "medium", "low"],
        },
        "labels": {"type": "array", "items": {"type": "string"}},
        "status": {
            "type": "string",
            "enum": ["backlog", "todo", "in_progress", "in_review", "done", "blocked", "canceled"],
        },
        "agentId": {"type": "string"},
        "ifVersion": {"type": "integer"},
        "intervalMinutes": {"type": "integer", "default": 5},
        "maxConcurrent": {"type": "integer", "default": 3},
    },
}


manage_tasks_tool = ToolDef(
    name="manage_tasks",
    description="Manage the global task board: list/create/update/move/assign/delete tasks and control the scheduler.",
    parameters=_manage_tasks_params,
    handler=_manage_tasks_handler,
)
