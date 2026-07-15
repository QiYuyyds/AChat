"""Tests for ToolExecutor.execute_with_hooks (Phase 3)."""

import asyncio

from app.services.hook_registry import (
    HookEvent,
    HookRegistry,
    HookResult,
)
from app.tools.base import ToolContext, ToolDef, ok
from app.tools.registry import ToolRegistry


def _ctx() -> ToolContext:
    return ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_1",
        cancel_event=asyncio.Event(),
    )


# ── Test tool ─────────────────────────────────────────────────────────────────


async def _echo_handler(args, ctx):
    return ok({"echoed": args})


_echo_tool = ToolDef(
    name="test_echo",
    description="echo args",
    parameters={"type": "object", "properties": {}},
    handler=_echo_handler,
)


def _registry_with_echo() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_echo_tool)
    return reg


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_no_hooks_falls_back_to_execute():
    """execute_with_hooks with no hook_registry calls execute directly."""
    reg = _registry_with_echo()
    result = await reg.execute_with_hooks("test_echo", {"x": 1}, _ctx(), hook_registry=None)
    assert result.ok
    assert result.value == {"echoed": {"x": 1}}


async def test_allow_hook_executes_normally():
    """A pre_tool_use hook returning allow lets the tool execute."""
    reg = _registry_with_echo()
    hooks = HookRegistry()

    async def allow_handler(ctx):
        return HookResult(action="allow")

    hooks.register(HookEvent.PRE_TOOL_USE, allow_handler, name="allow")
    result = await reg.execute_with_hooks("test_echo", {"x": 2}, _ctx(), hook_registry=hooks)
    assert result.ok
    assert result.value == {"echoed": {"x": 2}}


async def test_deny_hook_blocks_execution():
    """A pre_tool_use hook returning deny prevents tool execution."""
    reg = _registry_with_echo()
    hooks = HookRegistry()

    async def deny_handler(ctx):
        return HookResult(action="deny", data="blocked by policy")

    hooks.register(HookEvent.PRE_TOOL_USE, deny_handler, name="deny")
    result = await reg.execute_with_hooks("test_echo", {"x": 3}, _ctx(), hook_registry=hooks)
    assert not result.ok
    assert "blocked by policy" in result.error


async def test_modify_hook_changes_args():
    """A pre_tool_use hook returning modify changes the tool args."""
    reg = _registry_with_echo()
    hooks = HookRegistry()

    async def modify_handler(ctx):
        return HookResult(action="modify", data={"args": {"modified": True}})

    hooks.register(HookEvent.PRE_TOOL_USE, modify_handler, name="modify")
    result = await reg.execute_with_hooks("test_echo", {"x": 4}, _ctx(), hook_registry=hooks)
    assert result.ok
    assert result.value == {"echoed": {"modified": True}}


async def test_post_modify_hook_changes_result():
    """A post_tool_use hook returning modify changes the tool result."""
    reg = _registry_with_echo()
    hooks = HookRegistry()

    async def post_modify(ctx):
        return HookResult(action="modify", data={"result": {"overridden": True}})

    hooks.register(HookEvent.POST_TOOL_USE, post_modify, name="post_modify")
    result = await reg.execute_with_hooks("test_echo", {"x": 5}, _ctx(), hook_registry=hooks)
    assert result.ok
    assert result.value == {"overridden": True}


async def test_post_hook_receives_error_info():
    """post_tool_use context includes is_error when the tool fails."""
    reg = _registry_with_echo()
    hooks = HookRegistry()
    received = []

    async def post_handler(ctx):
        received.append(ctx.is_error)
        return None

    hooks.register(HookEvent.POST_TOOL_USE, post_handler, name="post")
    # Execute with unknown tool → error
    result = await reg.execute_with_hooks("nonexistent", {}, _ctx(), hook_registry=hooks)
    assert not result.ok
    assert received == [True]
