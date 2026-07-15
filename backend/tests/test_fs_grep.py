"""Tests for the fs_grep tool — regex text search.

Covers: structured match return, binary file skipping, dependency directory
skipping, result truncation flag, timeout partial results, and sandbox escape
rejection.
"""

from __future__ import annotations

import asyncio
import os

import pytest_asyncio

from app.services import conversation_service
from app.services.fs_service import get_workspace_for_conversation
from app.tools import fs_grep as fs_grep_mod
from app.tools.base import ToolContext
from app.tools.fs_grep import fs_grep_tool


@pytest_asyncio.fixture
async def ctx(agents) -> ToolContext:
    conv = await conversation_service.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        title="fs_grep test",
    )
    return ToolContext(
        conversation_id=conv.id,
        workspace_path="",
        agent_id=agents["alice"],
        run_id="run_test_grep",
        cancel_event=asyncio.Event(),
    )


async def _write_file(workspace, rel_path: str, content: str) -> None:
    abs_path = os.path.join(workspace.root_path, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


async def _write_binary_file(workspace, rel_path: str) -> None:
    abs_path = os.path.join(workspace.root_path, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(b"\x00\x01\x02\x00binary\xff\xfe")


# --- structured match return ---------------------------------------------------


async def test_grep_returns_structured_matches(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "app.ts", "import { useState } from 'react'\nconst x = useState(0)\n")
    await _write_file(ws, "other.ts", "console.log('hello')\n")

    result = await fs_grep_tool.handler({"pattern": "useState"}, ctx)
    assert result.ok, result.error
    matches = result.value["matches"]
    assert len(matches) == 2
    for m in matches:
        assert m["file"].endswith("app.ts")
        assert "line_number" in m
        assert "line" in m
        assert m["match"] == "useState"
    # Line numbers are correct
    line_nos = [m["line_number"] for m in matches]
    assert 1 in line_nos
    assert 2 in line_nos


async def test_grep_regex_pattern(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "code.py", "def foo():\n    bar = 1\n    return bar\n")

    result = await fs_grep_tool.handler({"pattern": r"bar"}, ctx)
    assert result.ok, result.error
    matches = result.value["matches"]
    assert len(matches) == 2  # "bar = 1" and "return bar"
    assert all("bar" in m["match"] for m in matches)


# --- binary file skipping ------------------------------------------------------


async def test_grep_skips_binary_files(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "text.txt", "findme here\n")
    await _write_binary_file(ws, "binary.dat")

    result = await fs_grep_tool.handler({"pattern": "findme"}, ctx)
    assert result.ok, result.error
    matches = result.value["matches"]
    # Only the text file is searched; binary file is skipped
    assert len(matches) == 1
    assert matches[0]["file"].endswith("text.txt")


# --- dependency directory skipping ---------------------------------------------


async def test_grep_skips_dependency_dirs(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "src/real.ts", "export const target = 1\n")
    # Create a file inside node_modules — should be skipped
    await _write_file(ws, "node_modules/pkg/index.ts", "const target = 2\n")
    # Create a file inside .git — should be skipped
    await _write_file(ws, ".git/config", "target = 3\n")

    result = await fs_grep_tool.handler({"pattern": "target"}, ctx)
    assert result.ok, result.error
    matches = result.value["matches"]
    files = {m["file"] for m in matches}
    # Only src/real.ts should match
    assert any("real.ts" in f for f in files)
    assert not any("node_modules" in f for f in files)
    assert not any(".git" in f for f in files)


# --- result truncation ---------------------------------------------------------


async def test_grep_result_truncation(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    # Per-file cap is 50, so create multiple files to exceed total max_results (100)
    for i in range(3):
        lines = "\n".join("matchline" for _ in range(60))
        await _write_file(ws, f"batch/file_{i}.txt", lines + "\n")

    result = await fs_grep_tool.handler({"pattern": "matchline"}, ctx)
    assert result.ok, result.error
    # 3 files × 50 per-file cap = 150 total, but max_results caps at 100
    assert len(result.value["matches"]) == 100
    assert result.value["total_matches"] == 150
    assert result.value["truncated"] is True


async def test_grep_custom_max_results(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    lines = "\n".join("hit" for _ in range(20))
    await _write_file(ws, "small.txt", lines + "\n")

    result = await fs_grep_tool.handler({"pattern": "hit", "max_results": 5}, ctx)
    assert result.ok, result.error
    assert len(result.value["matches"]) == 5
    assert result.value["truncated"] is True


# --- per-file match cap --------------------------------------------------------


async def test_grep_per_file_cap(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    # Create a file with 60 matches — per-file cap is 50
    lines = "\n".join("captest" for _ in range(60))
    await _write_file(ws, "caps.txt", lines + "\n")

    result = await fs_grep_tool.handler({"pattern": "captest"}, ctx)
    assert result.ok, result.error
    # Per-file cap is 50, total cap is 100; 50 < 100 so all 50 are returned
    assert len(result.value["matches"]) == 50
    assert result.value["total_matches"] == 50  # per-file cap stopped at 50


# --- timeout returns partial results -------------------------------------------


async def test_grep_timeout_returns_partial(ctx, monkeypatch):
    # Mock time.monotonic so each call advances 0.1s, guaranteeing the
    # 0.05s timeout triggers on the first per-line check.
    call_count = [0]

    def mock_monotonic():
        call_count[0] += 1
        return call_count[0] * 0.1

    monkeypatch.setattr(fs_grep_mod.time, "monotonic", mock_monotonic)
    monkeypatch.setattr(fs_grep_mod, "SEARCH_TIMEOUT_SECONDS", 0.05)
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "slow.txt", "line1 timeout_match\nline2\nline3\n")

    result = await fs_grep_tool.handler({"pattern": "timeout_match"}, ctx)
    assert result.ok, result.error
    assert result.value["truncated"] is True
    assert result.value.get("timeout") is True


# --- sandbox escape rejection --------------------------------------------------


async def test_grep_rejects_path_escape(ctx):
    result = await fs_grep_tool.handler(
        {"pattern": "test", "path": "../../.ssh"}, ctx
    )
    assert not result.ok
    assert "outside" in result.error.lower()


# --- glob filter ---------------------------------------------------------------


async def test_grep_glob_filter(ctx):
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "a.py", "search_target = 1\n")
    await _write_file(ws, "b.txt", "search_target = 2\n")

    result = await fs_grep_tool.handler({"pattern": "search_target", "glob": "*.py"}, ctx)
    assert result.ok, result.error
    matches = result.value["matches"]
    assert len(matches) == 1
    assert matches[0]["file"].endswith("a.py")


# --- invalid regex -------------------------------------------------------------


async def test_grep_invalid_regex(ctx):
    result = await fs_grep_tool.handler({"pattern": "[invalid"}, ctx)
    assert not result.ok
    assert "regex" in result.error.lower()
