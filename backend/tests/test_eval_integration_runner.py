"""AChatAgentRunner 单测 (任务 2.5) — httpx MockTransport 覆盖成功/失败/超时路径。

完成检测走 HTTP 降级通道 (纯 MockTransport 即可); 进程内 event_bus 通道
单独覆盖。trace_id 经注入 resolver 控制, 不依赖 Phoenix。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_eval.core.contract import TransientError
from agent_eval.core.types import EvalTask, GraderConfig, GraderType

from app.eval_integration.client import AChatApiClient
from app.eval_integration.environment import AChatWorkspaceEnvironment
from app.eval_integration.errors import AgentRunError
from app.eval_integration.runner import AChatAgentRunner, WorkspaceCoordinator

AGENT_ID = "ag_eval_target"


def _task(env: dict | None = None) -> EvalTask:
    return EvalTask(
        id="task_one",
        prompt="do the thing",
        env=env or {},
        graders=[GraderConfig(type=GraderType.CODE, name="dummy")],
    )


def _make_runner(
    handler,
    *,
    completion_channel="http",
    trace="trace_abc",
    coordinator=None,
    run_timeout=5.0,
    poll_interval=0.01,
    **kwargs,
) -> AChatAgentRunner:
    client = AChatApiClient(
        base_url="http://mock",
        token_provider=lambda: asyncio.sleep(0, result="token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    resolver = (lambda run_id: asyncio.sleep(0, result=trace)) if trace else None
    return AChatAgentRunner(
        client,
        AGENT_ID,
        completion_channel=completion_channel,
        trace_resolver=resolver,
        trace_wait_timeout=0.05,
        coordinator=coordinator,
        run_timeout=run_timeout,
        poll_interval=poll_interval,
        **kwargs,
    )


class MockAChat:
    """可编排的 MockTransport 处理器: 记录请求, 按脚本应答。"""

    def __init__(
        self,
        *,
        pre_seed_entries=None,
        final_message_status="complete",
        messages_status_sequence=None,
        artifacts=None,
        conversation_ids=None,
        fail_send=False,
        transport_error=False,
    ):
        self.requests: list[tuple[str, str]] = []
        self.written_files: dict[str, str] = {}
        self.deleted: list[str] = []
        self.pre_seed_entries = pre_seed_entries or []
        self.final_message_status = final_message_status
        self.messages_status_sequence = messages_status_sequence
        self._status_cursor = 0
        self.artifacts = artifacts or []
        self.conversation_ids = conversation_ids  # None → 每次新 id
        self._conv_counter = 0
        self.fail_send = fail_send
        self.transport_error = transport_error

    def _conv_id(self) -> str:
        if self.conversation_ids is not None:
            return self.conversation_ids[min(self._conv_counter, len(self.conversation_ids) - 1)]
        self._conv_counter += 1
        return f"conv_{self._conv_counter}"

    def _messages_payload(self) -> list[dict]:
        if self.messages_status_sequence is not None:
            status = self.messages_status_sequence[
                min(self._status_cursor, len(self.messages_status_sequence) - 1)
            ]
            self._status_cursor += 1
        else:
            status = self.final_message_status
        return [
            {
                "id": "m1",
                "conversationId": "conv_x",
                "role": "user",
                "parts": [{"type": "text", "content": "hello"}],
                "status": "complete",
                "runId": None,
                "createdAt": 1,
            },
            {
                "id": "m2",
                "conversationId": "conv_x",
                "role": "agent",
                "agentId": AGENT_ID,
                "parts": [{"type": "text", "content": "done"}],
                "status": status,
                "runId": "run_1",
                "createdAt": 2,
            },
        ]

    def _entries_for(self, prefix: str) -> list[dict]:
        """虚拟 fs: pre_seed 条目 + written_files 推导的目录树。"""
        entries = list(self.pre_seed_entries)
        seen_dirs: set[str] = set()
        for path, content in self.written_files.items():
            if prefix:
                if not path.startswith(prefix + "/"):
                    continue
                rest = path[len(prefix) + 1:]
            else:
                rest = path
            if "/" in rest:
                d = rest.split("/", 1)[0]
                if d not in seen_dirs:
                    seen_dirs.add(d)
                    entries.append({"name": d, "isDirectory": True})
            else:
                entries.append(
                    {"name": rest, "isDirectory": False, "size": len(content)}
                )
        return entries

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))

        if self.transport_error and path.endswith("/messages"):
            raise httpx.ConnectError("boom", request=request)

        if request.method == "POST" and path == "/api/conversations":
            return httpx.Response(
                201, json={"conversation": {"id": self._conv_id()}}
            )
        if request.method == "PATCH" and path.startswith("/api/conversations/"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "DELETE" and path.startswith("/api/conversations/"):
            self.deleted.append(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and path.endswith("/messages"):
            if self.fail_send:
                return httpx.Response(400, json={"error": "nope"})
            return httpx.Response(
                202, json={"messageId": "m2", "runIds": ["run_1"]}
            )
        if request.method == "GET" and path.endswith("/messages"):
            return httpx.Response(200, json={"messages": self._messages_payload()})
        if request.method == "POST" and path.endswith("/fs/write"):
            body = json.loads(request.content)
            self.written_files[body["path"]] = body["content"]
            return httpx.Response(200, json={"path": body["path"], "bytes": 3})
        if request.method == "GET" and path.endswith("/fs/listdir"):
            return httpx.Response(
                200,
                json={
                    "relPath": "",
                    "entries": self._entries_for(request.url.params.get("path", "")),
                },
            )
        if request.method == "GET" and path.endswith("/fs/read"):
            rel = request.url.params.get("path", "")
            return httpx.Response(
                200,
                json={"path": rel, "content": self.written_files.get(rel, "content-of-" + rel),
                      "truncated": False, "size": 3},
            )
        if request.method == "GET" and path == "/api/artifacts":
            return httpx.Response(200, json={"artifacts": self.artifacts})
        return httpx.Response(404, json={"error": f"unmocked {request.method} {path}"})


# ─── 成功路径 ────────────────────────────────────────────────────────────────


async def test_run_success_returns_trace_transcript_outcome():
    mock = MockAChat(
        artifacts=[{"id": "art_1", "type": "document", "title": "Doc"}],
    )
    runner = _make_runner(mock)
    trace_id, transcript, outcome = await runner.run(_task())

    assert trace_id == "trace_abc"
    assert [m["role"] for m in transcript] == ["user", "agent"]
    assert transcript[1]["content"] == "done"
    assert outcome["conversation_id"] == "conv_1"
    assert outcome["run_ids"] == ["run_1"]
    assert outcome["artifacts"] == [{"id": "art_1", "type": "document", "title": "Doc"}]

    # 请求序列: 创建会话 → listdir(种子前) → 写种子 → listdir(种子后) →
    # messages → listdir/outcome → artifacts → 清理删除
    methods_paths = mock.requests
    assert methods_paths[0] == ("POST", "/api/conversations")
    assert ("POST", "/api/conversations/conv_1/messages") in methods_paths
    assert mock.written_files == {}  # 无种子文件
    assert mock.deleted == ["conv_1"]  # 无 coordinator → 自行清理


async def test_run_writes_seed_files_before_prompt():
    mock = MockAChat()
    runner = _make_runner(mock)
    _, _, outcome = await runner.run(
        _task({"files": {"seed/notes.md": "# hello", "data.csv": "a,b"}})
    )

    assert mock.written_files == {"seed/notes.md": "# hello", "data.csv": "a,b"}
    assert outcome["seed_files"] == ["data.csv", "seed/notes.md"]
    # outcome 读回 workspace 文件内容
    assert outcome["files"]["seed/notes.md"] == "# hello"


async def test_run_no_trace_when_tracing_disabled(monkeypatch):
    from app.eval_integration import runner as runner_mod

    monkeypatch.setattr(runner_mod.AChatAgentRunner, "_trace_enabled", staticmethod(lambda: False))
    mock = MockAChat()
    runner = _make_runner(mock, trace=None)
    trace_id, _, outcome = await runner.run(_task())

    assert trace_id == ""
    assert "trace_id_unavailable" in outcome


async def test_run_raises_when_trace_unavailable(monkeypatch):
    from app.eval_integration import runner as runner_mod

    monkeypatch.setattr(runner_mod.AChatAgentRunner, "_trace_enabled", staticmethod(lambda: True))
    runner = _make_runner(MockAChat(), trace=None, run_timeout=5.0)
    with pytest.raises(AgentRunError, match="trace_id not found"):
        await runner.run(_task())


# ─── 失败路径 ────────────────────────────────────────────────────────────────


async def test_run_failed_message_raises_agent_run_error():
    mock = MockAChat(final_message_status="error")
    runner = _make_runner(mock)
    with pytest.raises(AgentRunError) as exc_info:
        await runner.run(_task())
    assert exc_info.value.status == "failed"
    assert exc_info.value.run_ids == ["run_1"]
    assert isinstance(exc_info.value.elapsed_ms, float) and exc_info.value.elapsed_ms >= 0


async def test_run_aborted_message_raises_agent_run_error():
    mock = MockAChat(final_message_status="aborted")
    runner = _make_runner(mock)
    with pytest.raises(AgentRunError) as exc_info:
        await runner.run(_task())
    assert exc_info.value.status == "aborted"


async def test_run_timeout_raises_agent_run_error():
    mock = MockAChat(final_message_status="streaming")
    runner = _make_runner(mock, run_timeout=0.05, poll_interval=0.01)
    with pytest.raises(AgentRunError) as exc_info:
        await runner.run(_task())
    assert exc_info.value.status == "timeout"


async def test_transport_error_maps_to_transient():
    mock = MockAChat(transport_error=True)
    runner = _make_runner(mock)
    with pytest.raises(TransientError):
        await runner.run(_task())


# ─── 进程内完成检测通道 ──────────────────────────────────────────────────────


async def test_in_process_completion_via_event_bus():
    from app.schemas.events import RunEndEvent
    from app.services.event_bus import event_bus

    mock = MockAChat()
    runner = _make_runner(mock, completion_channel="in_process")

    async def publish_run_end():
        await asyncio.sleep(0.02)
        event_bus.publish(
            RunEndEvent(
                conversation_id="conv_1",
                timestamp=1,
                run_id="run_1",
                agent_id=AGENT_ID,
                trigger_message_id="m2",
                status="complete",
            )
        )

    task = asyncio.create_task(runner.run(_task()))
    publisher = asyncio.create_task(publish_run_end())
    trace_id, _, outcome = await task
    await publisher
    assert trace_id == "trace_abc"
    assert outcome["run_ids"] == ["run_1"]


async def test_in_process_run_failed_event_raises():
    from app.schemas.events import RunEndEvent
    from app.services.event_bus import event_bus

    mock = MockAChat()
    runner = _make_runner(mock, completion_channel="in_process")

    async def publish_failure():
        await asyncio.sleep(0.02)
        event_bus.publish(
            RunEndEvent(
                conversation_id="conv_1",
                timestamp=1,
                run_id="run_1",
                agent_id=AGENT_ID,
                trigger_message_id="m2",
                status="failed",
                error="adapter exploded",
            )
        )

    task = asyncio.create_task(runner.run(_task()))
    publisher = asyncio.create_task(publish_failure())
    with pytest.raises(AgentRunError) as exc_info:
        await task
    await publisher
    assert exc_info.value.status == "failed"
    assert "adapter exploded" in str(exc_info.value)


# ─── coordinator / 环境集成 ──────────────────────────────────────────────────


async def test_coordinator_receives_trial_state_and_environment_cleans_up():
    mock = MockAChat(
        pre_seed_entries=[{"name": "seed.txt", "isDirectory": False, "size": 1}],
    )
    client = _client_of(mock)
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)
    runner = AChatAgentRunner(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
    )

    task = _task({"files": {"a.txt": "x"}})
    await runner.run(task)

    # 框架在 trial 收尾时依次调用 teardown → verify_clean (runner.core._run_trial)
    assert coordinator.current is not None
    assert coordinator.current.conversation_id == "conv_1"
    await environment.teardown(task)
    assert coordinator.current is None
    assert coordinator.last is not None
    assert coordinator.last.conversation_id == "conv_1"
    assert mock.deleted == ["conv_1"]

    verify = await environment.verify_clean({})
    assert verify["clean"] is False  # 种子前清单非空 → 共享目录退化告警
    kinds = {d["kind"] for d in verify["differences"]}
    assert "foreign_files" in kinds
    assert "trial_changes" in kinds  # a.txt 由种子后新增 → 记为变更 (参考信息)


async def test_environment_fresh_workspace_is_clean():
    mock = MockAChat()
    client = AChatApiClient(
        base_url="http://mock",
        token_provider=lambda: asyncio.sleep(0, result="token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(mock)),
    )
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)
    runner = AChatAgentRunner(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
    )
    task = _task()
    await runner.run(task)
    await environment.teardown(task)  # 框架收尾顺序: teardown → verify_clean
    verify = await environment.verify_clean({})
    assert verify["clean"] is True
    assert mock.deleted == ["conv_1"]


async def test_environment_reused_conversation_flagged():
    mock = MockAChat(conversation_ids=["conv_fixed"])
    client = AChatApiClient(
        base_url="http://mock",
        token_provider=lambda: asyncio.sleep(0, result="token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(mock)),
    )
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)
    runner = AChatAgentRunner(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
    )
    task = _task()
    await runner.run(task)
    await environment.teardown(task)
    await runner.run(task)  # 同一 conversation 复用两次
    await environment.teardown(task)
    verify = await environment.verify_clean({})
    kinds = {d["kind"] for d in verify["differences"]}
    assert "reused_conversation" in kinds
    assert verify["clean"] is False


def _client_of(mock: MockAChat) -> AChatApiClient:
    return AChatApiClient(
        base_url="http://mock",
        token_provider=lambda: asyncio.sleep(0, result="token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(mock)),
    )
