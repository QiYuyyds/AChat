"""AChatAgentRunner 单测 (任务 2.5) — httpx MockTransport 覆盖成功/失败/超时路径。

完成检测走 HTTP 降级通道 (纯 MockTransport 即可); 进程内 event_bus 通道
单独覆盖。trace_id 经注入 resolver 控制, 不依赖 Phoenix。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from agent_eval.core.contract import TransientError, TrialSession
from agent_eval.core.types import (
    EvalTask,
    EvidenceKind,
    GraderConfig,
    GraderType,
    Observation,
    ObservedBy,
    TaskView,
)

from app.eval_integration.client import AChatApiClient
from app.eval_integration.environment import (
    PROBE_WORKSPACE_FILES,
    AChatWorkspaceEnvironment,
    collect_workspace_files,
)
from app.eval_integration.errors import AgentRunError
from app.eval_integration.runner import AChatAgentRunner, WorkspaceCoordinator

AGENT_ID = "ag_eval_target"


class RunnerUnderTest(AChatAgentRunner):
    """测试壳: 用例仍按 `run(task)` 的形状调用, 但真正走的是 ③ 的新契约。

    框架递给被评方的永远是裁好的 ``TaskView`` + 一个 ``TrialSession``; 这里照此
    构造, 只是把 session 的探针回调做成用例可注入的 —— 否则绝大多数用例都要重复
    那两行样板, 而被测的东西一点没变。
    """

    def __init__(self, *args, probe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._probe = probe

    async def run(self, task: EvalTask, session: TrialSession | None = None):
        return await super().run(
            task if isinstance(task, TaskView) else TaskView.of(task),
            session or TrialSession(probe=self._probe),
        )


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
    probe=...,
    **kwargs,
) -> RunnerUnderTest:
    client = AChatApiClient(
        base_url="http://mock",
        token_provider=lambda: asyncio.sleep(0, result="token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    resolver = (lambda run_id: asyncio.sleep(0, result=trace)) if trace else None
    if probe is ...:
        probe = _workspace_probe(client, handler, coordinator)
    return RunnerUnderTest(
        client,
        AGENT_ID,
        completion_channel=completion_channel,
        trace_resolver=resolver,
        trace_wait_timeout=0.05,
        coordinator=coordinator,
        run_timeout=run_timeout,
        poll_interval=poll_interval,
        probe=probe,
        **kwargs,
    )


def _workspace_probe(client: AChatApiClient, mock: MockAChat, coordinator=None):
    """真实形状的取证探针: 经 fs API 读实际 workspace, 返回 harness 级读数。"""

    async def probe(channel: str = ""):
        conversation_id = (
            coordinator.current.conversation_id
            if coordinator is not None and coordinator.current
            else mock.last_conversation_id
        )
        payload = await collect_workspace_files(client, conversation_id)
        return [
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                channel=channel or PROBE_WORKSPACE_FILES,
                value=payload,
            )
        ]

    return probe


def _harness_files(evidence) -> dict[str, str]:
    """从取证通道里取最后一份 workspace 读数的文件内容。"""
    for reading in reversed(evidence.harness_state):
        if reading.is_absent or not isinstance(reading.value, dict):
            continue
        if "files" in reading.value:
            return reading.value["files"]
    return {}


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
        self.created_conversations: list[dict] = []  # POST /conversations 载荷
        self.written_files: dict[str, str] = {}
        self.deleted: list[str] = []
        self.pre_seed_entries = pre_seed_entries or []
        self.final_message_status = final_message_status
        self.messages_status_sequence = messages_status_sequence
        self._status_cursor = 0
        self.artifacts = artifacts or []
        self.conversation_ids = conversation_ids  # None → 每次新 id
        self.last_conversation_id: str | None = None
        self._conv_counter = 0
        self.fail_send = fail_send
        self.transport_error = transport_error

    def _conv_id(self) -> str:
        if self.conversation_ids is not None:
            conv_id = self.conversation_ids[
                min(self._conv_counter, len(self.conversation_ids) - 1)
            ]
            self._conv_counter += 1
        else:
            self._conv_counter += 1
            conv_id = f"conv_{self._conv_counter}"
        self.last_conversation_id = conv_id
        return conv_id

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
            self.created_conversations.append(json.loads(request.content))
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
    evidence = await runner.run(_task())

    assert evidence.trace_id == "trace_abc"
    transcript = evidence.messages()
    assert [m["role"] for m in transcript] == ["user", "agent"]
    assert transcript[1]["content"] == "done"

    reported = evidence.state_payload()  # 被评侧通道 (适配层交付 + agent 自述)
    assert reported["conversation_id"] == "conv_1"
    assert reported["run_ids"] == ["run_1"]
    assert reported["artifacts"] == [{"id": "art_1", "type": "document", "title": "Doc"}]

    # 请求序列: 创建会话 → listdir(种子前) → 写种子 → listdir(种子后) →
    # messages → 取证探针读 workspace → artifacts → 清理删除
    methods_paths = mock.requests
    assert methods_paths[0] == ("POST", "/api/conversations")
    assert ("POST", "/api/conversations/conv_1/messages") in methods_paths
    assert mock.written_files == {}  # 无种子文件
    assert mock.deleted == ["conv_1"]  # 无 coordinator → 自行清理


async def test_run_delivers_three_channels_separately():
    """三条通道各自带来源: 取证 ≠ 适配层交付 ≠ agent 自述。"""
    runner = _make_runner(MockAChat())
    evidence = await runner.run(_task())

    assert [obs.observed_by.value for obs in evidence.harness_state] == ["harness"]
    assert evidence.harness_state[0].channel == PROBE_WORKSPACE_FILES
    levels = {obs.observed_by for obs in evidence.subject_state}
    assert levels == {ObservedBy.RUNNER, ObservedBy.SUBJECT}
    # agent 自述单独成一条读数, 不会与适配层交付的元数据混成一个东西
    claims = [
        obs.value for obs in evidence.subject_state if obs.observed_by is ObservedBy.SUBJECT
    ]
    assert claims == [{"assistant_claims": "done"}]


async def test_run_writes_seed_files_before_prompt():
    mock = MockAChat()
    runner = _make_runner(mock)
    evidence = await runner.run(
        _task({"files": {"seed/notes.md": "# hello", "data.csv": "a,b"}})
    )

    assert mock.written_files == {"seed/notes.md": "# hello", "data.csv": "a,b"}
    assert evidence.state_payload()["seed_files"] == ["data.csv", "seed/notes.md"]
    # workspace 文件内容经取证通道交付 (不再是自报通道里那个 files 字典)
    assert _harness_files(evidence)["seed/notes.md"] == "# hello"


async def test_run_no_trace_when_tracing_disabled(monkeypatch):
    from app.eval_integration import runner as runner_mod

    monkeypatch.setattr(runner_mod.AChatAgentRunner, "_trace_enabled", staticmethod(lambda: False))
    mock = MockAChat()
    runner = _make_runner(mock, trace=None)
    evidence = await runner.run(_task())

    assert evidence.trace_id == ""
    assert "trace_id_unavailable" in evidence.state_payload()


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


# ─── task 级会话配置 (env.agent_id / env.conversation) ──────────────────────


async def test_no_conversation_config_keeps_default_payload():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(_task())

    (created,) = mock.created_conversations
    assert created["mode"] == "single"
    assert created["agentIds"] == [AGENT_ID]
    assert "dispatchMode" not in created  # 未配置 → 载荷与既有行为一致
    assert "@" not in created["title"]  # title 不带 agent 标记


async def test_env_agent_id_overrides_global_default():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(_task({"agent_id": "ag_rag"}))

    (created,) = mock.created_conversations
    assert created["mode"] == "single"
    assert created["agentIds"] == ["ag_rag"]
    assert created["title"].endswith("@ag_rag")


async def test_conversation_single_orchestrated_full_override():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(
        _task({"conversation": {"mode": "single", "dispatch_mode": "orchestrated"}})
    )

    (created,) = mock.created_conversations
    assert created["mode"] == "single"
    assert created["agentIds"] == [AGENT_ID]  # 未给 agent_ids → 全局默认
    assert created["dispatchMode"] == "orchestrated"


async def test_conversation_mode_defaults_to_single():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(_task({"conversation": {"agent_ids": [AGENT_ID]}}))

    (created,) = mock.created_conversations
    assert created["mode"] == "single"
    assert created["agentIds"] == [AGENT_ID]


async def test_conversation_group_with_multiple_agents():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(
        _task({"conversation": {"mode": "group", "agent_ids": ["ag_a", "ag_b"]}})
    )

    (created,) = mock.created_conversations
    assert created["mode"] == "group"
    assert created["agentIds"] == ["ag_a", "ag_b"]


async def test_conversation_wins_over_env_agent_id():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(
        _task(
            {
                "agent_id": "ag_fast",
                "conversation": {"mode": "single", "agent_ids": ["ag_slow"]},
            }
        )
    )

    (created,) = mock.created_conversations
    assert created["agentIds"] == ["ag_slow"]  # conversation 全量覆盖, agent_id 被忽略


async def test_conversation_without_agent_ids_ignores_env_agent_id():
    mock = MockAChat()
    runner = _make_runner(mock)
    await runner.run(_task({"agent_id": "ag_rag", "conversation": {"mode": "single"}}))

    (created,) = mock.created_conversations
    assert created["agentIds"] == [AGENT_ID]  # 全量覆盖语义: env.agent_id 不参与回退


@pytest.mark.parametrize(
    "env, fragment",
    [
        ({"conversation": {"mode": "group", "agent_ids": [AGENT_ID]}},
         "agent_ids 至少需要 2 个"),
        ({"conversation": {"mode": "group", "agent_ids": []}},
         "agent_ids 至少需要 2 个"),
        ({"conversation": {"mode": "single", "agent_ids": ["a", "b"]}},
         "恰好 1 个 agent"),
        ({"conversation": {"mode": "guide"}}, "'guide' 非法"),
        ({"conversation": {"mode": "single", "dispatch_mode": "magic"}},
         "'magic' 非法"),
        ({"conversation": "single"}, "必须是 dict"),
        ({"conversation": {"mode": "single", "agent_ids": "ag_x"}},
         "string 列表"),
        ({"conversation": {"mode": "single", "agent_ids": [42]}},
         "string 列表"),
        ({"agent_id": 123}, "必须是非空 string"),
        ({"agent_id": "  "}, "必须是非空 string"),
    ],
)
async def test_invalid_conversation_config_fails_loudly(env, fragment):
    mock = MockAChat()
    runner = _make_runner(mock)
    with pytest.raises(AgentRunError, match=fragment):
        await runner.run(_task(env))
    # 校验发生在建会话前 → 未发出任何请求, 不静默回退到默认会话
    assert mock.requests == []


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
    evidence = await task
    await publisher
    assert evidence.trace_id == "trace_abc"
    assert evidence.state_payload()["run_ids"] == ["run_1"]


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
    runner = RunnerUnderTest(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
        probe=environment.probe,
    )

    task = _task({"files": {"a.txt": "x"}})
    evidence = await runner.run(task)

    # 框架在 trial 收尾时依次调用 取证探针 → teardown → verify_clean
    assert coordinator.current is not None
    assert coordinator.current.conversation_id == "conv_1"
    assert [obs.channel for obs in evidence.harness_state] == [PROBE_WORKSPACE_FILES]
    await environment.teardown(task)
    assert coordinator.current is None
    assert coordinator.last is not None
    assert coordinator.last.conversation_id == "conv_1"
    assert mock.deleted == ["conv_1"]

    verify = await environment.verify_clean({}, evidence.harness_state)
    assert verify["clean"] is False  # 种子前清单非空 → 共享目录退化告警
    kinds = {d["kind"] for d in verify["differences"]}
    assert "foreign_files" in kinds
    changes = next(d for d in verify["differences"] if d["kind"] == "trial_changes")
    assert changes["source"] == "harness_probe"  # 末期状态读的是独立取证
    assert changes["files"] == []  # a.txt 在种子后基线里就有 → 相对基线无变更
    # 取证读数本身确实看到了这个文件 (这才是要保住的能力)
    assert "a.txt" in _harness_files(evidence)


async def test_probe_without_trial_reports_missing_not_empty():
    """没有进行中的 trial 会话时, 探针必须报「没取到」而不是交空读数。"""
    environment = AChatWorkspaceEnvironment(_client_of(MockAChat()), WorkspaceCoordinator())
    readings = await environment.probe("workspace_files")

    assert len(readings) == 1
    assert readings[0].is_absent is True
    assert readings[0].absent_reason == "provider_unavailable"


async def test_probe_follows_the_current_trial_not_the_caller():
    """运行中取证与结束前取证共用 probe(): 它读 ``coordinator.current``。

    两条 trial 交叠时后 begin 的会覆盖前一条 —— 这条约束写出来而不是假装没有:
    宿主装配因此固定 ``concurrency=1`` (§17.5 隔离正确性优先)。
    """
    mock = MockAChat()
    client = _client_of(mock)
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)

    coordinator.begin("conv_a")
    client_and_files = await collect_workspace_files(client, "conv_a")
    assert client_and_files["files"] == {}  # 还没写过任何文件

    coordinator.begin("conv_b")  # 第二条 trial 交叠进来
    await client.fs_write("conv_b", "only_in_b.txt", "b")
    readings = await environment.probe(PROBE_WORKSPACE_FILES)

    assert readings[0].observed_by is ObservedBy.HARNESS
    assert "only_in_b.txt" in readings[0].value["files"]
    # 探针没有「谁发起就读谁」的概念: 它只能读到当下那条 trial
    assert coordinator.current.conversation_id == "conv_b"


async def test_probe_unknown_channel_names_the_available_ones():
    environment = AChatWorkspaceEnvironment(_client_of(MockAChat()), WorkspaceCoordinator())
    coordinator = environment.coordinator
    coordinator.begin("conv_1")
    try:
        readings = await environment.probe("registry_dump")
    finally:
        coordinator.clear()

    assert readings[0].is_absent is True
    assert readings[0].absent_reason == "provider_not_covered"
    assert PROBE_WORKSPACE_FILES in readings[0].detail


async def test_environment_fresh_workspace_is_clean():
    mock = MockAChat()
    client = _client_of(mock)
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)
    runner = RunnerUnderTest(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
        probe=environment.probe,
    )
    task = _task()
    await runner.run(task)
    await environment.teardown(task)  # 框架收尾顺序: 取证 → teardown → verify_clean
    verify = await environment.verify_clean({})
    assert verify["clean"] is True
    assert mock.deleted == ["conv_1"]


async def test_environment_reused_conversation_flagged():
    mock = MockAChat(conversation_ids=["conv_fixed"])
    client = _client_of(mock)
    coordinator = WorkspaceCoordinator()
    environment = AChatWorkspaceEnvironment(client, coordinator)
    runner = RunnerUnderTest(
        client, AGENT_ID, completion_channel="http",
        trace_resolver=lambda run_id: asyncio.sleep(0, result="t"),
        coordinator=coordinator, cleanup_conversations=False,
        probe=environment.probe,
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
