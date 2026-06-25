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
        "path": {"type": "string", "description": "Destination path inside the workspace."},
        "content": {"type": "string", "description": "UTF-8 text content (max 100 KB)."},
    },
}

_DESCRIPTION = (
    "Write a UTF-8 text file inside the workspace. Path can be relative (resolved "
    "against the workspace root) or absolute (must still be inside the workspace). "
    "Parent directories are created automatically. Each file is capped at 100 KB; in "
    "sandbox mode the workspace as a whole is capped at 100 MB / 1000 files. In "
    "'review' mode the user must approve the diff before the write actually happens; "
    "you'll see ok:false with 'rejected' if they decline. Use this to scaffold code, "
    "write documents, etc."
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
        }
    )


fs_write_tool = ToolDef(
    name="fs_write",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
