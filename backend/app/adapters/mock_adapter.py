"""MockAdapter — no real LLM, streams events from preset scripts.

Port of src/server/adapters/mock-adapter.ts. See specs/05-adapter-interface.md.

Uses:
  1. develop without burning tokens
  2. end-to-end skeleton checks (SSE / store / UI rendering)
  3. demo-environment fallback

Behaviour:
  - picks a response script by prompt keyword (so demos look "smart")
  - char-level streaming (tiny sleeps) to mimic real cadence
  - honours cancel_event (the TS AbortSignal)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.adapters.base import AdapterInput, AdapterName, AgentPlatformAdapter
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    StreamEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.utils.clock import now_ms
from app.utils.ids import new_message_id, new_tool_call_id

# small sleeps keep tests fast while preserving the streaming cadence
_TEXT_DELAY_S = 0.005
_THINKING_DELAY_S = 0.005
_TOOL_DELAY_S = 0.005


class MockAdapter(AgentPlatformAdapter):
    @property
    def name(self) -> AdapterName:
        return "mock"

    async def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        script = _pick_script(input.prompt)

        message_id = new_message_id()
        yield MessageStartEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            agent_id=input.agent_id,
            run_id=input.run_id,
        )

        part_index = -1

        for step in script:
            if cancel_event.is_set():
                break

            part_index += 1

            if step.kind == "text":
                yield PartStartEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                    part={"type": "text", "content": ""},
                )
                for chunk in _chunk_text(step.content, 4):
                    if cancel_event.is_set():
                        break
                    await asyncio.sleep(_TEXT_DELAY_S)
                    yield PartDeltaEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=part_index,
                        delta={"type": "text.append", "text": chunk},
                    )
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                )
            elif step.kind == "thinking":
                yield PartStartEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                    part={"type": "thinking", "content": ""},
                )
                for chunk in _chunk_text(step.content, 8):
                    if cancel_event.is_set():
                        break
                    await asyncio.sleep(_THINKING_DELAY_S)
                    yield PartDeltaEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=part_index,
                        delta={"type": "thinking.append", "text": chunk},
                    )
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                )
            elif step.kind == "code":
                yield PartStartEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                    part={"type": "code", "language": step.language, "content": ""},
                )
                for chunk in _chunk_text(step.content, 8):
                    if cancel_event.is_set():
                        break
                    await asyncio.sleep(_THINKING_DELAY_S)
                    yield PartDeltaEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=part_index,
                        delta={"type": "code.append", "text": chunk},
                    )
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=part_index,
                )
            elif step.kind == "tool":
                call_id = new_tool_call_id()
                yield ToolCallEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    tool_name=step.tool_name,
                    args=step.args,
                )
                await asyncio.sleep(_TOOL_DELAY_S)
                yield ToolResultEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    result=step.result if step.result is not None else {"ok": True},
                    is_error=False,
                )

        yield MessageEndEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
        )


# ─── Script step ─────────────────────────────────────────────
@dataclass
class _ScriptStep:
    kind: str  # 'text' | 'thinking' | 'code' | 'tool'
    content: str = ""
    language: str = ""
    tool_name: str = ""
    args: Any = None
    result: Any = None


# ─── Built-in scripts ─────────────────────────────────────────────
_GREETING_SCRIPT: list[_ScriptStep] = [
    _ScriptStep(kind="thinking", content="用户在问候，我应该礼貌回应并介绍自己。"),
    _ScriptStep(
        kind="text",
        content=(
            "你好！我是 Mock Agent，目前用于验证 AgentHub 的端到端骨架。我会按预设脚本流式回复，"
            "不消耗任何 LLM token。\n\n你可以试试输入「写代码」或「执行任务」看其他场景。"
        ),
    ),
]

_CODE_SCRIPT: list[_ScriptStep] = [
    _ScriptStep(kind="thinking", content="用户希望看到代码示例，我演示一段 React 组件代码。"),
    _ScriptStep(kind="text", content="好的，这是一个简单的 React 计数器组件："),
    _ScriptStep(
        kind="code",
        language="tsx",
        content="""import { useState } from 'react'

export function Counter() {
  const [count, setCount] = useState(0)
  return (
    <div className="flex items-center gap-2">
      <button onClick={() => setCount(c => c - 1)}>-</button>
      <span>{count}</span>
      <button onClick={() => setCount(c => c + 1)}>+</button>
    </div>
  )
}""",
    ),
    _ScriptStep(
        kind="text", content="这是最朴素的实现。需要扩展可以告诉我（持久化、键盘快捷键等）。"
    ),
]

_TOOL_SCRIPT: list[_ScriptStep] = [
    _ScriptStep(kind="thinking", content="我需要演示工具调用流程。"),
    _ScriptStep(kind="text", content="我先调用工具收集信息："),
    _ScriptStep(
        kind="tool",
        tool_name="read_artifact",
        args={"artifactId": "art_demo"},
        result={"title": "示例产物", "size": 1024},
    ),
    _ScriptStep(
        kind="text", content="已读取产物信息。这只是脚本演示，真实工具会在后续 milestone 接入。"
    ),
]

_DEFAULT_SCRIPT: list[_ScriptStep] = [
    _ScriptStep(kind="thinking", content="收到了用户消息，按通用模板回应。"),
    _ScriptStep(
        kind="text",
        content=(
            "我收到了你的消息。这是 MockAdapter 的默认响应，用于验证消息流式渲染、part 切换、"
            'tool 调用等链路。\n\n试试输入 "你好"、"写代码"、"执行任务" 触发不同脚本。'
        ),
    ),
]


def _pick_script(prompt: str) -> list[_ScriptStep]:
    p = prompt.lower()
    if any(kw in p for kw in ("你好", "hello", "hi", "您好")):
        return _GREETING_SCRIPT
    if any(kw in p for kw in ("写代码", "代码", "code", "component", "组件")):
        return _CODE_SCRIPT
    if any(kw in p for kw in ("执行", "工具", "tool", "run", "跑")):
        return _TOOL_SCRIPT
    return _DEFAULT_SCRIPT


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]
