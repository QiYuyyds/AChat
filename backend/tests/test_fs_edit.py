"""Tests for the fs_edit tool — precise in-place string replacement.

Covers: unique-match replacement success, zero-match rejection, multi-match
rejection, review-mode pending write registration + approve/reject, large-file
rejection, sandbox escape rejection, file-not-found, and run-abort cancellation.
"""

from __future__ import annotations

import asyncio
import os

import pytest_asyncio
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Conversation
from app.services import conversation_service
from app.services.fs_service import MAX_READ_BYTES, get_workspace_for_conversation
from app.services.pending_writes import pending_writes
from app.tools.base import ToolContext
from app.tools.fs_edit import fs_edit_tool


@pytest_asyncio.fixture
async def ctx(agents) -> ToolContext:
    conv = await conversation_service.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        title="fs_edit test",
    )
    return ToolContext(
        conversation_id=conv.id,
        workspace_path="",
        agent_id=agents["alice"],
        run_id="run_test_edit",
        cancel_event=asyncio.Event(),
    )


async def _set_auto_mode(conversation_id: str) -> None:
    """Set the conversation's fs_write_approval_mode to 'auto'."""
    async with get_db() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv:
            conv.fs_write_approval_mode = "auto"


async def _write_file(workspace, rel_path: str, content: str) -> None:
    abs_path = os.path.join(workspace.root_path, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


# --- unique match replacement (auto mode) --------------------------------------


async def test_edit_unique_match_auto(ctx):
    await _set_auto_mode(ctx.conversation_id)
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "code.py", "def hello():\n    print('world')\n")

    result = await fs_edit_tool.handler(
        {"path": "code.py", "old_string": "print('world')", "new_string": "print('hello')"},
        ctx,
    )
    assert result.ok, result.error
    assert result.value["applied"] == "auto"
    # Verify the file was actually changed
    abs_path = os.path.join(ws.root_path, "code.py")
    with open(abs_path, encoding="utf-8") as f:
        content = f.read()
    assert "print('hello')" in content
    assert "print('world')" not in content


# --- zero matches rejected -----------------------------------------------------


async def test_edit_zero_matches_rejected(ctx):
    await _set_auto_mode(ctx.conversation_id)
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "code.py", "def hello():\n    pass\n")

    result = await fs_edit_tool.handler(
        {"path": "code.py", "old_string": "nonexistent", "new_string": "x"},
        ctx,
    )
    assert not result.ok
    assert "not found" in result.error.lower()
    # File not modified
    abs_path = os.path.join(ws.root_path, "code.py")
    with open(abs_path, encoding="utf-8") as f:
        assert "pass" in f.read()


# --- multiple matches rejected -------------------------------------------------


async def test_edit_multiple_matches_rejected(ctx):
    await _set_auto_mode(ctx.conversation_id)
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "code.py", "x = 1\nx = 1\n")

    result = await fs_edit_tool.handler(
        {"path": "code.py", "old_string": "x = 1", "new_string": "x = 2"},
        ctx,
    )
    assert not result.ok
    assert "2 locations" in result.error or "multiple" in result.error.lower()
    # File not modified
    abs_path = os.path.join(ws.root_path, "code.py")
    with open(abs_path, encoding="utf-8") as f:
        content = f.read()
    assert content.count("x = 1") == 2


# --- file not found ------------------------------------------------------------


async def test_edit_file_not_found(ctx):
    await _set_auto_mode(ctx.conversation_id)
    result = await fs_edit_tool.handler(
        {"path": "missing.py", "old_string": "a", "new_string": "b"},
        ctx,
    )
    assert not result.ok
    assert "not found" in result.error.lower()


# --- large file rejected -------------------------------------------------------


async def test_edit_large_file_rejected(ctx):
    await _set_auto_mode(ctx.conversation_id)
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    # Create a file larger than MAX_READ_BYTES (1 MB)
    big_content = "x" * (MAX_READ_BYTES + 1)
    await _write_file(ws, "big.txt", big_content)

    result = await fs_edit_tool.handler(
        {"path": "big.txt", "old_string": "x", "new_string": "y"},
        ctx,
    )
    assert not result.ok
    assert "too large" in result.error.lower()
    assert "fs_write" in result.error


# --- sandbox escape rejected ---------------------------------------------------


async def test_edit_sandbox_escape_rejected(ctx):
    await _set_auto_mode(ctx.conversation_id)
    result = await fs_edit_tool.handler(
        {"path": "../../.ssh/id_rsa", "old_string": "a", "new_string": "b"},
        ctx,
    )
    assert not result.ok
    assert "outside" in result.error.lower()


# --- review mode: approve ------------------------------------------------------


async def test_edit_review_mode_approve(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    # Default mode is 'review'
    await _write_file(ws, "review.txt", "line one\nline two\nline three\n")

    # Start the handler in a background task (it will block on pending approval)
    task = asyncio.create_task(
        fs_edit_tool.handler(
            {"path": "review.txt", "old_string": "line two", "new_string": "LINE TWO"},
            ctx,
        )
    )

    # Wait for the pending write to be registered
    pending = None
    for _ in range(50):  # up to 5 seconds
        writes = pending_writes.list_by_conversation(ctx.conversation_id)
        if writes:
            pending = writes[0]
            break
        await asyncio.sleep(0.1)

    assert pending is not None, "Pending write was not registered"
    assert pending.old_content == "line one\nline two\nline three\n"
    assert pending.new_content == "line one\nLINE TWO\nline three\n"

    # Approve the pending write
    approved = pending_writes.approve(pending.id)
    assert approved

    # Wait for the handler to return
    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.ok, result.error
    assert result.value["applied"] == "review"

    # Verify the file was written
    abs_path = os.path.join(ws.root_path, "review.txt")
    with open(abs_path, encoding="utf-8") as f:
        content = f.read()
    assert "LINE TWO" in content
    assert "line two" not in content


# --- review mode: reject -------------------------------------------------------


async def test_edit_review_mode_reject(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "reject.txt", "original content\n")

    task = asyncio.create_task(
        fs_edit_tool.handler(
            {"path": "reject.txt", "old_string": "original", "new_string": "modified"},
            ctx,
        )
    )

    # Wait for the pending write
    pending = None
    for _ in range(50):
        writes = pending_writes.list_by_conversation(ctx.conversation_id)
        if writes:
            pending = writes[0]
            break
        await asyncio.sleep(0.1)

    assert pending is not None

    # Reject the pending write
    pending_writes.reject(pending.id)

    result = await asyncio.wait_for(task, timeout=5.0)
    assert not result.ok
    assert "rejected" in result.error.lower()

    # File should be unchanged
    abs_path = os.path.join(ws.root_path, "reject.txt")
    with open(abs_path, encoding="utf-8") as f:
        assert "original" in f.read()


# --- run abort cancellation ----------------------------------------------------


async def test_edit_run_abort_cancel(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "abort.txt", "keep me\n")

    task = asyncio.create_task(
        fs_edit_tool.handler(
            {"path": "abort.txt", "old_string": "keep me", "new_string": "changed"},
            ctx,
        )
    )

    # Wait for the pending write
    pending = None
    for _ in range(50):
        writes = pending_writes.list_by_conversation(ctx.conversation_id)
        if writes:
            pending = writes[0]
            break
        await asyncio.sleep(0.1)

    assert pending is not None

    # Trigger run abort via cancel_event
    ctx.cancel_event.set()

    result = await asyncio.wait_for(task, timeout=5.0)
    assert not result.ok  # cancelled → not applied

    # File should be unchanged
    abs_path = os.path.join(ws.root_path, "abort.txt")
    with open(abs_path, encoding="utf-8") as f:
        assert "keep me" in f.read()
