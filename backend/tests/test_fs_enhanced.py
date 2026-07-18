"""Tests for enhanced fs_list (depth / showHidden) and fs_read (mode) tools.

Covers:
- 7.1: fs_list depth — depth=1 format unchanged; depth=3 flat list with
  relativePath / depth; skips node_modules; >500 entries truncated=true
- 7.2: fs_list showHidden — default hides dotfiles; showHidden=true includes
  .env.example
- 7.3: fs_read mode — full unchanged; outline returns skeleton without content;
  head returns first N lines
- 7.4: fs_read outline language coverage — TypeScript / Python / Go / Java
- 7.6: regression — existing agent configs (no new params) behavior unchanged
"""

from __future__ import annotations

import asyncio
import os

import pytest_asyncio

from app.services import conversation_service
from app.services.fs_service import get_workspace_for_conversation
from app.tools.base import ToolContext
from app.tools.fs_list import fs_list_tool
from app.tools.fs_read import fs_read_tool


@pytest_asyncio.fixture
async def ctx(agents, test_user) -> ToolContext:
    conv = await conversation_service.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        title="fs_enhanced test",
        user_id=test_user["id"],
    )
    return ToolContext(
        conversation_id=conv.id,
        workspace_path="",
        agent_id=agents["alice"],
        run_id="run_test_fs_enhanced",
        cancel_event=asyncio.Event(),
    )


async def _write_file(workspace, rel_path: str, content: str = "x") -> None:
    abs_path = os.path.join(workspace.root_path, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


async def _mkdir(workspace, rel_path: str) -> None:
    abs_path = os.path.join(workspace.root_path, rel_path.replace("/", os.sep))
    os.makedirs(abs_path, exist_ok=True)


# ─── 7.1: fs_list depth tests ──────────────────────────────────────────────


async def test_list_depth_1_format_unchanged(ctx):
    """depth=1 (default) returns format identical to current behavior."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "App.tsx", "export default function App() {}")
    await _mkdir(ws, "src")

    result = await fs_list_tool.handler({}, ctx)
    assert result.ok, result.error
    val = result.value
    # depth=1 response has relPath, absolutePath, parent, entries
    assert "relPath" in val
    assert "absolutePath" in val
    assert "parent" in val
    assert "entries" in val
    assert "truncated" not in val  # no truncated field for depth=1
    for entry in val["entries"]:
        assert "name" in entry
        assert "isDirectory" in entry
        # depth=1 entries should NOT have relativePath or depth fields
        assert "relativePath" not in entry
        assert "depth" not in entry


async def test_list_depth_3_returns_flat_list_with_relative_path_and_depth(ctx):
    """depth=3 returns flat list with relativePath and depth fields."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "App.tsx", "content")
    await _write_file(ws, "src/components/Chat.tsx", "content")
    await _write_file(ws, "src/utils/format.ts", "content")
    await _mkdir(ws, "src/components")

    result = await fs_list_tool.handler({"depth": 3}, ctx)
    assert result.ok, result.error
    val = result.value
    assert "entries" in val
    assert "truncated" in val
    assert val["truncated"] is False

    # All entries should have relativePath and depth
    for entry in val["entries"]:
        assert "relativePath" in entry
        assert "depth" in entry
        assert isinstance(entry["depth"], int)
        assert entry["depth"] >= 1

    # Check specific entries exist
    rel_paths = {e["relativePath"] for e in val["entries"]}
    assert "App.tsx" in rel_paths
    assert "src/components/Chat.tsx" in rel_paths
    assert "src/utils/format.ts" in rel_paths

    # Verify depth values
    depth_map = {e["relativePath"]: e["depth"] for e in val["entries"]}
    assert depth_map["App.tsx"] == 1
    assert depth_map["src/components/Chat.tsx"] == 3
    assert depth_map["src/utils/format.ts"] == 3


async def test_list_depth_skips_node_modules(ctx):
    """depth>1 skips dependency directories like node_modules."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "src/real.ts", "content")
    await _write_file(ws, "node_modules/pkg/index.ts", "content")
    await _write_file(ws, ".git/config", "content")

    result = await fs_list_tool.handler({"depth": 3}, ctx)
    assert result.ok, result.error
    rel_paths = {e["relativePath"] for e in result.value["entries"]}

    # node_modules dir is listed (depth=1) but its contents are NOT recursed
    assert any("node_modules" in p for p in rel_paths)
    # Files inside node_modules should not appear
    assert not any("node_modules/pkg" in p for p in rel_paths)
    # .git is a dotfile, hidden by default
    assert not any(".git" in p for p in rel_paths)


async def test_list_depth_exceeds_500_entries_truncated(ctx):
    """depth>1 with >500 entries returns truncated=true."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    # Create 600 files across multiple directories
    for i in range(60):
        for j in range(10):
            await _write_file(ws, f"batch/dir_{i:02d}/file_{j}.txt", "x")

    result = await fs_list_tool.handler({"depth": 5}, ctx)
    assert result.ok, result.error
    assert result.value["truncated"] is True
    assert len(result.value["entries"]) <= 500


# ─── 7.2: fs_list showHidden tests ─────────────────────────────────────────


async def test_list_default_hides_dotfiles(ctx):
    """Default (showHidden=false) hides dotfiles."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, ".env.example", "FOO=bar")
    await _write_file(ws, ".eslintrc.json", "{}")
    await _write_file(ws, "visible.ts", "export const x = 1")

    result = await fs_list_tool.handler({}, ctx)
    assert result.ok, result.error
    names = {e["name"] for e in result.value["entries"]}
    assert "visible.ts" in names
    assert ".env.example" not in names
    assert ".eslintrc.json" not in names


async def test_list_showHidden_true_includes_dotfiles(ctx):
    """showHidden=true includes .env.example and other dotfiles."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, ".env.example", "FOO=bar")
    await _write_file(ws, ".eslintrc.json", "{}")
    await _write_file(ws, "visible.ts", "export const x = 1")

    result = await fs_list_tool.handler({"showHidden": True}, ctx)
    assert result.ok, result.error
    names = {e["name"] for e in result.value["entries"]}
    assert ".env.example" in names
    assert ".eslintrc.json" in names
    assert "visible.ts" in names


async def test_list_showHidden_with_depth(ctx):
    """showHidden=true works with depth>1."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, ".env.example", "FOO=bar")
    await _write_file(ws, "src/.eslintrc.json", "{}")
    await _write_file(ws, "src/visible.ts", "x")

    result = await fs_list_tool.handler({"depth": 3, "showHidden": True}, ctx)
    assert result.ok, result.error
    rel_paths = {e["relativePath"] for e in result.value["entries"]}
    assert ".env.example" in rel_paths
    assert "src/.eslintrc.json" in rel_paths


# ─── 7.3: fs_read mode tests ───────────────────────────────────────────────


async def test_read_mode_full_format_unchanged(ctx):
    """mode=full (default) returns format identical to current behavior."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    content = "line1\nline2\nline3\n"
    await _write_file(ws, "test.ts", content)

    result = await fs_read_tool.handler({"path": "test.ts"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert "content" in val
    assert val["content"] == content
    assert "mode" not in val  # full mode doesn't add mode field
    assert "outline" not in val


async def test_read_mode_outline_returns_skeleton_without_content(ctx):
    """mode=outline returns outline array without content field."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    ts_code = (
        "import React from 'react'\n"
        "import { useState } from 'react'\n"
        "\n"
        "interface Props {\n"
        "  name: string\n"
        "}\n"
        "\n"
        "export function App(props: Props): JSX.Element {\n"
        "  const [count, setCount] = useState(0)\n"
        "  return null\n"
        "}\n"
    )
    await _write_file(ws, "App.tsx", ts_code)

    result = await fs_read_tool.handler({"path": "App.tsx", "mode": "outline"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["mode"] == "outline"
    assert val["language"] == "typescript"
    assert "outline" in val
    assert "content" not in val
    assert "totalLines" in val
    assert "fullSize" in val
    assert isinstance(val["outline"], list)
    assert len(val["outline"]) > 0

    # Check outline entries have required fields
    for item in val["outline"]:
        assert "type" in item
        assert "line" in item
        assert "content" in item

    # Verify import and function are detected
    types = {item["type"] for item in val["outline"]}
    assert "import" in types
    assert "function" in types


async def test_read_mode_head_returns_first_n_lines(ctx):
    """mode=head returns first N lines with truncated flag."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    content = "\n".join(f"line {i}" for i in range(1, 101)) + "\n"
    await _write_file(ws, "big.ts", content)

    # Default head limit is 50
    result = await fs_read_tool.handler({"path": "big.ts", "mode": "head"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["mode"] == "head"
    assert val["startLine"] == 1
    assert val["endLine"] == 50
    assert val["totalLines"] == 100
    assert val["truncated"] is True
    # Content should be first 50 lines
    lines = val["content"].splitlines()
    assert len(lines) == 50
    assert lines[0] == "line 1"

    # Custom limit
    result2 = await fs_read_tool.handler(
        {"path": "big.ts", "mode": "head", "limit": 10}, ctx
    )
    assert result2.ok, result2.error
    assert result2.value["endLine"] == 10
    assert result2.value["truncated"] is True


async def test_read_mode_head_short_file_not_truncated(ctx):
    """mode=head on a short file returns all lines, truncated=false."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    content = "line1\nline2\nline3\n"
    await _write_file(ws, "small.ts", content)

    result = await fs_read_tool.handler({"path": "small.ts", "mode": "head"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["truncated"] is False
    assert val["totalLines"] == 3
    assert val["endLine"] == 3


async def test_read_mode_outline_empty_result_with_note(ctx):
    """mode=outline on a file with no structure returns empty outline + note."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "plain.txt", "just some text\nno structure here\n")

    result = await fs_read_tool.handler({"path": "plain.txt", "mode": "outline"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["outline"] == []
    assert "note" in val
    assert "full" in val["note"].lower()


# ─── 7.4: fs_read outline language coverage ────────────────────────────────


async def test_read_outline_typescript(ctx):
    """Outline extracts TypeScript structure."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    ts_code = (
        "import { useState } from 'react'\n"
        "import type { FC } from 'react'\n"
        "\n"
        "interface User {\n"
        "  id: number\n"
        "}\n"
        "\n"
        "type Status = 'active' | 'inactive'\n"
        "\n"
        "export const App: FC = () => {\n"
        "  const [user, setUser] = useState<User | null>(null)\n"
        "  return null\n"
        "}\n"
        "\n"
        "export async function fetchData(): Promise<User> {\n"
        "  return { id: 1 }\n"
        "}\n"
    )
    await _write_file(ws, "App.tsx", ts_code)

    result = await fs_read_tool.handler({"path": "App.tsx", "mode": "outline"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["language"] == "typescript"
    types = {item["type"] for item in val["outline"]}
    assert "import" in types
    assert "interface" in types
    assert "type" in types
    assert "variable" in types
    assert "function" in types


async def test_read_outline_python(ctx):
    """Outline extracts Python structure."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    py_code = (
        "import os\n"
        "from typing import Optional\n"
        "\n"
        "class UserService:\n"
        "    def __init__(self):\n"
        "        self.users = []\n"
        "\n"
        "    async def get_user(self, user_id: int) -> Optional[dict]:\n"
        "        pass\n"
        "\n"
        "def main():\n"
        "    pass\n"
        "\n"
        "async def fetch_data() -> list:\n"
        "    return []\n"
    )
    await _write_file(ws, "service.py", py_code)

    result = await fs_read_tool.handler({"path": "service.py", "mode": "outline"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["language"] == "python"
    types = {item["type"] for item in val["outline"]}
    assert "import" in types
    assert "class" in types
    assert "function" in types

    # Verify function signatures are captured
    contents = [item["content"] for item in val["outline"]]
    assert any("def get_user" in c for c in contents)
    assert any("async def fetch_data" in c for c in contents)


async def test_read_outline_go(ctx):
    """Outline extracts Go structure."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    go_code = (
        'package main\n'
        '\n'
        'import (\n'
        '    "fmt"\n'
        '    "os"\n'
        ')\n'
        '\n'
        'type Config struct {\n'
        '    Port int\n'
        '}\n'
        '\n'
        'type Handler interface {\n'
        '    Serve() error\n'
        '}\n'
        '\n'
        'func main() {\n'
        '    fmt.Println("hello")\n'
        '}\n'
        '\n'
        'func NewConfig() *Config {\n'
        '    return &Config{Port: 8080}\n'
        '}\n'
    )
    await _write_file(ws, "main.go", go_code)

    result = await fs_read_tool.handler({"path": "main.go", "mode": "outline"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["language"] == "go"
    types = {item["type"] for item in val["outline"]}
    assert "import" in types
    assert "type" in types
    assert "func" in types

    contents = [item["content"] for item in val["outline"]]
    assert any("func main" in c for c in contents)
    assert any("type Config struct" in c for c in contents)


async def test_read_outline_java(ctx):
    """Outline extracts Java structure."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    java_code = (
        "package com.example;\n"
        "\n"
        "import java.util.List;\n"
        "import java.util.ArrayList;\n"
        "\n"
        "public class UserService {\n"
        "    private List<String> users;\n"
        "\n"
        "    public void addUser(String name) {\n"
        "        users.add(name);\n"
        "    }\n"
        "\n"
        "    public List<String> getUsers() {\n"
        "        return users;\n"
        "    }\n"
        "}\n"
        "\n"
        "interface UserRepository {\n"
        "    List<String> findAll();\n"
        "}\n"
    )
    await _write_file(ws, "UserService.java", java_code)

    result = await fs_read_tool.handler(
        {"path": "UserService.java", "mode": "outline"}, ctx
    )
    assert result.ok, result.error
    val = result.value
    assert val["language"] == "java"
    types = {item["type"] for item in val["outline"]}
    assert "import" in types
    assert "class" in types
    assert "interface" in types

    contents = [item["content"] for item in val["outline"]]
    assert any("class UserService" in c for c in contents)


# ─── 7.6: Regression test ──────────────────────────────────────────────────


async def test_list_no_new_params_behavior_unchanged(ctx):
    """Calling fs_list without depth/showHidden behaves exactly as before."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    await _write_file(ws, "visible.ts", "x")
    await _write_file(ws, ".hidden", "y")

    # No params at all — same as before
    result = await fs_list_tool.handler({}, ctx)
    assert result.ok, result.error
    val = result.value
    assert "parent" in val
    assert "truncated" not in val
    names = {e["name"] for e in val["entries"]}
    assert "visible.ts" in names
    assert ".hidden" not in names  # dotfiles hidden by default


async def test_read_no_mode_param_behavior_unchanged(ctx):
    """Calling fs_read without mode behaves exactly as before."""
    ws = await get_workspace_for_conversation(ctx.conversation_id)
    content = "line1\nline2\nline3\n"
    await _write_file(ws, "test.ts", content)

    # No mode param — same as before
    result = await fs_read_tool.handler({"path": "test.ts"}, ctx)
    assert result.ok, result.error
    val = result.value
    assert val["content"] == content
    assert "mode" not in val
    assert "outline" not in val

    # With offset/limit but no mode — same as before
    result2 = await fs_read_tool.handler(
        {"path": "test.ts", "offset": 1, "limit": 1}, ctx
    )
    assert result2.ok, result2.error
    assert "content" in result2.value
    assert "mode" not in result2.value
