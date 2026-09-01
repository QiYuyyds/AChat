"""Tests for CustomAdapter (phase 4).

Exercises the openai-driven tool loop with a fully faked AsyncOpenAI client
(no network): _build_client is monkeypatched to return a fake client whose
chat.completions.create yields stub ChatCompletionChunk-like objects.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest_asyncio
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

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
    # Turn 1: model calls fs_list. Turn 2: model emits final text.
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
        CustomAdapter(), _input(conversation, tool_names=["fs_list"])
    )
    types = [e.type for e in events]

    assert "tool.call" in types
    assert "tool.result" in types
    # two turns → two message.start events
    assert types.count("message.start") == 2

    tool_call = next(e for e in events if e.type == "tool.call")
    assert tool_call.tool_name == "fs_list"
    assert tool_call.call_id == "call_1"

    tool_result = next(e for e in events if e.type == "tool.result")
    assert tool_result.is_error is False

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


async def test_truncated_tool_args_emits_error(conversation, monkeypatch):
    """When finish_reason=length and args JSON is broken, emit truncation error."""
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
                                        id="call_trunc",
                                        function=_FakeFunction(
                                            name="write_artifact",
                                            arguments='{"type":"web_app","title":"Big","content":{"files":{"index.html":"<html',  # truncated
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[
                        _FakeChoice(delta=_FakeDelta(), finish_reason="length")
                    ]
                ),
            ],
            # Turn 2: model sees truncation error and stops
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="ok will retry"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["write_artifact"])
    )

    tool_results = [e for e in events if e.type == "tool.result"]
    assert len(tool_results) >= 1
    truncation_result = tool_results[0]
    assert truncation_result.is_error is True
    assert "truncated" in truncation_result.result.get("error", "").lower()
    assert "fs_edit" in truncation_result.result.get("error", "")


# ─── _try_extract_json unit tests ────────────────────────────────────────────


def test_try_extract_json_direct():
    from app.adapters.custom_adapter import _try_extract_json

    assert _try_extract_json('{"a": 1}') == '{"a": 1}'
    assert _try_extract_json('  {"a": 1}  ') == '{"a": 1}'


def test_try_extract_json_markdown_code_block():
    from app.adapters.custom_adapter import _try_extract_json

    text = 'Here is the tool call:\n```json\n{"type": "web_app", "title": "Test"}\n```\n'
    result = _try_extract_json(text)
    assert result is not None
    assert '"web_app"' in result


def test_try_extract_json_bare_code_block():
    from app.adapters.custom_adapter import _try_extract_json

    text = '```\n{"type": "document", "content": "# Hi"}\n```'
    result = _try_extract_json(text)
    assert result is not None
    assert '"document"' in result


def test_try_extract_json_embedded_in_text():
    from app.adapters.custom_adapter import _try_extract_json

    text = 'I will create a web app now.\n{"type": "web_app", "title": "Test", "content": {"files": {"index.html": "<html></html>"}}}\nDone!'
    result = _try_extract_json(text)
    assert result is not None
    assert '"web_app"' in result


def test_try_extract_json_no_json():
    from app.adapters.custom_adapter import _try_extract_json

    assert _try_extract_json("") is None
    assert _try_extract_json("no json here") is None
    assert _try_extract_json("just some { broken text") is None


def test_try_extract_json_nested_objects():
    from app.adapters.custom_adapter import _try_extract_json

    text = 'prefix {"type": "web_app", "content": {"files": {"index.html": "<html>{not json}</html>"}}} suffix'
    result = _try_extract_json(text)
    assert result is not None
    assert '"web_app"' in result


# ─── _recover_tool_args unit tests ───────────────────────────────────────────


def test_recover_tool_args_from_text_buffer():
    from app.adapters.custom_adapter import _recover_tool_args

    text = '```json\n{"type": "web_app", "title": "Test"}\n```'
    result = _recover_tool_args(text, "")
    assert result is not None
    assert '"web_app"' in result


def test_recover_tool_args_from_reasoning_buffer():
    from app.adapters.custom_adapter import _recover_tool_args

    reasoning = 'I need to call write_artifact with {"type": "document", "title": "Doc", "content": "# Hi"}'
    result = _recover_tool_args("", reasoning)
    assert result is not None
    assert '"document"' in result


def test_recover_tool_args_nothing():
    from app.adapters.custom_adapter import _recover_tool_args

    assert _recover_tool_args("", "") is None
    assert _recover_tool_args("no json", "also no json") is None


# ─── integration: empty args recovery via stream ─────────────────────────────


async def test_empty_args_recovered_from_text_buffer(conversation, monkeypatch):
    """Model emits tool call with id+name but args as text content (markdown block)."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                content='```json\n{"type":"document","title":"Doc","content":"# Hi"}\n```'
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                tool_calls=[
                                    _FakeToolCallDelta(
                                        index=0,
                                        id="call_recover",
                                        function=_FakeFunction(name="write_artifact"),
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
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="done"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["write_artifact"])
    )

    tool_call = next(e for e in events if e.type == "tool.call")
    assert tool_call.tool_name == "write_artifact"
    assert tool_call.args.get("type") == "document"
    assert tool_call.args.get("title") == "Doc"

    tool_result = next(e for e in events if e.type == "tool.result")
    assert tool_result.is_error is False


async def test_empty_args_recovered_from_reasoning(conversation, monkeypatch):
    """Model emits tool call with id+name but args in reasoning_content."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                reasoning_content='I should call write_artifact with {"type":"document","title":"R","content":"# R"}'
                            )
                        )
                    ]
                ),
                _FakeChunk(
                    choices=[
                        _FakeChoice(
                            delta=_FakeDelta(
                                tool_calls=[
                                    _FakeToolCallDelta(
                                        index=0,
                                        id="call_reason",
                                        function=_FakeFunction(name="write_artifact"),
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
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="done"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["write_artifact"])
    )

    tool_call = next(e for e in events if e.type == "tool.call")
    assert tool_call.tool_name == "write_artifact"
    assert tool_call.args.get("type") == "document"
    assert tool_call.args.get("title") == "R"


async def test_empty_args_no_recovery_emits_error(conversation, monkeypatch):
    """Model emits tool call with id+name but no args anywhere — should get empty args error."""
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
                                        id="call_empty",
                                        function=_FakeFunction(name="write_artifact"),
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
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="retry"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
            ],
        ],
    )

    events = await _collect(
        CustomAdapter(), _input(conversation, tool_names=["write_artifact"])
    )

    tool_results = [e for e in events if e.type == "tool.result"]
    assert len(tool_results) >= 1
    error_result = tool_results[0]
    assert error_result.is_error is True
    assert "empty" in error_result.result.get("error", "").lower()




# ─── LLM HTTP client cache (speed-up-first-token-latency task 4) ─────────────


def test_build_client_reuses_cached_instance(monkeypatch):
    """Same (provider, base_url, api_key) twice → the same cached AsyncOpenAI instance."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-cache-test")
    custom_adapter._client_cache.clear()
    try:
        c1 = custom_adapter._build_client("deepseek", None, None)
        c2 = custom_adapter._build_client("deepseek", None, None)
        assert c1 is c2
    finally:
        custom_adapter._client_cache.clear()


def test_build_client_new_instance_on_api_key_change(monkeypatch):
    """A changed api_key yields a distinct client instance (new cache key)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-unused-env")
    custom_adapter._client_cache.clear()
    try:
        c1 = custom_adapter._build_client("deepseek", "sk-a", None)
        c2 = custom_adapter._build_client("deepseek", "sk-b", None)
        assert c1 is not c2
        # deepseek resolves base_url to the provider default — cache holds both keys
        assert set(custom_adapter._client_cache.values()) == {c1, c2}
    finally:
        custom_adapter._client_cache.clear()


def test_build_client_new_instance_on_base_url_change(monkeypatch):
    """A changed base_url (openai-compatible) yields a distinct client instance."""
    custom_adapter._client_cache.clear()
    try:
        c1 = custom_adapter._build_client("openai-compatible", "sk-x", "https://a.example.com/v1")
        c2 = custom_adapter._build_client("openai-compatible", "sk-x", "https://b.example.com/v1")
        assert c1 is not c2
    finally:
        custom_adapter._client_cache.clear()


def test_build_client_keyed_on_resolved_config(monkeypatch):
    """Env-resolved and explicitly-passed keys resolving to the same value share one client."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-shared")
    custom_adapter._client_cache.clear()
    try:
        from_env = custom_adapter._build_client("deepseek", None, None)
        explicit = custom_adapter._build_client("deepseek", "sk-shared", None)
        assert from_env is explicit
    finally:
        custom_adapter._client_cache.clear()


async def test_close_cached_clients_closes_and_clears():
    """close_cached_clients closes every cached client and empties the cache."""
    closed: list[str] = []

    class _FakeClient:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            closed.append(self.name)

    custom_adapter._client_cache[("deepseek", "k1", None)] = _FakeClient("deepseek")
    custom_adapter._client_cache[("openai", "k2", None)] = _FakeClient("openai")

    await custom_adapter.close_cached_clients()

    assert sorted(closed) == ["deepseek", "openai"]
    assert custom_adapter._client_cache == {}


async def test_close_cached_clients_tolerates_close_errors():
    """A failing client.close() does not abort the shutdown sweep."""
    class _BadClient:
        async def close(self) -> None:
            raise RuntimeError("boom")

    custom_adapter._client_cache[("openai", "bad", None)] = _BadClient()

    await custom_adapter.close_cached_clients()  # must not raise

    assert custom_adapter._client_cache == {}


# ─── unified cache-usage resolution (fix-openai-compat-cache-usage) ──────────


def _sdk_usage(**overrides) -> CompletionUsage:
    """Real SDK usage object; extra='allow' keeps vendor extras (LongCat et al.)."""
    fields: dict = dict(prompt_tokens=1696, completion_tokens=64, total_tokens=1760)
    fields.update(overrides)
    return CompletionUsage(**fields)


def _nested_details(**overrides) -> PromptTokensDetails:
    """Real SDK nested container; extra='allow' keeps vendor extras like cache_write_tokens."""
    fields: dict = dict(cached_tokens=0)
    fields.update(overrides)
    return PromptTokensDetails(**fields)


class TestUsageCacheRead:
    """4.1/4.2: _usage_cache_read priority chain across known wire formats."""

    def test_nested_openai_standard_format(self):
        """LongCat/OpenAI style: hits only in prompt_tokens_details.cached_tokens."""
        usage = _sdk_usage(prompt_tokens_details=_nested_details(cached_tokens=1664))
        assert custom_adapter._usage_cache_read(usage) == 1664

    def test_nested_zero_fill_resolves_zero(self):
        usage = _sdk_usage(prompt_tokens_details=_nested_details(cached_tokens=0))
        assert custom_adapter._usage_cache_read(usage) == 0

    def test_top_level_prompt_cache_hit_tokens_unchanged(self):
        """DeepSeek top-level format (backward compat)."""
        usage = _sdk_usage(prompt_cache_hit_tokens=1500)
        assert custom_adapter._usage_cache_read(usage) == 1500

    def test_top_level_cached_tokens_unchanged(self):
        usage = _sdk_usage(cached_tokens=1200)
        assert custom_adapter._usage_cache_read(usage) == 1200

    def test_top_level_wins_over_nested(self):
        usage = _sdk_usage(
            prompt_cache_hit_tokens=1500,
            prompt_tokens_details=_nested_details(cached_tokens=100),
        )
        assert custom_adapter._usage_cache_read(usage) == 1500

    def test_zero_top_level_falls_through_to_nested(self):
        """0-valued candidate falls through; harmless (all-zero formats resolve 0)."""
        usage = _sdk_usage(
            prompt_cache_hit_tokens=0,
            prompt_tokens_details=_nested_details(cached_tokens=1400),
        )
        assert custom_adapter._usage_cache_read(usage) == 1400

    def test_no_cache_fields_resolves_zero(self):
        assert custom_adapter._usage_cache_read(_sdk_usage()) == 0

    def test_missing_nested_container_resolves_zero(self):
        usage = _sdk_usage(prompt_tokens_details=None)
        assert custom_adapter._usage_cache_read(usage) == 0

    def test_nested_usage_field_rejects_bool(self):
        """_nested_usage_field mirrors _usage_field semantics (bool is not a count)."""
        usage = SimpleNamespace(
            prompt_tokens_details=SimpleNamespace(cached_tokens=True)
        )
        assert custom_adapter._nested_usage_field(
            usage, "prompt_tokens_details", "cached_tokens"
        ) == 0


class TestUsageCacheCreation:
    """4.3: _usage_cache_creation chain; accounting intact when fields absent."""

    def test_nested_cache_write_tokens(self):
        usage = _sdk_usage(
            prompt_tokens_details=_nested_details(
                cached_tokens=1664, cache_write_tokens=32
            )
        )
        assert custom_adapter._usage_cache_creation(usage) == 32

    def test_top_level_anthropic_creation(self):
        usage = _sdk_usage(cache_creation_input_tokens=77)
        assert custom_adapter._usage_cache_creation(usage) == 77

    def test_top_level_creation_tokens_alias(self):
        usage = _sdk_usage(cache_creation_tokens=88)
        assert custom_adapter._usage_cache_creation(usage) == 88

    def test_top_level_creation_wins_over_nested(self):
        usage = _sdk_usage(
            cache_creation_input_tokens=77,
            prompt_tokens_details=_nested_details(cache_write_tokens=32),
        )
        assert custom_adapter._usage_cache_creation(usage) == 77

    def test_no_cache_fields_creation_zero_accounting_intact(self):
        usage = _sdk_usage()
        assert custom_adapter._usage_cache_read(usage) == 0
        assert custom_adapter._usage_cache_creation(usage) == 0
        assert custom_adapter._usage_field(usage, "prompt_tokens") == 1696
        assert custom_adapter._usage_field(usage, "completion_tokens") == 64


class TestDetectCacheStylePresenceBased:
    """4.4: detection classifies by field presence, not hit counts."""

    def test_first_turn_cache_fill_is_deepseek(self):
        """Zero-valued nested cached_tokens still classifies (container present)."""
        usage = _sdk_usage(prompt_tokens_details=_nested_details(cached_tokens=0))
        assert custom_adapter.detect_cache_style_from_usage(usage) == 'deepseek'

    def test_no_cache_fields_is_none(self):
        assert custom_adapter.detect_cache_style_from_usage(_sdk_usage()) == 'none'

    def test_top_level_creation_field_is_anthropic_even_at_zero(self):
        usage = _sdk_usage(cache_creation_input_tokens=0)
        assert custom_adapter.detect_cache_style_from_usage(usage) == 'anthropic'

    def test_nested_cache_write_tokens_alone_not_anthropic(self):
        """LongCat nests cache_write_tokens in prompt_tokens_details → deepseek, not anthropic."""
        usage = _sdk_usage(
            prompt_tokens_details=_nested_details(
                cached_tokens=0, cache_write_tokens=32
            )
        )
        assert custom_adapter.detect_cache_style_from_usage(usage) == 'deepseek'

    def test_none_usage_undetermined(self):
        assert custom_adapter.detect_cache_style_from_usage(None) is None


async def test_nested_cache_usage_flows_to_run_usage(conversation, monkeypatch):
    """4.1: nested OpenAI-standard usage reaches run.usage and message.usage."""
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hi"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(
                    choices=[],
                    usage=_sdk_usage(
                        prompt_tokens_details=_nested_details(
                            cached_tokens=1664, cache_write_tokens=32
                        )
                    ),
                ),
            ]
        ],
    )

    events = await _collect(CustomAdapter(), _input(conversation))

    run_usage = next(e for e in events if e.type == "run.usage")
    assert run_usage.usage.cache_read_tokens == 1664
    assert run_usage.usage.cache_creation_tokens == 32
    assert run_usage.usage.input_tokens == 1696
    msg_usage = next(e for e in events if e.type == "message.usage")
    assert msg_usage.usage.cache_read_tokens == 1664


def _compatible_input(conversation, **overrides) -> AdapterInput:
    return _input(
        conversation,
        custom_config=CustomConfig(
            model_provider="openai-compatible", supports_vision=False
        ),
        model_profile_id="prof_1",
        **overrides,
    )


async def test_streaming_run_persists_detected_cache_style(conversation, monkeypatch):
    """4.5: openai-compatible run without declared style persists detection."""
    persisted: list[tuple[str, str]] = []

    async def _fake_persist(model_profile_id: str, cache_style: str) -> None:
        persisted.append((model_profile_id, cache_style))

    monkeypatch.setattr(custom_adapter, "_persist_detected_cache_style", _fake_persist)
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hi"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(
                    choices=[],
                    usage=_sdk_usage(
                        prompt_tokens_details=_nested_details(cached_tokens=0)
                    ),
                ),
            ]
        ],
    )

    events = await _collect(CustomAdapter(), _compatible_input(conversation))
    for _ in range(3):
        await asyncio.sleep(0)  # let the fire-and-forget write-back task run

    assert persisted == [("prof_1", "deepseek")]
    run_usage = next(e for e in events if e.type == "run.usage")
    assert run_usage.usage.cache_style == "deepseek"


async def test_streaming_run_skips_writeback_for_none_detection(
    conversation, monkeypatch
):
    """'none' is not persisted — profile stays un-learned for re-detection."""
    persisted: list[tuple[str, str]] = []

    async def _fake_persist(model_profile_id: str, cache_style: str) -> None:
        persisted.append((model_profile_id, cache_style))

    monkeypatch.setattr(custom_adapter, "_persist_detected_cache_style", _fake_persist)
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hi"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(choices=[], usage=_sdk_usage()),
            ]
        ],
    )

    events = await _collect(CustomAdapter(), _compatible_input(conversation))
    for _ in range(3):
        await asyncio.sleep(0)

    assert persisted == []
    run_usage = next(e for e in events if e.type == "run.usage")
    assert run_usage.usage.cache_style == "none"


async def test_streaming_run_no_detection_when_style_declared(
    conversation, monkeypatch
):
    """4.5: user-declared cache_style disables detection and write-back."""
    persisted: list[tuple[str, str]] = []

    async def _fake_persist(model_profile_id: str, cache_style: str) -> None:
        persisted.append((model_profile_id, cache_style))

    monkeypatch.setattr(custom_adapter, "_persist_detected_cache_style", _fake_persist)
    _install_fake_client(
        monkeypatch,
        [
            [
                _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="hi"))]),
                _FakeChunk(
                    choices=[_FakeChoice(delta=_FakeDelta(), finish_reason="stop")]
                ),
                _FakeChunk(
                    choices=[],
                    usage=_sdk_usage(
                        prompt_tokens_details=_nested_details(cached_tokens=999)
                    ),
                ),
            ]
        ],
    )

    events = await _collect(
        CustomAdapter(), _compatible_input(conversation, cache_style="anthropic")
    )
    for _ in range(3):
        await asyncio.sleep(0)

    assert persisted == []
    run_usage = next(e for e in events if e.type == "run.usage")
    assert run_usage.usage.cache_style == "anthropic"


async def test_usage_shape_logged_once_per_model(conversation, monkeypatch, caplog):
    """4.6: first usage per model logs one DEBUG line; later usages don't repeat."""
    custom_adapter._SEEN_USAGE_SHAPE_MODELS.clear()
    usage = _sdk_usage(prompt_tokens_details=_nested_details(cached_tokens=1664))

    with caplog.at_level(logging.DEBUG, logger="app.adapters.custom_adapter"):
        for _ in range(2):
            _install_fake_client(
                monkeypatch,
                [
                    [
                        _FakeChunk(
                            choices=[_FakeChoice(delta=_FakeDelta(content="hi"))]
                        ),
                        _FakeChunk(
                            choices=[
                                _FakeChoice(delta=_FakeDelta(), finish_reason="stop")
                            ]
                        ),
                        _FakeChunk(choices=[], usage=usage),
                    ]
                ],
            )
            await _collect(CustomAdapter(), _input(conversation))

    shape_logs = [r for r in caplog.records if "[usage-shape]" in r.getMessage()]
    assert len(shape_logs) == 1
    assert "model=test-model" in shape_logs[0].getMessage()
    assert "prompt_tokens_details.cached_tokens" in shape_logs[0].getMessage()
