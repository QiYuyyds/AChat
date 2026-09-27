"""Tests for ReAct loop error propagation (fix-react-loop-error-classification).

Provider/SDK 调用抛出的异常不再被 catch-all 吞成 budget_exhausted「假成功」：
分类为 ProviderError（中文用户消息）后重新抛出，经 execute_run 的 catch-all
finalize(status="failed")，在会话中渲染「[失败] {消息}」；真实预算路径与
取消（CancelledError）路径不受影响。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest
import pytest_asyncio

from app.adapters import custom_adapter
from app.adapters.base import AdapterInput

# ─── Seed helpers ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sdk_conversation(db, test_user, tmp_path):
    """Conversation + custom(SDK) agent + default ModelProfile + trigger message."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Message, ModelProfile, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws-err"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    agent_id = "ag_sdk_err"
    trigger_message_id = "msg_trigger_err"
    now = now_ms()

    async with get_db() as session:
        session.add(ModelProfile(
            id="mp_sdk_err",
            name="test-profile",
            provider="openai",
            model_id="test-model",
            api_key="sk-test",
            api_base_url=None,
            is_default=True,
            supports_vision=False,
            created_at=now,
            updated_at=now,
        ))
        agent = Agent(
            id=agent_id,
            name="SdkErr",
            avatar="E",
            description="test sdk agent",
            system_prompt="you are a test agent",
            adapter_name="custom",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

        conv = Conversation(
            id=conv_id,
            title="T",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent_id]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(Workspace(
            id=new_workspace_id(),
            conversation_id=conv_id,
            root_path=str(ws_root),
            mode="sandbox",
            bound_path=None,
            created_at=now,
        ))
        session.add(Message(
            id=trigger_message_id,
            conversation_id=conv_id,
            role="user",
            parts=[{"type": "text", "content": "hello"}],
            status="complete",
            created_at=now,
        ))

    return {
        "conversation_id": conv_id,
        "agent_id": agent_id,
        "workspace_root": str(ws_root),
        "trigger_message_id": trigger_message_id,
    }


@pytest.fixture
def recorded_events(monkeypatch):
    """Capture every published event; publish() fanned out to SSE otherwise."""
    from app.services.event_bus import event_bus

    events: list = []

    def _recorder(event, user_id=None):
        events.append(event)

    monkeypatch.setattr(event_bus, "publish", _recorder)
    return events


class _FailingCompletions:
    """chat.completions.create that raises the scripted exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def create(self, **kwargs):
        raise self._exc


class _FailingChat:
    def __init__(self, exc: BaseException) -> None:
        self.completions = _FailingCompletions(exc)


class _FailingClient:
    def __init__(self, exc: BaseException) -> None:
        self.chat = _FailingChat(exc)


def _install_failing_client(monkeypatch, exc: BaseException) -> None:
    monkeypatch.setattr(
        custom_adapter, "_build_client", lambda *a, **k: _FailingClient(exc)
    )


def _auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(401, request=request, text="invalid api key")
    return openai.AuthenticationError(
        "Error code: 401 - invalid api key", response=response, body=None
    )


# ─── 3.1 集成：provider 错误 → run failed + [失败] 消息 ─────────────────────


async def test_authentication_error_run_fails_with_actionable_message(
    db, sdk_conversation, recorded_events, monkeypatch
):
    """401 → ProviderError 分类消息 → run failed + 会话渲染「[失败] …API Key…」。

    端到端走 execute_run → execute_simple_run → _run_react_loop →
    CustomAdapter.call_once（真实代码路径），仅 _build_client 返回抛
    AuthenticationError 的假 client。
    """
    from app.db.engine import get_db
    from app.db.models import AgentRun, Message
    from app.services.agent_runner import RunArgs, execute_run

    _install_failing_client(monkeypatch, _auth_error())

    run_id = "run_err_401"
    args = RunArgs(
        agent_id=sdk_conversation["agent_id"],
        conversation_id=sdk_conversation["conversation_id"],
        trigger_message_id=sdk_conversation["trigger_message_id"],
        user_id="test_user_1",
    )
    result = await execute_run(run_id, asyncio.Event(), args)

    assert result.status == "failed"
    assert "API Key" in (result.error or "")

    async with get_db() as session:
        run = (
            await session.execute(
                AgentRun.__table__.select().where(AgentRun.id == run_id)
            )
        ).first()
        assert run is not None
        assert run.status == "failed"
        assert "API Key" in (run.error or "")

        # _emit_error_visualisation：新错误消息（无流式消息可附加时）
        err_msg = (
            await session.execute(
                Message.__table__.select().where(Message.id == f"msg_err_{run_id}")
            )
        ).first()
        assert err_msg is not None
        parts_text = "".join(
            p.get("content", "") for p in (err_msg.parts or []) if isinstance(p, dict)
        )
        assert "[失败]" in parts_text
        assert "API Key" in parts_text
        assert "模型配置" in parts_text

    # run 不得以 budget_exhausted 语义收尾
    usage_events = [e for e in recorded_events if e.type == "run.usage"]
    assert all(e.stop_reason != "budget_exhausted" for e in usage_events)
    run_ends = [e for e in recorded_events if e.type == "run.end"]
    assert len(run_ends) == 1
    assert run_ends[0].status == "failed"
    assert run_ends[0].stop_reason != "budget_exhausted"


async def test_unclassifiable_error_keeps_original_text(
    db, sdk_conversation, recorded_events, monkeypatch
):
    """无法分类的异常走 fallback：run failed，错误消息含原文（截断）。"""
    from app.services.agent_runner import RunArgs, execute_run

    _install_failing_client(monkeypatch, RuntimeError("boom unexpected"))

    run_id = "run_err_fallback"
    args = RunArgs(
        agent_id=sdk_conversation["agent_id"],
        conversation_id=sdk_conversation["conversation_id"],
        trigger_message_id=sdk_conversation["trigger_message_id"],
        user_id="test_user_1",
    )
    result = await execute_run(run_id, asyncio.Event(), args)

    assert result.status == "failed"
    assert "模型调用失败" in (result.error or "")
    assert "boom unexpected" in (result.error or "")

    run_ends = [e for e in recorded_events if e.type == "run.end"]
    assert run_ends and run_ends[0].status == "failed"
    assert run_ends[0].stop_reason != "budget_exhausted"


# ─── 3.3 取消路径：CancelledError 不被分类/改写 ─────────────────────────────


async def test_cancelled_error_not_classified(sdk_conversation, monkeypatch):
    """call_once 抛 CancelledError 时原样传播，不包装为 ProviderError。"""
    from app.services.agent_runner import _run_react_loop

    async def _raising_call_once(self, input, cancel_event):
        yield SimpleNamespace(type="message.start", message_id="m1")
        raise asyncio.CancelledError()

    monkeypatch.setattr(custom_adapter.CustomAdapter, "call_once", _raising_call_once)

    adapter = custom_adapter.CustomAdapter()
    inp = AdapterInput(
        agent_id=sdk_conversation["agent_id"],
        conversation_id=sdk_conversation["conversation_id"],
        run_id="run_err_cancel",
        prompt="hello",
        workspace_path=sdk_conversation["workspace_root"],
        system_prompt="you are a test agent",
        api_key="sk-test",
        api_base_url=None,
        model_id="test-model",
        tool_names=[],
        custom_config=None,
    )
    with pytest.raises(asyncio.CancelledError):
        async for _ in _run_react_loop(
            adapter, inp, asyncio.Event(),
            run_id="run_err_cancel",
            agent_id=sdk_conversation["agent_id"],
            conversation_id=sdk_conversation["conversation_id"],
            model_id="test-model",
        ):
            pass


# ─── 2.1 直接断言：react loop 抛 ProviderError 且不 yield budget 兜底 ────────


async def test_react_loop_raises_provider_error_on_auth_failure(
    sdk_conversation, monkeypatch
):
    """catch-all 分类后重新抛出：ProviderError 消息含「API Key」，且流上
    不再出现 budget_exhausted 的 run.usage 兜底事件。"""
    from app.adapters.base import AdapterInput, CustomConfig
    from app.adapters.errors import ProviderError
    from app.services.agent_runner import _run_react_loop

    _install_failing_client(monkeypatch, _auth_error())

    adapter = custom_adapter.CustomAdapter()
    inp = AdapterInput(
        agent_id=sdk_conversation["agent_id"],
        conversation_id=sdk_conversation["conversation_id"],
        run_id="run_err_401_loop",
        prompt="hello",
        workspace_path=sdk_conversation["workspace_root"],
        system_prompt="you are a test agent",
        api_key="sk-test",
        api_base_url=None,
        model_id="test-model",
        tool_names=[],
        custom_config=CustomConfig(model_provider="openai", supports_vision=False),
    )
    events: list = []
    with pytest.raises(ProviderError) as exc_info:
        async for ev in _run_react_loop(
            adapter, inp, asyncio.Event(),
            run_id="run_err_401_loop",
            agent_id=sdk_conversation["agent_id"],
            conversation_id=sdk_conversation["conversation_id"],
            model_id="test-model",
        ):
            events.append(ev)

    assert "API Key" in str(exc_info.value)
    assert "模型配置" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, openai.AuthenticationError)
    stop_reasons = [
        getattr(e, "stop_reason", None) for e in events if e.type == "run.usage"
    ]
    assert "budget_exhausted" not in stop_reasons
