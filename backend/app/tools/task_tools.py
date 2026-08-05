"""Task management tools — 7 opt-in tools for Custom Agents (SDK route).

These tools are NOT part of the baseline 9; they must be explicitly added to
``agent.tool_names`` via the Agent Builder UI. All operations are scoped by
``ToolContext.user_id``.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

# ── task_list ──────────────────────────────────────────────────

_task_list_params: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "description": "Filter by status: backlog/todo/in_progress/in_review/done/blocked/canceled",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 20)",
            "default": 20,
        },
    },
}


async def _task_list_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    if ctx.user_id is None:
        return err("No user context")
    status_filter = args.get("status") if isinstance(args, dict) else None
    limit = args.get("limit", 20) if isinstance(args, dict) else 20
    tasks = await task_service.list_tasks(ctx.user_id, status=status_filter)
    truncated = tasks[:limit]
    return ok({"tasks": [t.model_dump(by_alias=True) for t in truncated]})


task_list_tool = ToolDef(
    name="task_list",
    description="List tasks in the global task pool. Optional filter by status.",
    parameters=_task_list_params,
    handler=_task_list_handler,
)


# ── task_get ───────────────────────────────────────────────────

_task_get_params: dict[str, Any] = {
    "type": "object",
    "required": ["taskId"],
    "properties": {
        "taskId": {"type": "string", "description": "Task ID"},
    },
}


async def _task_get_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    if ctx.user_id is None:
        return err("No user context")
    task_id = args["taskId"]
    task = await task_service.get_task(ctx.user_id, task_id)
    if task is None:
        return err(f"Task not found: {task_id}")
    comments = await task_service.list_comments(ctx.user_id, task_id)
    result = task.model_dump(by_alias=True)
    result["comments"] = [c.model_dump(by_alias=True) for c in comments]
    return ok(result)


task_get_tool = ToolDef(
    name="task_get",
    description="Get a task's detail including comments.",
    parameters=_task_get_params,
    handler=_task_get_handler,
)


# ── task_create ────────────────────────────────────────────────

_task_create_params: dict[str, Any] = {
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string", "description": "Short title (max 500 chars)"},
        "description": {"type": "string", "description": "Detailed description"},
        "priority": {
            "type": "string",
            "enum": ["none", "urgent", "high", "medium", "low"],
            "default": "none",
        },
        "labels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Free-form tags",
        },
        "workspaceMode": {
            "type": "string",
            "enum": ["sandbox", "local"],
            "description": "Workspace binding mode. 'local' requires workspacePath.",
        },
        "workspacePath": {
            "type": "string",
            "description": "Absolute path to local project (only for workspaceMode='local')",
        },
    },
}


async def _task_create_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    if ctx.user_id is None:
        return err("No user context")
    task = await task_service.create_task(
        ctx.user_id,
        title=args["title"],
        description=args.get("description", ""),
        priority=args.get("priority", "none"),
        labels=args.get("labels", []),
        workspace_mode=args.get("workspaceMode"),
        workspace_path=args.get("workspacePath"),
        creator_type="agent",
        creator_id=ctx.agent_id,
        creator_name="Agent",
    )
    return ok(task.model_dump(by_alias=True))


task_create_tool = ToolDef(
    name="task_create",
    description="Create a new task in the global task pool.",
    parameters=_task_create_params,
    handler=_task_create_handler,
)


# ── task_claim ─────────────────────────────────────────────────

_task_claim_params: dict[str, Any] = {
    "type": "object",
    "required": ["taskId", "ifVersion"],
    "properties": {
        "taskId": {"type": "string"},
        "ifVersion": {"type": "integer", "description": "Current version for OCC"},
    },
}


async def _task_claim_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    if ctx.user_id is None:
        return err("No user context")
    task_id = args["taskId"]
    if_version = args["ifVersion"]
    try:
        task = await task_service.move_task(
            ctx.user_id,
            task_id,
            new_status="in_progress",
            if_version=if_version,
        )
        await task_service.assign_task(
            ctx.user_id,
            task_id,
            agent_id=ctx.agent_id,
            if_version=task.version,
        )
    except VersionConflictError:
        return err("版本冲突：任务已被其他 Agent 认领或状态已变更")
    except ValueError as e:
        return err(str(e))

    # Re-fetch to get the latest version after assign
    task = await task_service.get_task(ctx.user_id, task_id)
    if task is None:
        return err("Task disappeared after claim")
    return ok(task.model_dump(by_alias=True))


task_claim_tool = ToolDef(
    name="task_claim",
    description="Claim a 'todo' task → 'in_progress'. Enforces optimistic concurrency (ifVersion).",
    parameters=_task_claim_params,
    handler=_task_claim_handler,
)


# ── task_complete ──────────────────────────────────────────────

_task_complete_params: dict[str, Any] = {
    "type": "object",
    "required": ["taskId", "ifVersion", "summary"],
    "properties": {
        "taskId": {"type": "string"},
        "ifVersion": {"type": "integer"},
        "summary": {"type": "string", "description": "Completion summary (becomes a comment)"},
    },
}


async def _task_complete_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    if ctx.user_id is None:
        return err("No user context")
    task_id = args["taskId"]
    if_version = args["ifVersion"]
    summary = args["summary"]
    try:
        task, comment = await task_service.complete_task(
            ctx.user_id,
            task_id,
            if_version=if_version,
            summary=summary,
            author_type="agent",
            author_id=ctx.agent_id,
            author_name="Agent",
        )
    except VersionConflictError:
        return err("版本冲突：任务状态已变更，请重新获取任务信息")
    except ValueError as e:
        return err(str(e))

    return ok({
        "task": task.model_dump(by_alias=True),
        "comment": comment.model_dump(by_alias=True),
    })


task_complete_tool = ToolDef(
    name="task_complete",
    description="Mark an 'in_progress' task as 'in_review' with a summary comment. Resets failure count.",
    parameters=_task_complete_params,
    handler=_task_complete_handler,
)


# ── task_move ──────────────────────────────────────────────────

_task_move_params: dict[str, Any] = {
    "type": "object",
    "required": ["taskId", "status", "ifVersion"],
    "properties": {
        "taskId": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["backlog", "todo", "in_progress", "in_review", "done", "blocked", "canceled"],
        },
        "ifVersion": {"type": "integer"},
        "reason": {"type": "string", "description": "Reason for the move (especially for 'blocked')"},
    },
}


async def _task_move_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service
    from app.services.task_service import VersionConflictError

    if ctx.user_id is None:
        return err("No user context")
    task_id = args["taskId"]
    new_status = args["status"]
    if_version = args["ifVersion"]
    reason = args.get("reason")

    try:
        task = await task_service.move_task(
            ctx.user_id,
            task_id,
            new_status=new_status,
            if_version=if_version,
        )
    except VersionConflictError:
        return err("版本冲突：任务状态已变更")
    except ValueError as e:
        return err(str(e))

    if reason:
        await task_service.add_comment(
            ctx.user_id,
            task_id,
            body=f"[状态变更为 {new_status}] {reason}",
            author_type="agent",
            author_id=ctx.agent_id,
            author_name="Agent",
        )

    return ok(task.model_dump(by_alias=True))


task_move_tool = ToolDef(
    name="task_move",
    description="Move a task to a different status. Use 'blocked' with a reason when stuck.",
    parameters=_task_move_params,
    handler=_task_move_handler,
)


# ── task_comment ───────────────────────────────────────────────

_task_comment_params: dict[str, Any] = {
    "type": "object",
    "required": ["taskId", "body"],
    "properties": {
        "taskId": {"type": "string"},
        "body": {"type": "string", "description": "Comment text"},
    },
}


async def _task_comment_handler(args: Any, ctx: ToolContext) -> ToolResult:
    from app.services import task_service

    if ctx.user_id is None:
        return err("No user context")
    comment = await task_service.add_comment(
        ctx.user_id,
        args["taskId"],
        body=args["body"],
        author_type="agent",
        author_id=ctx.agent_id,
        author_name="Agent",
    )
    return ok(comment.model_dump(by_alias=True))


task_comment_tool = ToolDef(
    name="task_comment",
    description="Add a comment to a task to record progress or notes.",
    parameters=_task_comment_params,
    handler=_task_comment_handler,
)
