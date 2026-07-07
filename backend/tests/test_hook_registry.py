"""Tests for HookRegistry registration, dispatch, and control flow."""

import asyncio

import pytest

from app.services.hook_registry import (
    HookContext,
    HookEvent,
    HookRegistry,
    HookResult,
)


def _ctx(event: HookEvent = HookEvent.PRE_TOOL_USE, **kw) -> HookContext:
    defaults = dict(
        event=event,
        run_id="run_1",
        agent_id="ag_1",
        conversation_id="conv_1",
    )
    defaults.update(kw)
    return HookContext(**defaults)


# ─── Registration & dispatch ──────────────────────────────────────────────────


async def test_no_handlers_returns_allow():
    reg = HookRegistry()
    result = await reg.dispatch(_ctx())
    assert result.action == "allow"


async def test_handler_invoked():
    reg = HookRegistry()
    called = []

    async def handler(ctx):
        called.append(ctx.tool_name)
        return None

    reg.register(HookEvent.PRE_TOOL_USE, handler, name="test")
    await reg.dispatch(_ctx(tool_name="bash"))
    assert called == ["bash"]


async def test_multiple_handlers_priority_order():
    reg = HookRegistry()
    order = []

    async def h1(ctx):
        order.append("h1")
        return None

    async def h2(ctx):
        order.append("h2")
        return None

    reg.register(HookEvent.POST_TOOL_USE, h2, priority=10, name="h2")
    reg.register(HookEvent.POST_TOOL_USE, h1, priority=5, name="h1")
    await reg.dispatch(_ctx(HookEvent.POST_TOOL_USE))
    assert order == ["h1", "h2"]


# ─── Control flow: deny / modify / inject ────────────────────────────────────


async def test_deny_prevents_execution():
    reg = HookRegistry()

    async def deny_handler(ctx):
        return HookResult(action="deny", data="blocked by policy")

    reg.register(HookEvent.PRE_TOOL_USE, deny_handler, name="blocker")
    result = await reg.dispatch(_ctx())
    assert result.action == "deny"
    assert result.data == "blocked by policy"


async def test_modify_changes_args():
    reg = HookRegistry()
    modified_args = {"command": "echo safe"}

    async def modify_handler(ctx):
        return HookResult(action="modify", data={"args": modified_args})

    reg.register(HookEvent.PRE_TOOL_USE, modify_handler, name="modifier")
    result = await reg.dispatch(_ctx())
    assert result.action == "modify"
    assert result.data == {"args": modified_args}


async def test_inject_adds_events():
    reg = HookRegistry()
    injected = [{"type": "text", "content": "summary"}]

    async def inject_handler(ctx):
        return HookResult(action="inject", data=injected)

    reg.register(HookEvent.ON_STOP, inject_handler, name="injector")
    result = await reg.dispatch(_ctx(HookEvent.ON_STOP))
    assert result.action == "inject"
    assert result.data == injected


async def test_allow_proceeds_normally():
    reg = HookRegistry()

    async def allow_handler(ctx):
        return HookResult(action="allow")

    reg.register(HookEvent.PRE_TOOL_USE, allow_handler, name="allower")
    result = await reg.dispatch(_ctx())
    assert result.action == "allow"


async def test_none_return_treated_as_allow():
    reg = HookRegistry()

    async def none_handler(ctx):
        return None

    reg.register(HookEvent.PRE_TOOL_USE, none_handler, name="none_handler")
    result = await reg.dispatch(_ctx())
    assert result.action == "allow"


# ─── Error handling ──────────────────────────────────────────────────────────


async def test_handler_exception_logged_and_continues():
    reg = HookRegistry()
    called_after_error = []

    async def error_handler(ctx):
        raise ValueError("oops")

    async def normal_handler(ctx):
        called_after_error.append("called")
        return None

    reg.register(HookEvent.PRE_TOOL_USE, error_handler, priority=5, name="error")
    reg.register(HookEvent.PRE_TOOL_USE, normal_handler, priority=10, name="normal")
    result = await reg.dispatch(_ctx())
    # Error handler raises → logged, continues with default allow
    assert result.action == "allow"
    assert called_after_error == ["called"]


async def test_deny_short_circuits():
    reg = HookRegistry()
    called = []

    async def deny_handler(ctx):
        called.append("deny")
        return HookResult(action="deny", data="blocked")

    async def later_handler(ctx):
        called.append("later")
        return None

    reg.register(HookEvent.PRE_TOOL_USE, deny_handler, priority=5, name="deny")
    reg.register(HookEvent.PRE_TOOL_USE, later_handler, priority=10, name="later")
    result = await reg.dispatch(_ctx())
    assert result.action == "deny"
    # deny short-circuits — later handler should NOT be called
    assert called == ["deny"]


# ─── has_handlers ────────────────────────────────────────────────────────────


async def test_has_handlers():
    reg = HookRegistry()
    assert not reg.has_handlers(HookEvent.PRE_TOOL_USE)

    async def handler(ctx):
        return None

    reg.register(HookEvent.PRE_TOOL_USE, handler, name="test")
    assert reg.has_handlers(HookEvent.PRE_TOOL_USE)
    assert not reg.has_handlers(HookEvent.POST_TURN)
