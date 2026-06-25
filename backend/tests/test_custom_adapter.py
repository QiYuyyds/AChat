"""Tests for CustomAdapter (phase 4).

Exercises the openai-driven tool loop with a fully faked AsyncOpenAI client
(no network): _build_client is monkeypatched to return a fake client whose
chat.completions.create yields stub ChatCompletionChunk-like objects.
"""

import asyncio
from dataclasses import dataclass, field

import pytest_asyncio

from app.adapters import custom_adapter
from app.adapters.base import AdapterInput, CustomConfig
from app.adapters.custom_adapter import CustomAdapter

# ─── fake SDK chunk stubs ────────────────────────────────────────────────────


@dataclass
class _FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class _FakeToolCallDelta:
    index: int
    id: str | None = None
    function: _FakeFunction | None = None


@dataclass
class _FakeDelta:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[_FakeToolCallDelta] | None = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta
    finish_reason: str | None = None


@dataclass
class _FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class _FakeChunk:
    choices: list[_FakeChoice] = field(default_factory=list)
    usage: _FakeUsage | None = None


class _FakeCompletions:
    """Returns one scripted async stream per create() call (one per turn)."""

    def __init__(self, scripts: list[list[_FakeChunk]]) -> None:
        self._scripts = scripts
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN003 - mirrors the SDK signature
        self.calls.append(kwargs)
        chunks = self._scripts.pop(0)

        async def _gen():
            for chunk in chunks:
                yield chunk

        return _gen()


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, scripts: list[list[_FakeChunk]]) -> None:
        self.chat = _FakeChat(_FakeCompletions(scripts))


def _install_fake_client(monkeypatch, scripts: list[list[_FakeChunk]]) -> _FakeClient:
    client = _FakeClient(scripts)
    monkeypatch.setattr(custom_adapter, "_build_client", lambda *a, **k: client)
    return client


def _input(conversation, **overrides) -> AdapterInput:
    base = {
        "agent_id": conversation["agent_id"],
        "conversation_id": conversation["conversation_id"],
        "run_id": "run_test",
        "prompt": "hello",
        "workspace_path": conversation["workspace_root"],
        "system_prompt": "you are a test agent",
        "api_key": "sk-test",
        "api_base_url": None,
        "model_id": "test-model",
        "tool_names": [],
        "custom_config": CustomConfig(model_provider="openai", supports_vision=False),
    }
    base.update(overrides)
    return AdapterInput(**base)


# ─── conversation fixture (mirrors tests/test_tools.py) ───────────────────────


@pytest_asyncio.fixture
async def conversation(db, agents, tmp_path):
    """Create a conversation + on-disk sandbox workspace; return ids and paths."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="T",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agents["alice"]]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )

    return {
        "conversation_id": conv_id,
        "agent_id": agents["alice"],
        "workspace_root": str(ws_root),
    }


async def _collect(adapter: CustomAdapter, inp: AdapterInput) -> list:
    return [ev async for ev in adapter.stream(inp, asyncio.Event())]


# ─── tests ────────────────────────────────────────────────────────────────────


async def test_no_tool_text_response(conversation, monkeypatch):
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="Hello"))]),
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content=" world"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(
                    choices=[], usage=_FakeUsage(prompt_tokens=10, completion_tokens=5)
                ),
            ]
        ],
    )

    events = await _collect(CustomAdapter(), _input(conversation))
    types = [e.type for e in events]

    assert types == [
        "message.start",
        "part.start",
        "part.delta",
        "part.delta",
        "part.end",
        "message.usage",
        "message.end",
        "run.usage",
    ]

    text_deltas = [e.delta["text"] for e in events if e.type == "part.delta"]
    assert "".join(text_deltas) == "Hello world"

    run_usage = next(e for e in events if e.type == "run.usage")
    assert run_usage.usage.input_tokens == 10
    assert run_usage.usage.output_tokens == 5
    assert run_usage.usage.model == "test-model"


async def test_tool_call_loops(conversation, monkeypatch):
    # Turn 1: model calls report_task_result. Turn 2: model emits final text.
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                tool_calls=[
                                    _FakeToolCallDelta(
                                        index=0,
                                        id="call_1",
                                        function=_FakeFunction(
                                            name="report_task_result",
                                            arguments='{"status":"complete",'
                                            '"summary":"done"}',
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[
                        _FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")
                    ]
                ),
            ],
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="all set"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["report_task_result"])
    )
    types = [e.type for e in events]

    assert "tool.call" in types
    assert "tool.result" in types
    # two turns → two message.start events
    assert types.count("message.start") == 2

    tool_call = next(e for e in events if e.type == "tool.call")
    assert tool_call.tool_name == "report_task_result"
    assert tool_call.call_id == "call_1"

    tool_result = next(e for e in events if e.type == "tool.result")
    assert tool_result.is_error is False
    assert tool_result.result["status"] == "complete"

    # final turn produced the closing text + run.usage
    assert types[-1] == "run.usage"


async def test_write_artifact_emits_artifact_create(conversation, monkeypatch):
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                tool_calls=[
                                    _FakeToolCallDelta(
                                        index=0,
                                        id="call_a",
                                        function=_FakeFunction(
                                            name="write_artifact",
                                            arguments='{"type":"document",'
                                            '"title":"Doc","content":"# Hi"}',
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[
                        _FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")
                    ]
                ),
            ],
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="saved"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["write_artifact"])
    )
    types = [e.type for e in events]

    assert "artifact.create" in types
    artifact_event = next(e for e in events if e.type == "artifact.create")
    assert artifact_event.artifact.type == "document"
    assert artifact_event.artifact.title == "Doc"


async def test_reasoning_content_yields_thinking_part(conversation, monkeypatch):
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(delta=_FakeDelta(reasoning_content="let me think"))
                    ]
                ),
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="answer"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    events = await _collect(CustomAdapter(), _input(conversation))
    part_starts = [e for e in events if e.type == "part.start"]
    assert part_starts[0].part["type"] == "thinking"
    assert part_starts[1].part["type"] == "text"
