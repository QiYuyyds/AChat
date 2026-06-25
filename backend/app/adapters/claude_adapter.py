"""ClaudeAdapter — Anthropic Messages API adapter with its own tool loop.

Loosely a port of src/server/adapters/custom-agent-adapter.ts (the tool-loop
structure), but the Python Claude path talks to the Anthropic Messages API
directly via the ``anthropic`` SDK instead of wrapping the Claude Code CLI —
the user routes Anthropic through a gateway. See specs/05-adapter-interface.md.

Tool loop: stream a turn → emit text part.* deltas → collect tool_use blocks →
on stop_reason 'tool_use' run the tools via ToolExecutor → append assistant
tool_use + user tool_result blocks → loop, until the model stops or MAX_TURNS.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import select

from app.adapters.base import AdapterInput, AdapterName, AgentPlatformAdapter
from app.db.engine import get_db
from app.db.models import Artifact
from app.schemas.artifacts import ArtifactRecord
from app.schemas.events import (
    ArtifactCreateEvent,
    DeployStatusEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUsageEventPayload,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RunUsageEvent,
    StreamEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.schemas.messages import DeployStatusRecord, MessageUsage, RunUsage
from app.tools.base import ToolContext, ToolDef
from app.tools.registry import tool_registry
from app.utils.clock import now_ms
from app.utils.ids import new_message_id

# Cap the tool loop so a misbehaving model can't spin forever.
MAX_TURNS = 8
# Anthropic requires an explicit max_tokens; pick a generous default.
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MODEL = "claude-sonnet-4-5"


class ClaudeAdapter(AgentPlatformAdapter):
    @property
    def name(self) -> AdapterName:
        return "claude-code"

    async def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        client = _build_client(input.api_key, input.api_base_url)
        model_id = input.model_id or DEFAULT_MODEL

        tool_defs = tool_registry.resolve(input.tool_names)
        api_tools = [_to_api_tool(t) for t in tool_defs]

        ctx = ToolContext(
            conversation_id=input.conversation_id,
            workspace_path=input.workspace_path,
            agent_id=input.agent_id,
            run_id=input.run_id,
            cancel_event=cancel_event,
        )

        # Anthropic messages: system goes in the top-level param, history is
        # converted from OpenAI-format dicts, current prompt appended as user.
        messages: list[dict] = _convert_history(input.history or [])
        messages.append({"role": "user", "content": input.prompt})

        # Cross-turn token totals; run.usage flushed to AgentRunner before return.
        run_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "last_input_tokens": 0,
        }

        turn = 0
        while turn < MAX_TURNS:
            if cancel_event.is_set():
                return
            turn += 1

            message_id = new_message_id()
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=input.run_id,
            )

            text_part_index = -1
            next_part_index = 0

            stream_kwargs: dict[str, Any] = {
                "model": model_id,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "system": input.system_prompt,
                "messages": messages,
            }
            if api_tools:
                stream_kwargs["tools"] = api_tools

            try:
                async with client.messages.stream(**stream_kwargs) as stream:
                    async for event in stream:
                        if cancel_event.is_set():
                            return
                        if (
                            event.type == "content_block_delta"
                            and event.delta.type == "text_delta"
                            and event.delta.text
                        ):
                            if text_part_index < 0:
                                text_part_index = next_part_index
                                next_part_index += 1
                                yield PartStartEvent(
                                    conversation_id=input.conversation_id,
                                    timestamp=now_ms(),
                                    message_id=message_id,
                                    part_index=text_part_index,
                                    part={"type": "text", "content": ""},
                                )
                            yield PartDeltaEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                part_index=text_part_index,
                                delta={"type": "text.append", "text": event.delta.text},
                            )
                    final = await stream.get_final_message()
            except Exception:
                yield MessageEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                )
                raise

            if text_part_index >= 0:
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=text_part_index,
                )

            # Accumulate this turn's usage into both per-message and per-run totals.
            usage = final.usage
            inp = getattr(usage, "input_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or 0
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            run_usage["input_tokens"] += inp
            run_usage["output_tokens"] += out
            run_usage["cache_creation_tokens"] += cache_creation
            run_usage["cache_read_tokens"] += cache_read
            run_usage["last_input_tokens"] = inp
            msg_usage = MessageUsage(
                input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read
            )

            tool_use_blocks = [b for b in final.content if getattr(b, "type", None) == "tool_use"]

            # Mirror back the assistant turn so the next round sees its tool calls.
            messages.append({"role": "assistant", "content": _serialize_content(final.content)})

            if final.stop_reason != "tool_use" or not tool_use_blocks:
                if inp > 0 or out > 0:
                    yield MessageUsageEventPayload(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        usage=msg_usage,
                    )
                yield MessageEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                )
                yield RunUsageEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    run_id=input.run_id,
                    usage=RunUsage(model=model_id, **run_usage),
                )
                return

            # Run each requested tool, then feed results back as tool_result blocks.
            tool_results: list[dict] = []
            for block in tool_use_blocks:
                call_id = block.id
                tool_name = block.name
                args = block.input if isinstance(block.input, dict) else {}

                yield ToolCallEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    args=args,
                )

                result = await tool_registry.execute(tool_name, args, ctx)
                value = result.value if result.ok else {"error": result.error}

                yield ToolResultEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    result=value,
                    is_error=not result.ok,
                )

                # Convention: a write_artifact result carrying artifactId means a
                # new artifact row exists; the adapter publishes artifact.create.
                if tool_name == "write_artifact" and result.ok and _has_artifact_id(value):
                    artifact_event = await _load_artifact_event(input.conversation_id, value)
                    if artifact_event is not None:
                        yield artifact_event

                if (
                    tool_name in ("deploy_artifact", "deploy_workspace")
                    and result.ok
                    and _is_deploy_status_record(value)
                ):
                    yield DeployStatusEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        deployment=DeployStatusRecord.model_validate(value),
                    )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": json.dumps(value),
                    }
                )

            messages.append({"role": "user", "content": tool_results})

            if inp > 0 or out > 0:
                yield MessageUsageEventPayload(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    usage=msg_usage,
                )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            # loop into the next turn

        # MAX_TURNS fallback: flush accumulated usage (happy path returned earlier).
        yield RunUsageEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            run_id=input.run_id,
            usage=RunUsage(model=model_id, **run_usage),
        )


# ─── helpers ────────────────────────────────────────────────


def _build_client(api_key: str | None, base_url: str | None) -> AsyncAnthropic:
    # Wrapped so tests can monkeypatch a fake AsyncAnthropic.
    return AsyncAnthropic(api_key=api_key, base_url=base_url)


def _to_api_tool(t: ToolDef) -> dict:
    return {"name": t.name, "description": t.description, "input_schema": t.parameters}


def _serialize_content(content: list) -> list[dict]:
    """Turn final-message content blocks into Anthropic message-param dicts."""
    blocks: list[dict] = []
    for b in content:
        btype = getattr(b, "type", None)
        if btype == "text":
            blocks.append({"type": "text", "text": b.text})
        elif btype == "tool_use":
            blocks.append(
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            )
    return blocks


def _convert_history(history: list[dict]) -> list[dict]:
    """Convert OpenAI-format chat-message dicts to Anthropic role/content.

    user/assistant pass through; OpenAI ``tool`` messages map to a user
    message carrying a single tool_result block. The current trigger prompt is
    appended by the caller, so history excludes it.
    """
    messages: list[dict] = []
    for msg in history:
        role = msg.get("role")
        if role == "system":
            continue  # system lives in the top-level param, not in messages
        if role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": _coerce_text(msg.get("content")),
                        }
                    ],
                }
            )
            continue
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": _coerce_text(msg.get("content"))})
    return messages


def _coerce_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _has_artifact_id(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("artifactId"), str)


def _is_deploy_status_record(value: Any) -> bool:
    created_at = value.get("createdAt") if isinstance(value, dict) else None
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("artifactId"), str)
        and isinstance(value.get("previewPath"), str)
        and value.get("status") in ("ready", "failed")
        # TS guard accepts any number; mirror that (reject bool) for fidelity.
        and isinstance(created_at, (int, float))
        and not isinstance(created_at, bool)
    )


async def _load_artifact_event(
    conversation_id: str, value: dict
) -> ArtifactCreateEvent | None:
    """Load the freshly-written artifact row and wrap it in artifact.create."""
    artifact_id = value["artifactId"]
    async with get_db() as session:
        result = await session.execute(select(Artifact).where(Artifact.id == artifact_id))
        artifact = result.scalar_one_or_none()
        if artifact is None:
            return None
        record = ArtifactRecord(
            id=artifact.id,
            conversation_id=artifact.conversation_id,
            type=artifact.type,
            title=artifact.title,
            content=artifact.content_dict,
            version=artifact.version,
            parent_artifact_id=artifact.parent_artifact_id,
            created_by_agent_id=artifact.created_by_agent_id,
            created_at=artifact.created_at,
        )
    return ArtifactCreateEvent(
        conversation_id=conversation_id,
        timestamp=now_ms(),
        artifact=record,
    )
