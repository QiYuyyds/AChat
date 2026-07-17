"""fs_edit tool — precise in-place string replacement in a workspace file.

Unlike ``fs_write`` (full rewrite), ``fs_edit`` replaces exactly one occurrence
of ``old_string`` with ``new_string``. The tool verifies that ``old_string``
appears exactly once in the file (zero or multiple matches are rejected), then
reuses the same ``pending_writes`` approval flow as ``fs_write`` — so in review
mode the user sees a diff scoped to the actually changed lines.

See specs/07-tools.md "fs_edit".
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Conversation
from app.services.fs_service import (
    MAX_READ_BYTES,
    get_workspace_for_conversation,
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
    old_string: str
    new_string: str


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["path", "old_string", "new_string"],
    "properties": {
        "path": {
            "type": "string",
            "description": "File path inside the workspace (relative or absolute).",
        },
        "old_string": {
            "type": "string",
            "description": (
                "The exact text to replace. Must appear exactly once in the file; "
                "zero or multiple matches will be rejected. Include enough "
                "surrounding context to make the match unique."
            ),
        },
        "new_string": {
            "type": "string",
            "description": "The replacement text.",
        },
    },
}


_DESCRIPTION = (
    "Edit a workspace file by replacing a single, unique occurrence of "
    "old_string with new_string. The tool verifies old_string appears exactly "
    "once (zero or multiple matches are rejected) to prevent ambiguous edits. "
    "In review mode the user sees a diff highlighting only the changed lines "
    "(unlike fs_write which shows a full-file diff). Use this for precise, "
    "targeted edits; use fs_write for full rewrites or new files."
)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    # Sandbox check first (gives a clear "outside workspace" error).
    try:
        abs_path = assert_path_within_workspace(workspace, parsed.path)
    except ValueError as e:
        return err(str(e))

    # Check file existence.
    if not os.path.isfile(abs_path):
        return err(f"File not found: {parsed.path}")

    # Large file protection.
    file_size = os.path.getsize(abs_path)
    if file_size > MAX_READ_BYTES:
        return err(
            f"File too large for edit ({file_size / 1024 / 1024:.2f} MB > "
            f"1 MB limit); use fs_write for full rewrite"
        )

    # Read old content.
    try:
        with open(abs_path, encoding="utf-8") as f:
            old_content = f.read()
    except OSError as e:
        return err(f"Failed to read file: {e}")

    # Uniqueness check.
    count = old_content.count(parsed.old_string)
    if count == 0:
        return err("old_string not found in file")
    if count > 1:
        return err(
            f"old_string matches {count} locations; provide more context to make "
            f"the match unique"
        )

    # Compute new content.
    new_content = old_content.replace(parsed.old_string, parsed.new_string)
    byte_len = len(new_content.encode("utf-8"))

    # Look up the conversation's approval mode.
    async with get_db() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == ctx.conversation_id)
        )
        conv = result.scalar_one_or_none()
    mode = conv.fs_write_approval_mode if conv else "review"

    # Auto mode: write immediately.
    if mode == "auto":
        try:
            write_result = write_file_in_workspace(workspace, parsed.path, new_content)
        except (ValueError, OSError) as e:
            return err(str(e))
        record_file_write(ctx.run_id, write_result.absolute_path, new_content)
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
                "newContent": new_content,
            }
        )

    # Review mode: register a pending write and wait for the user.
    pending = pending_writes.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        path=parsed.path,
        absolute_path=abs_path,
        old_content=old_content,
        new_content=new_content,
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

    record_file_write(ctx.run_id, abs_path, new_content)
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


fs_edit_tool = ToolDef(
    name="fs_edit",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
