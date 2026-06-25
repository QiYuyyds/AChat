"""Tests for MockAdapter scripted streaming (phase 4)."""

import asyncio

from app.adapters.base import AdapterInput
from app.adapters.mock_adapter import MockAdapter


def _make_input(prompt: str) -> AdapterInput:
    return AdapterInput(
        agent_id="ag_test",
        conversation_id="conv_test",
        run_id="run_test",
        prompt=prompt,
        workspace_path="/tmp/ws",
        system_prompt="sys",
        api_key=None,
        api_base_url=None,
        model_id=None,
        tool_names=[],
    )


async def _drain(prompt: str, cancel_event: asyncio.Event | None = None) -> list:
    adapter = MockAdapter()
    cancel = cancel_event or asyncio.Event()
    return [event async for event in adapter.stream(_make_input(prompt), cancel)]


async def test_name_is_mock():
    assert MockAdapter().name == "mock"


async def test_greeting_script_brackets_parts():
    events = await _drain("你好")
    types = [e.type for e in events]
    assert types[0] == "message.start"
    assert types[-1] == "message.end"
    # greeting = one thinking part + one text part, each start..delta..end
    assert types.count("part.start") == 2
    assert types.count("part.end") == 2
    assert types.count("message.start") == 1
    assert types.count("message.end") == 1


async def test_tool_script_emits_call_and_result():
    events = await _drain("执行任务")
    types = [e.type for e in events]
    assert "tool.call" in types
    assert "tool.result" in types
    call = next(e for e in events if e.type == "tool.call")
    result = next(e for e in events if e.type == "tool.result")
    assert call.tool_name == "read_artifact"
    assert call.call_id == result.call_id
    assert result.is_error is False
    # tool result value stays camelCase / passes the scripted payload through
    assert result.result == {"title": "示例产物", "size": 1024}


async def test_part_indices_are_monotonic():
    events = await _drain("写代码")
    starts = [e.part_index for e in events if e.type == "part.start"]
    assert starts == sorted(starts)
    assert starts == list(range(len(starts)))


async def test_cancel_short_circuits():
    cancel = asyncio.Event()
    cancel.set()
    events = await _drain("你好", cancel)
    types = [e.type for e in events]
    # cancel before the first step → only the bracketing message events
    assert types == ["message.start", "message.end"]
