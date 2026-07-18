"""fs_write tool — write a workspace text file (auto / review approval).

Port of src/server/tools/fs-write.ts. Behaviour branches on the conversation's
``fs_write_approval_mode``:
  - 'auto'   : write directly
  - 'review' : register a pending write, emit ``fs_write.pending`` for the
               approval dialog, and wait for approve / reject (or run abort).

See specs/07-tools.md "fs_write 审批模式".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Conversation
from app.services.fs_service import (
    get_workspace_for_conversation,
    read_if_exists,
    write_file_in_workspace,
)
from app.services.pending_writes import pending_writes
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.approval import await_pending_decision
from app.utils.dispatch_file_writes import record_file_write
from app.utils.dispatch_run_evidence import RunFileEvidence, record_run_file_write
from app.utils.workspace_utils import assert_path_within_workspace


class _Args(BaseModel):
    path: str = Field(min_length=1)
    content: str


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["path", "content"],
    "properties": {
        "path": {"type": "string", "description": "目标文件路径（相对于 workspace 根目录或绝对路径，必须在 workspace 内）。"},
        "content": {"type": "string", "description": "UTF-8 文本内容（最大 100KB）。"},
    },
}

_DESCRIPTION = (
    "向 workspace 写入 UTF-8 文本文件（创建或完整覆盖）。"
    "父目录自动创建。单个文件上限 100KB，sandbox 模式下 workspace 总量上限 100MB / 1000 文件。"
    "review 模式下用户需审批 diff 后才会真正写入，用户拒绝时返回 ok:false。"
    "适合创建新文件或完整重写已有文件。小范围修改用 fs_edit 更精准。"
    "内容过大时先 fs_write 写入首部分，再用 fs_edit 追加后续内容，避免超出 LLM 输出 token 限制。"
)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    async with get_db() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == ctx.conversation_id)
        )
        conv = result.scalar_one_or_none()
    mode = conv.fs_write_approval_mode if conv else "review"

    byte_len = len(parsed.content.encode("utf-8"))

    # Auto mode: write immediately.
    if mode == "auto":
        old_content = read_if_exists(workspace, parsed.path)
        try:
            write_result = write_file_in_workspace(workspace, parsed.path, parsed.content)
        except (ValueError, OSError) as e:
            return err(str(e))
        record_file_write(ctx.run_id, write_result.absolute_path, parsed.content)
        record_run_file_write(
            ctx.run_id,
            RunFileEvidence(
                path=parsed.path,
                absolute_path=write_result.absolute_path,
                bytes=write_result.bytes,
                applied="auto",
            ),
        )
        return ok(
            {
                "path": write_result.path,
                "absolutePath": write_result.absolute_path,
                "cwd": write_result.cwd,
                "bytes": write_result.bytes,
                "applied": "auto",
                "oldContent": old_content,
                "newContent": parsed.content,
            }
        )

    # Review mode: register a pending write and wait for the user.
    try:
        abs_path = assert_path_within_workspace(workspace, parsed.path)
    except ValueError as e:
        return err(str(e))

    pending = pending_writes.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        path=parsed.path,
        absolute_path=abs_path,
        old_content=read_if_exists(workspace, parsed.path),
        new_content=parsed.content,
        workspace=workspace,
        user_id=ctx.user_id,
    )

    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_writes.attach_resolver(pending.id, r),
        cancel=lambda: pending_writes.cancel(pending.id),
        cancel_event=ctx.cancel_event,
        cancelled_value={"applied": False},
    )

    if not (isinstance(decision, dict) and decision.get("applied")):
        return err("User rejected the file change")

    record_file_write(ctx.run_id, abs_path, parsed.content)
    record_run_file_write(
        ctx.run_id,
        RunFileEvidence(
            path=parsed.path, absolute_path=abs_path, bytes=byte_len, applied="review"
        ),
    )
    return ok(
        {
            "path": parsed.path,
            "absolutePath": abs_path,
            "bytes": byte_len,
            "applied": "review",
            "oldContent": pending.old_content,
            "newContent": pending.new_content,
        }
    )


fs_write_tool = ToolDef(
    name="fs_write",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
