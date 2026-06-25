"""CustomAdapter — adapter for self-configured agents.

Port of src/server/adapters/custom-agent-adapter.ts. See specs/05-adapter-interface.md.

Drives the tool loop itself via the openai SDK (OpenAI Chat Completions compatible):
stream model output → parse tool_calls → run ToolExecutor → feed results back into
messages → continue, until the model stops calling tools.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from sqlalchemy import select

from app.adapters.base import (
    AdapterAttachment,
    AdapterInput,
    AdapterName,
    AgentPlatformAdapter,
)
from app.adapters.custom_provider_client import resolve_custom_provider_client_config
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

logger = logging.getLogger(__name__)

# OpenAI SDK default maxRetries=2; declare it so spec 05's "retry network/rate-limit"
# promise is visible and tunable in one place (retries only apply to the initial
# connection — once chunks start streaming there is no retry).
MAX_API_RETRIES = 2

MAX_TURNS = 8

# guard against one user message carrying too many images (token blowup + provider caps)
MAX_IMAGES_PER_MESSAGE = 5


@dataclass
class _AccumulatingToolCall:
    id: str = ""
    name: str = ""
    args_buffer: str = ""


@dataclass
class _RunUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    last_input_tokens: int = 0


@dataclass
class _MsgUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class _ToolCallBuffer:
    """Ordered map from delta tool_call index to its accumulating state."""

    entries: dict[int, _AccumulatingToolCall] = field(default_factory=dict)

    def get_or_create(self, idx: int) -> _AccumulatingToolCall:
        entry = self.entries.get(idx)
        if entry is None:
            entry = _AccumulatingToolCall()
            self.entries[idx] = entry
        return entry


class CustomAdapter(AgentPlatformAdapter):
    @property
    def name(self) -> AdapterName:
        return "custom"

    async def stream(  # noqa: C901 - faithful port of the TS tool loop
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        if not input.custom_config:
            raise ValueError("CustomAdapter requires custom_config")
        if not input.model_id:
            raise ValueError("CustomAdapter requires model_id")

        model_provider = input.custom_config.model_provider
        supports_vision = input.custom_config.supports_vision
        model_id = input.model_id

        client = _build_client(model_provider, input.api_key, input.api_base_url)

        tool_defs = tool_registry.resolve(input.tool_names)
        api_tools = [_to_api_tool(t) for t in tool_defs]

        ctx = ToolContext(
            conversation_id=input.conversation_id,
            workspace_path=input.workspace_path,
            agent_id=input.agent_id,
            run_id=input.run_id,
            cancel_event=cancel_event,
        )

        # user message content: if the agent declares vision + actually has images, go multimodal
        image_attachments = [
            a for a in (input.attachments or []) if a.kind == "image"
        ][:MAX_IMAGES_PER_MESSAGE]
        use_multimodal = bool(supports_vision) and len(image_attachments) > 0

        user_content: object = (
            _build_multimodal_user_content(input.prompt, image_attachments)
            if use_multimodal
            else input.prompt
        )

        messages: list[dict] = [
            {"role": "system", "content": input.system_prompt},
            # cross-run history: spec 13 serialized context. Empty list behaves like before.
            *(input.history or []),
            {"role": "user", "content": user_content},
        ]

        turn = 0
        # token usage accumulated across turns; yield run.usage before run end for persistence
        run_usage = _RunUsage()

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
            text_buffer = ""
            thinking_part_index = -1
            reasoning_buffer = ""
            next_part_index = 0
            tool_call_buffer = _ToolCallBuffer()

            try:
                stream = await client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    tools=api_tools if len(api_tools) > 0 else None,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            except Exception:
                yield MessageEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                )
                raise

            finish_reason: str | None = None
            # per-message usage for this turn; maintained alongside run_usage
            msg_usage = _MsgUsage()

            async for chunk in stream:
                if cancel_event.is_set():
                    return
                # Final usage chunk (stream_options.include_usage): choices usually empty.
                # DeepSeek also carries prompt_cache_hit_tokens / prompt_cache_miss_tokens.
                usage = getattr(chunk, "usage", None)
                if usage:
                    inp = _usage_field(usage, "prompt_tokens")
                    out = _usage_field(usage, "completion_tokens")
                    cached = _usage_field(usage, "prompt_cache_hit_tokens") or _usage_field(
                        usage, "cached_tokens"
                    )
                    msg_usage.input_tokens += inp
                    msg_usage.output_tokens += out
                    msg_usage.cache_read_tokens += cached
                    run_usage.input_tokens += inp
                    run_usage.output_tokens += out
                    run_usage.cache_read_tokens += cached
                    run_usage.last_input_tokens = inp
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.delta

                # DeepSeek V4/R1 thinking-mode models add reasoning_content on delta
                # (not in the official OpenAI type) — yield it as a thinking part.
                reasoning = getattr(delta, "reasoning_content", None)
                if isinstance(reasoning, str) and len(reasoning) > 0:
                    if thinking_part_index < 0:
                        thinking_part_index = next_part_index
                        next_part_index += 1
                        yield PartStartEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            part_index=thinking_part_index,
                            part={"type": "thinking", "content": ""},
                        )
                    reasoning_buffer += reasoning
                    yield PartDeltaEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=thinking_part_index,
                        delta={"type": "thinking.append", "text": reasoning},
                    )

                content = getattr(delta, "content", None)
                if isinstance(content, str) and len(content) > 0:
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
                    text_buffer += content
                    yield PartDeltaEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=text_part_index,
                        delta={"type": "text.append", "text": content},
                    )

                for tcd in getattr(delta, "tool_calls", None) or []:
                    entry = tool_call_buffer.get_or_create(tcd.index)
                    if tcd.id:
                        entry.id = tcd.id
                    if tcd.function and tcd.function.name:
                        entry.name = tcd.function.name
                    if tcd.function and tcd.function.arguments:
                        entry.args_buffer += tcd.function.arguments

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

            if thinking_part_index >= 0:
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=thinking_part_index,
                )
            if text_part_index >= 0:
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=text_part_index,
                )

            tool_calls = [tc for tc in tool_call_buffer.entries.values() if tc.id and tc.name]

            # write back assistant message. DeepSeek thinking-mode models require
            # reasoning_content to be echoed back next turn, else the API errors with
            # "The reasoning_content in the thinking mode must be passed back".
            assistant_msg: dict = {
                "role": "assistant",
                "content": text_buffer or None,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.args_buffer or "{}"},
                    }
                    for tc in tool_calls
                ]
            if reasoning_buffer:
                assistant_msg["reasoning_content"] = reasoning_buffer
            messages.append(assistant_msg)

            if len(tool_calls) == 0 or finish_reason == "stop":
                if msg_usage.input_tokens > 0 or msg_usage.output_tokens > 0:
                    yield MessageUsageEventPayload(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        usage=_to_message_usage(msg_usage),
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
                    usage=_to_run_usage(run_usage, model_id),
                )
                return

            # execute tools
            for tc in tool_calls:
                try:
                    args = json.loads(tc.args_buffer) if tc.args_buffer else {}
                except (ValueError, TypeError):
                    args = {}

                yield ToolCallEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=tc.id,
                    tool_name=tc.name,
                    args=args,
                )

                result = await tool_registry.execute(tc.name, args, ctx)
                value = result.value if result.ok else {"error": result.error}

                yield ToolResultEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=tc.id,
                    result=value,
                    is_error=not result.ok,
                )

                # a tool result carrying artifactId is treated as "created an artifact" —
                # the adapter publishes artifact.create so AgentRunner injects an artifact_ref.
                if tc.name == "write_artifact" and result.ok and _has_artifact_id(value):
                    artifact_event = await _load_artifact_event(
                        input.conversation_id, value["artifactId"]
                    )
                    if artifact_event is not None:
                        yield artifact_event

                if (
                    tc.name in ("deploy_artifact", "deploy_workspace")
                    and result.ok
                    and _is_deploy_status_record(value)
                ):
                    yield DeployStatusEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        deployment=DeployStatusRecord.model_validate(value),
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(value),
                    }
                )

            if msg_usage.input_tokens > 0 or msg_usage.output_tokens > 0:
                yield MessageUsageEventPayload(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    usage=_to_message_usage(msg_usage),
                )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            # continue to next turn

        # MAX_TURNS fallback: emit accumulated usage (normal path already emitted + returned)
        yield RunUsageEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            run_id=input.run_id,
            usage=_to_run_usage(run_usage, model_id),
        )


# ─── helpers ──────────────────────────────────────────────


def _build_client(
    provider: str, override_key: str | None, api_base_url: str | None
) -> AsyncOpenAI:
    config = resolve_custom_provider_client_config(provider, override_key, api_base_url)
    return AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=MAX_API_RETRIES,
    )


def _to_api_tool(t: ToolDef) -> dict:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }


def _usage_field(usage: object, name: str) -> int:
    # TS used `usage.x ?? 0` (accepts any number); mirror that, rejecting bool.
    value = getattr(usage, name, None)
    if isinstance(value, bool):
        return 0
    return int(value) if isinstance(value, (int, float)) else 0


def _to_message_usage(u: _MsgUsage) -> MessageUsage:
    return MessageUsage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
    )


def _to_run_usage(u: _RunUsage, model_id: str) -> RunUsage:
    return RunUsage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_creation_tokens=u.cache_creation_tokens,
        cache_read_tokens=u.cache_read_tokens,
        last_input_tokens=u.last_input_tokens,
        model=model_id,
    )


def _has_artifact_id(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("artifactId"), str)


def _is_deploy_status_record(value: object) -> bool:
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
    conversation_id: str, artifact_id: str
) -> ArtifactCreateEvent | None:
    """Load the freshly-written artifact row and wrap it in artifact.create."""
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


def _build_multimodal_user_content(
    prompt: str, images: list[AdapterAttachment]
) -> list[dict]:
    """Build OpenAI-style multimodal user content blocks.

    DeepSeek follows the OpenAI schema; Anthropic's image block shape differs, but
    CustomAdapter only targets openai SDK + OpenAI-compatible endpoints for now.
    """
    blocks: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        try:
            with open(img.abs_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.mime_type};base64,{data}"},
                }
            )
        except OSError:
            logger.warning("[CustomAdapter] failed to read image %s", img.abs_path)
    return blocks
