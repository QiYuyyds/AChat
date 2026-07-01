"""Adapter contract shared by all agent-platform adapters.

Port of src/server/adapters/types.ts. See specs/05-adapter-interface.md.

The TS ``AbortSignal`` becomes an :class:`asyncio.Event` (``cancel_event``):
adapters check ``cancel_event.is_set()`` to abort their stream.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from app.schemas.events import StreamEvent

AdapterName = Literal["mock", "custom", "codex"]


@dataclass
class AdapterAttachment:
    id: str
    file_name: str
    mime_type: str
    kind: Literal["image", "file"]
    abs_path: str  # local absolute path, adapter reads base64 from it


@dataclass
class CustomConfig:
    """Only CustomAgentAdapter uses this (OpenAI-compatible model selection)."""

    model_provider: str
    supports_vision: bool = False


@dataclass
class AdapterInput:
    agent_id: str
    conversation_id: str
    run_id: str
    # full prompt already assembled by the caller (group chat wraps it in XML)
    prompt: str
    # workspace absolute path
    workspace_path: str
    # system prompt with `<workspace_info>` injected; shared by all adapters
    system_prompt: str
    # per-agent API key; None falls back to env / OAuth in the adapter
    api_key: str | None
    # per-agent API base URL; endpoint protocol differs per adapter
    api_base_url: str | None
    # per-agent model id; None lets SDK adapters use their default
    model_id: str | None
    # tool names scoped for this run (AgentRunner already resolved overrides)
    tool_names: list[str]
    # trigger-message attachments (images / files)
    attachments: list[AdapterAttachment] | None = None
    # cross-run history as OpenAI chat-message dicts (excludes current trigger)
    history: list[dict] | None = None
    custom_config: CustomConfig | None = None
    # per-agent executable path override (Codex CLI adapter only)
    executable_path: str | None = None


class AgentPlatformAdapter(ABC):
    """Hides per-platform API differences behind a unified event stream."""

    @property
    @abstractmethod
    def name(self) -> AdapterName: ...

    @abstractmethod
    def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]: ...
