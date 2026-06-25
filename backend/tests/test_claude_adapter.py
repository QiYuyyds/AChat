"""ClaudeAdapter tests — fake AsyncAnthropic, no real network/LLM.

Monkeypatches ``claude_adapter._build_client`` to return a fake client whose
``messages.stream(...)`` yields scripted raw stream events and a final message.
Asserts the emitted StreamEvent sequence for a plain text answer and for a
single tool_use round-trip.
"""

import asyncio
from types import SimpleNamespace

import pytest_asyncio

from app.adapters import claude_adapter
from app.adapters.base import AdapterInput
from tests.test_tools import conversation as _conversation_fixture

# Re-expose the shared conversation+workspace fixture under its own name so
# pytest resolves it by argument while ruff doesn't flag a redefinition.
conversation = pytest_asyncio.fixture(_conversation_fixture.__wrapped__)


# ─── fakes ───────────────────────────────────────────────────────────────────
def _text_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _usage(input_tokens=10, output_tokens=5, cache_creation=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(call_id: str, name: str, inp: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=inp)


class _FakeStream:
    """Async context manager + async iterator over scripted raw events."""

    def __init__(self, events, final_message):
        self._events = events
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for ev in self._events:
                yield ev

        return gen()

    async def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, turns):
        self._turns = list(turns)
        self._i = 0

    def stream(self, **kwargs):  # noqa: ARG002 - signature must match SDK call
        turn = self._turns[self._i]
        self._i += 1
        return _FakeStream(turn["events"], turn["final"])


class _FakeClient:
    def __init__(self, turns):
        self.messages = _FakeMessages(turns)


def _input(conversation, prompt="hello", tool_names=None) -> AdapterInput:
    return AdapterInput(
        agent_id=conversation["agent_id"],
        conversation_id=conversation["conversation_id"],
        run_id="run_test",
        prompt=prompt,
        workspace_path=conversation["workspace_root"],
        system_prompt="sys",
        api_key="k",
        api_base_url=None,
        model_id="claude-test",
        tool_names=tool_names or [],
    )


async def _collect(adapter, inp):
    events = []
    async for ev in adapter.stream(inp, asyncio.Event()):
        events.append(ev)
    return events


# ─── tests ───────────────────────────────────────────────────────────────────
async def test_simple_text_answer(conversation, monkeypatch):
    final = SimpleNamespace(
        content=[_text_block("Hi there")],
        stop_reason="end_turn",
        usage=_usage(input_tokens=12, output_tokens=7),
    )
    turns = [{"events": [_text_delta("Hi "), _text_delta("there")], "final": final}]
    monkeypatch.setattr(claude_adapter, "_build_client", lambda *a, **k: _FakeClient(turns))

    events = await _collect(claude_adapter.ClaudeAdapter(), _input(conversation))
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

    deltas = [e.delta["text"] for e in events if e.type == "part.delta"]
    assert deltas == ["Hi ", "there"]

    run_usage = events[-1]
    assert run_usage.usage.input_tokens == 12
    assert run_usage.usage.output_tokens == 7
    assert run_usage.usage.last_input_tokens == 12
    assert run_usage.usage.model == "claude-test"

    msg_usage = next(e for e in events if e.type == "message.usage")
    assert msg_usage.usage.input_tokens == 12
    assert msg_usage.usage.output_tokens == 7


async def test_tool_use_round_trip(conversation, monkeypatch):
    # Turn 1: model asks to call read_artifact. Turn 2: model answers in text.
    turn1_final = SimpleNamespace(
        content=[_tool_use_block("call_1", "read_artifact", {"artifactId": "art_x"})],
        stop_reason="tool_use",
        usage=_usage(input_tokens=20, output_tokens=4),
    )
    turn2_final = SimpleNamespace(
        content=[_text_block("done")],
        stop_reason="end_turn",
        usage=_usage(input_tokens=30, output_tokens=6),
    )
    turns = [
        {"events": [], "final": turn1_final},
        {"events": [_text_delta("done")], "final": turn2_final},
    ]
    monkeypatch.setattr(claude_adapter, "_build_client", lambda *a, **k: _FakeClient(turns))

    inp = _input(conversation, prompt="read it", tool_names=["read_artifact"])
    events = await _collect(claude_adapter.ClaudeAdapter(), inp)
    types = [e.type for e in events]

    # First turn: start → tool.call → tool.result → message.usage → message.end.
    assert types[:5] == [
        "message.start",
        "tool.call",
        "tool.result",
        "message.usage",
        "message.end",
    ]
    # Final turn ends with the run.usage flush.
    assert types[-1] == "run.usage"
    assert "run.usage" not in types[:-1]  # only emitted once, at the very end

    call = next(e for e in events if e.type == "tool.call")
    assert call.tool_name == "read_artifact"
    assert call.args == {"artifactId": "art_x"}

    result = next(e for e in events if e.type == "tool.result")
    # read_artifact for a missing id fails → surfaced as is_error with error value.
    assert result.is_error is True

    # run.usage accumulates across both turns.
    run_usage = events[-1]
    assert run_usage.usage.input_tokens == 50
    assert run_usage.usage.output_tokens == 10
    assert run_usage.usage.last_input_tokens == 30


async def test_artifact_create_emitted(conversation, monkeypatch):
    # Turn 1: model calls write_artifact (real tool writes the row). Turn 2: text.
    turn1_final = SimpleNamespace(
        content=[
            _tool_use_block(
                "call_w",
                "write_artifact",
                {"type": "document", "title": "Doc", "content": "hi"},
            )
        ],
        stop_reason="tool_use",
        usage=_usage(input_tokens=15, output_tokens=3),
    )
    turn2_final = SimpleNamespace(
        content=[_text_block("created")],
        stop_reason="end_turn",
        usage=_usage(input_tokens=18, output_tokens=2),
    )
    turns = [
        {"events": [], "final": turn1_final},
        {"events": [_text_delta("created")], "final": turn2_final},
    ]
    monkeypatch.setattr(claude_adapter, "_build_client", lambda *a, **k: _FakeClient(turns))

    inp = _input(conversation, prompt="make a doc", tool_names=["write_artifact"])
    events = await _collect(claude_adapter.ClaudeAdapter(), inp)
    types = [e.type for e in events]

    assert "tool.result" in types
    assert "artifact.create" in types  # exercises _load_artifact_event branch
    artifact_event = next(e for e in events if e.type == "artifact.create")
    assert artifact_event.artifact.type == "document"
    assert artifact_event.artifact.title == "Doc"

    result = next(e for e in events if e.type == "tool.result")
    assert result.is_error is False


async def test_cancel_before_start(conversation, monkeypatch):
    final = SimpleNamespace(
        content=[_text_block("x")], stop_reason="end_turn", usage=_usage()
    )
    turns = [{"events": [_text_delta("x")], "final": final}]
    monkeypatch.setattr(claude_adapter, "_build_client", lambda *a, **k: _FakeClient(turns))

    cancel = asyncio.Event()
    cancel.set()
    events = []
    async for ev in claude_adapter.ClaudeAdapter().stream(_input(conversation), cancel):
        events.append(ev)
    assert events == []
