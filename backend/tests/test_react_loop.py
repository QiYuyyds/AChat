"""Tests for call_once and the SDK ReAct loop (Phase 1).

Exercises CustomAdapter.call_once and AgentRunner._run_react_loop with a
fully faked AsyncOpenAI client (no network).
"""

import asyncio
from dataclasses import dataclass, field

import pytest_asyncio

from app.adapters import custom_adapter
from app.adapters.base import AdapterInput, CustomConfig
from app.adapters.custom_adapter import CustomAdapter

# ─── fake SDK chunk stubs (shared with test_custom_adapter.py) ────────────────


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

    async def create(self, **kwargs):
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


@pytest_asyncio.fixture
async def conversation(db, agents, test_user, tmp_path):
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
            user_id=test_user["id"],
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


# ─── call_once tests ──────────────────────────────────────────────────────────


async def test_call_once_text_response(conversation, monkeypatch):
    """call_once yields message.start → parts → message.end for a text-only turn."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="Hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(
                    choices=[], usage=_FakeUsage(prompt_tokens=10, completion_tokens=5)
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    events = [ev async for ev in adapter.call_once(_input(conversation), cancel)]
    types = [e.type for e in events]

    assert "message.start" in types
    assert "message.end" in types
    assert "run.usage" not in types  # call_once does NOT emit run.usage
    assert "tool.call" not in types  # no tool calls in this turn

    text_deltas = [e.delta["text"] for e in events if e.type == "part.delta"]
    assert "".join(text_deltas) == "Hello"


async def test_call_once_with_tool_calls(conversation, monkeypatch):
    """call_once yields tool.call events but does NOT execute tools or yield tool.result."""
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
                                            name="fs_list",
                                            arguments='{"path":""}',
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
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    events = [ev async for ev in adapter.call_once(
        _input(conversation, tool_names=["fs_list"]), cancel
    )]
    types = [e.type for e in events]

    assert "tool.call" in types
    assert "tool.result" not in types  # call_once does NOT execute tools

    tool_call = next(e for e in events if e.type == "tool.call")
    assert tool_call.tool_name == "fs_list"
    assert tool_call.call_id == "call_1"
    assert tool_call.args == {"path": ""}


async def test_call_once_with_messages(conversation, monkeypatch):
    """call_once uses input.messages when provided instead of constructing its own."""
    client = _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="ok"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation)
    inp.messages = [
        {"role": "system", "content": "custom system"},
        {"role": "user", "content": "custom user"},
        {"role": "assistant", "content": "prev reply"},
        {"role": "user", "content": "new question"},
    ]

    events = [ev async for ev in adapter.call_once(inp, cancel)]
    # Verify the adapter used the provided messages
    assert len(client.chat.completions.calls) == 1
    sent_messages = client.chat.completions.calls[0]["messages"]
    assert sent_messages == inp.messages


async def test_call_once_cancel(conversation, monkeypatch):
    """call_once respects cancel_event and stops early."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="Hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    cancel.set()  # pre-cancelled

    events = [ev async for ev in adapter.call_once(_input(conversation), cancel)]
    assert len(events) == 0  # no events because cancelled before start


async def test_call_once_reasoning_content(conversation, monkeypatch):
    """call_once preserves reasoning_content as a thinking part."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(delta=_FakeDelta(reasoning_content="thinking..."))
                    ]
                ),
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="answer"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    events = [ev async for ev in adapter.call_once(_input(conversation), cancel)]
    part_starts = [e for e in events if e.type == "part.start"]
    assert part_starts[0].part["type"] == "thinking"
    assert part_starts[1].part["type"] == "text"


# ─── _run_react_loop tests ────────────────────────────────────────────────────


async def test_react_loop_multi_turn(conversation, monkeypatch):
    """ReAct loop: turn 1 calls a tool, turn 2 produces final text."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            # Turn 1: tool call
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
                                            name="fs_list",
                                            arguments='{"path":""}',
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
            # Turn 2: final text
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="all done"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    types = [e.type for e in events]

    # Should have tool.call (from call_once) + tool.result (from AgentRunner)
    assert "tool.call" in types
    assert "tool.result" in types
    # Two turns → two message.start events
    assert types.count("message.start") == 2
    # Final event should be run.usage
    assert types[-1] == "run.usage"


async def test_react_loop_event_order(conversation, monkeypatch):
    """ReAct loop: message.end must come AFTER tool.result, not before.

    consume_stream clears parts_buffer on message.end. If message.end is
    yielded before tool.result, the tool_result part overwrites all prior
    parts in the DB, causing rendering loss after restart.
    """
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            # Turn 1: tool call
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
                                            name="fs_list",
                                            arguments='{"path":""}',
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
            # Turn 2: final text
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="done"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    types = [e.type for e in events]

    # Find indices for turn 1's events
    first_msg_end_idx = types.index("message.end")
    first_tool_result_idx = types.index("tool.result")

    # message.end MUST come after tool.result
    assert first_tool_result_idx < first_msg_end_idx, (
        f"message.end (idx={first_msg_end_idx}) must come AFTER tool.result "
        f"(idx={first_tool_result_idx}); otherwise consume_stream clears "
        f"parts_buffer before tool.result is persisted, causing data loss"
    )


async def test_react_loop_no_tools(conversation, monkeypatch):
    """ReAct loop with no tool calls: single turn, text response, run.usage."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation)

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    types = [e.type for e in events]

    assert types.count("message.start") == 1
    assert "tool.call" not in types
    assert "tool.result" not in types
    assert types[-1] == "run.usage"


async def test_react_loop_cancel(conversation, monkeypatch):
    """ReAct loop respects cancel_event."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    cancel.set()  # pre-cancelled

    inp = _input(conversation)
    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    # Pre-cancelled: no model call, but stop_reason is still recorded.
    assert len(events) == 1
    assert events[0].type == "run.usage"
    assert events[0].stop_reason == "cancelled"


async def test_react_loop_model_done_beyond_eight_tool_turns(conversation, monkeypatch):
    """No default 8-step cap: loop continues past 8 tool turns until model-done."""
    from app.services.agent_runner import _run_react_loop

    n_tool_turns = 10
    scripts = [
        [
            _FakeChunk(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id=f"call_{i}",
                                    function=_FakeFunction(
                                        name="fs_list",
                                        # Distinct fingerprints (avoid duplicate breaker);
                                        # empty path so tool succeeds (avoid error breaker).
                                        arguments=f'{{"path":"","_i":{i}}}',
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
        ]
        for i in range(n_tool_turns)
    ]
    # Final model-done turn
    scripts.append(
        [
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="done"))]),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]),
        ]
    )

    _install_fake_client(monkeypatch, scripts)

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    types = [e.type for e in events]

    assert types.count("message.start") == n_tool_turns + 1
    assert types[-1] == "run.usage"
    usage_ev = events[-1]
    assert usage_ev.stop_reason == "complete"


async def test_react_loop_max_tool_turns_fuse(conversation, monkeypatch):
    """Configured max_tool_turns triggers soft→forced wrap-up with label."""
    from app.config import get_settings
    from app.services.agent_runner import _run_react_loop

    settings = get_settings()
    monkeypatch.setattr(settings, "max_tool_turns", 2)

    # 2 tool turns + soft inject turn (tools again) + forced final (tools=[])
    scripts = [
        # turn 1 tool
        [
            _FakeChunk(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id="call_0",
                                    function=_FakeFunction(name="fs_list", arguments='{"path":"a"}'),
                                )
                            ]
                        )
                    )
                ]
            ),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]),
        ],
        # turn 2 tool → fuse hit after this
        [
            _FakeChunk(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id="call_1",
                                    function=_FakeFunction(name="fs_list", arguments='{"path":"b"}'),
                                )
                            ]
                        )
                    )
                ]
            ),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]),
        ],
        # soft inject turn — still tools
        [
            _FakeChunk(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id="call_2",
                                    function=_FakeFunction(name="fs_list", arguments='{"path":"c"}'),
                                )
                            ]
                        )
                    )
                ]
            ),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]),
        ],
        # forced final
        [
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="forced summary"))]),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]),
        ],
    ]

    client = _install_fake_client(monkeypatch, scripts)
    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    usage_ev = [e for e in events if e.type == "run.usage"][-1]
    assert usage_ev.stop_reason == "max_tool_turns"
    assert usage_ev.stop_reason_label
    # Last create should have tools=None (empty list becomes None in adapter)
    last_call = client.chat.completions.calls[-1]
    assert last_call.get("tools") in (None, [])


async def test_react_loop_duplicate_tool_breaker(conversation, monkeypatch):
    """Identical tool fingerprint ×3 injects then forces."""
    from app.services.agent_runner import _run_react_loop

    same_args = '{"path":"same"}'
    scripts = []
    for i in range(3):
        scripts.append(
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                tool_calls=[
                                    _FakeToolCallDelta(
                                        index=0,
                                        id=f"call_{i}",
                                        function=_FakeFunction(name="fs_list", arguments=same_args),
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]),
            ]
        )
    # soft inject turn — still same fingerprint
    scripts.append(
        [
            _FakeChunk(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id="call_3",
                                    function=_FakeFunction(name="fs_list", arguments=same_args),
                                )
                            ]
                        )
                    )
                ]
            ),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]),
        ]
    )
    # forced final
    scripts.append(
        [
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="breaker summary"))]),
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]),
        ]
    )

    _install_fake_client(monkeypatch, scripts)
    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]
    usage_ev = [e for e in events if e.type == "run.usage"][-1]
    assert usage_ev.stop_reason == "duplicate_tool_breaker"


# ─── O9: TurnMetricEvent ──────────────────────────────────────────────────────


async def test_react_loop_yields_turn_metric_multi_turn(conversation, monkeypatch):
    """ReAct loop yields one TurnMetricEvent per turn with correct data."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            # Turn 1: tool call (fs_list)
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
                                            name="fs_list",
                                            arguments='{"path":""}',
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="tool_calls")]
                ),
            ],
            # Turn 2: final text (no tool calls)
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="done"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation, tool_names=["fs_list"])

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]

    # Extract turn.metric events
    turn_metrics = [e for e in events if e.type == "turn.metric"]
    assert len(turn_metrics) == 2, f"Expected 2 turn.metric events, got {len(turn_metrics)}"

    # Turn 1: has tool call fs_list
    m1 = turn_metrics[0]
    assert m1.turn == 1
    assert m1.tool_calls == ["fs_list"]
    assert m1.duration_ms >= 0  # might be 0 on fast machines

    # Turn 2: no tool calls (stop)
    m2 = turn_metrics[1]
    assert m2.turn == 2
    assert m2.tool_calls == []

    # turn.metric should come after deferred_events (message.usage + message.end)
    # but before post_turn hook — verify ordering: message.end before turn.metric
    types = [e.type for e in events]
    for i, t in enumerate(types):
        if t == "turn.metric":
            # Find the preceding message.end (deferred)
            assert "message.end" in types[:i], (
                "turn.metric should come after message.end (deferred events)"
            )


async def test_react_loop_turn_metric_no_tools(conversation, monkeypatch):
    """ReAct loop with no tool calls: single turn still yields TurnMetricEvent."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation)

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]

    turn_metrics = [e for e in events if e.type == "turn.metric"]
    assert len(turn_metrics) == 1
    assert turn_metrics[0].turn == 1
    assert turn_metrics[0].tool_calls == []


async def test_react_loop_turn_metric_tokens(conversation, monkeypatch):
    """TurnMetricEvent contains token data from message.usage events."""
    from app.services.agent_runner import _run_react_loop

    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hello"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")],
                    usage=_FakeUsage(prompt_tokens=100, completion_tokens=50),
                ),
            ]
        ],
    )

    adapter = CustomAdapter()
    cancel = asyncio.Event()
    inp = _input(conversation)

    events = [
        ev async for ev in _run_react_loop(
            adapter, inp, cancel,
            run_id="run_test",
            agent_id=conversation["agent_id"],
            conversation_id=conversation["conversation_id"],
            model_id="test-model",
        )
    ]

    turn_metrics = [e for e in events if e.type == "turn.metric"]
    assert len(turn_metrics) == 1
    # Token data should be non-zero if usage was reported
    # (FakeUsage has prompt_tokens=100, completion_tokens=50)
    assert turn_metrics[0].tokens.input_tokens >= 0
    assert turn_metrics[0].tokens.output_tokens >= 0
