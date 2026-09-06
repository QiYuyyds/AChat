"""AChatAgentRunner — AgentRunner 契约的 AChat 实现 (任务 2.1; ③ 迁到证据契约)。

流程 (对照表 §14.1 已核对):
    1. POST /api/conversations         → 全新 sandbox 会话 (服务端默认)
    2. GET  fs/listdir                 → 种子前清单 (共享目录退化防御基线)
    3. POST fs/write (view.env.files)  → 写入种子文件
    4. GET  fs/listdir                 → 种子后基线清单 (verify_clean 基线)
    5. 订阅 event_bus → POST messages  → 先订阅再发送 (防丢快速失败的事件),
                                          取 runIds
    6. 等待完成                        → 进程内 RunEndEvent (主) / HTTP 消息
                                          状态轮询 (降级), 含超时
    7. GET messages                    → transcript
    8. trace_id                        → 进程内 SpanProcessor 桥 (主) /
                                          Phoenix 属性过滤 (降级)
    9. 交付证据 (change ③): workspace 文件走**评测侧取证通道**
       (``await session.harness_probe(PROBE_WORKSPACE_FILES)`` → 由环境管理器
       实现, 框架把读数钉成 harness 级), 适配层交付的会话/产物元数据记 runner
       级, agent 最后一条回复里的自述记 subject 级 —— 三条通道互不替换

WorkspaceCoordinator 是 runner 与 AChatWorkspaceEnvironment 之间的共享
trial 状态单元: runner 发布会话与基线清单, 环境管理器据此做快照/校验/恢复,
并据此实现取证探针。未装配环境管理器时 runner 自行清理会话 (cleanup_conversations)。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_eval.core.contract import TransientError, TrialSession
from agent_eval.core.types import (
    EvidenceKind,
    Observation,
    ObservedBy,
    TaskView,
    TrialEvidence,
)

from app.eval_integration import trace_bridge
from app.eval_integration.client import AChatApiClient
from app.eval_integration.errors import AgentRunError

logger = logging.getLogger(__name__)

CompletionChannel = Literal["in_process", "http"]

# 取证通道名 (与 environment.py 的 probe 实现一一对应)
PROBE_WORKSPACE_FILES = "workspace_files"
PROBE_DB_DUMP = "db_dump"

# workspace 递归收集上限 (防失控)
_MAX_OUTCOME_FILES = 50
_MAX_OUTCOME_DEPTH = 3
_MAX_LISTING_ENTRIES = 200
# 每文件读取上限 (fs/read 端点自身还会截断, 这里再挡一层)
_MAX_OUTCOME_FILE_BYTES = 200_000
# 递归收集时跳过的目录名
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}

# 降级轮询里判失败的消息 status (MessageRecord: streaming/complete/error/aborted/interrupted)
_FAILED_MESSAGE_STATUSES = {"error", "aborted", "interrupted"}

# task 级会话配置 (env["conversation"]) 合法枚举 — 对照 AChat
# CreateConversationRequest; guide 模式非评测对象, 不放行。
_CONVERSATION_MODES = ("single", "group")
_DISPATCH_MODES = ("solo", "orchestrated")


def _extract_text_from_parts(parts: list[dict]) -> str:
    """从消息 parts 提取纯文本 (与 orchestrator_prompts 提取语义一致, 精简版)。"""
    out: list[str] = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        ptype = p.get("type")
        if ptype in ("text", "thinking"):
            out.append(str(p.get("content", "")))
        elif ptype == "code":
            out.append(f"```{p.get('language', '')}\n{p.get('content', '')}\n```")
        else:
            out.append(f"[{ptype}]")
    return "\n".join(t for t in out if t)


@dataclass(frozen=True)
class ConversationSpec:
    """task 级会话配置解析结果 — create_conversation 建会话的唯一依据。"""

    mode: str
    agent_ids: list[str]
    dispatch_mode: str | None = None


@dataclass
class TrialWorkspace:
    """单次 trial 的 workspace 状态 (runner 发布, 环境管理器消费)。"""

    conversation_id: str
    pre_seed_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    post_seed_listing: dict[str, dict[str, Any]] = field(default_factory=dict)
    final_listing: dict[str, dict[str, Any]] | None = None
    deleted: bool = False


class WorkspaceCoordinator:
    """runner ↔ environment 的共享 trial 状态单元。"""

    def __init__(self) -> None:
        self.current: TrialWorkspace | None = None
        self.last: TrialWorkspace | None = None

    def begin(self, conversation_id: str) -> TrialWorkspace:
        self.current = TrialWorkspace(conversation_id=conversation_id)
        return self.current

    def clear(self, *, deleted: bool = False) -> None:
        if self.current is not None:
            self.current.deleted = deleted
            self.last = self.current
            self.current = None


async def collect_workspace_listing(
    client: AChatApiClient, conversation_id: str
) -> dict[str, dict[str, Any]]:
    """有界递归目录清单 → {rel_path: {name, isDirectory, size}}。

    runner (种子前后/末期基线) 与环境管理器 (取证探针 / teardown 兜底) 共用。
    列目录失败仅告警 (返回尽力清单), 不判 trial 失败。
    """
    listing: dict[str, dict[str, Any]] = {}

    async def walk(prefix: str, depth: int) -> None:
        if depth > _MAX_OUTCOME_DEPTH or len(listing) >= _MAX_LISTING_ENTRIES:
            return
        try:
            entries = await client.fs_listdir(conversation_id, prefix)
        except Exception as e:  # noqa: BLE001
            logger.warning("fs_listdir failed for %s/%s: %s", conversation_id, prefix, e)
            return
        for entry in entries:
            name = entry.get("name", "")
            if not name:
                continue
            rel = f"{prefix}/{name}" if prefix else name
            if len(listing) >= _MAX_LISTING_ENTRIES:
                return
            listing[rel] = {
                "name": name,
                "isDirectory": bool(entry.get("isDirectory")),
                "size": entry.get("size"),
            }
            if entry.get("isDirectory") and name not in _SKIP_DIRS:
                await walk(rel, depth + 1)

    await walk("", 1)
    return listing


class AChatAgentRunner:
    """经 AChat HTTP API 执行评测任务并**交付带来源的证据**。"""

    def __init__(
        self,
        client: AChatApiClient,
        agent_id: str,
        *,
        run_timeout: float = 300.0,
        poll_interval: float = 2.0,
        completion_channel: CompletionChannel = "http",
        trace_wait_timeout: float = 10.0,
        trace_resolver: Any = None,
        coordinator: WorkspaceCoordinator | None = None,
        cleanup_conversations: bool = True,
        conversation_title_prefix: str = "[Aeval]",
    ):
        """
        Args:
            client: AChat HTTP 客户端
            agent_id: 被评 agent ID (必填; 无默认)
            run_timeout: 等待 run 完成的超时 (秒)
            poll_interval: HTTP 降级轮询间隔 (秒)
            completion_channel: 完成检测通道 (auto=进程内优先, 不可用则 HTTP)
            trace_wait_timeout: 进程内等待 trace_id 映射的上限 (秒)
            trace_resolver: 自定义 ``(run_id) -> str | None`` 协程 (测试注入);
                缺省 = 进程内桥 + Phoenix 降级
            coordinator: 与 runner 共享的 trial 状态单元
            cleanup_conversations: 无 coordinator 时是否删除 trial 会话
        """
        if not agent_id:
            raise AgentRunError("AChatAgentRunner: agent_id is required")
        self.client = client
        self.agent_id = agent_id
        self.run_timeout = run_timeout
        self.poll_interval = poll_interval
        self.completion_channel = completion_channel
        self.trace_wait_timeout = trace_wait_timeout
        self._trace_resolver = trace_resolver
        self.coordinator = coordinator
        self.cleanup_conversations = cleanup_conversations
        self.conversation_title_prefix = conversation_title_prefix

    # ── AgentRunner 契约 ─────────────────────────────────────────────────

    async def run(self, view: TaskView, session: TrialSession) -> TrialEvidence:
        """执行一次 trial 并按通道交付证据。

        ``view`` 是框架裁出来的任务视图 (只有 id/description/prompt/env), 判据与
        答案键拿不到; 环境状态的读取全部走 ``session.harness_probe`` —— 由框架
        调用的取证通道, 读数会被钉成 ``harness`` 级, 不由本适配层自报。
        """
        started = time.monotonic()

        spec = self._resolve_conversation(view)
        lead_agent = spec.agent_ids[0]
        # task 级覆盖了 agent 时把实际 agent 带进 title, 便于 AChat 侧区分 trial
        agent_tag = "" if lead_agent == self.agent_id else f"@{lead_agent}"
        conversation_id = await self.client.create_conversation(
            title=f"{self.conversation_title_prefix} {view.id}{agent_tag}".strip(),
            agent_id=lead_agent,
            mode=spec.mode,
            agent_ids=spec.agent_ids,
            dispatch_mode=spec.dispatch_mode,
        )
        trial = self.coordinator.begin(conversation_id) if self.coordinator else None

        try:
            pre_seed = await self._collect_listing(conversation_id)
            if trial is not None:
                trial.pre_seed_files = pre_seed

            seeds = self._seed_files(view)
            for path in sorted(seeds):
                await self.client.fs_write(conversation_id, path, seeds[path])

            post_seed = await self._collect_listing(conversation_id)
            if trial is not None:
                trial.post_seed_listing = post_seed

            run_ids = await self._send_and_wait(conversation_id, view.prompt, started)

            messages = await self.client.list_messages(conversation_id)
            transcript = self._normalize_transcript(messages)
            trace_id = await self._resolve_trace_id(run_ids)
            artifacts = await self.client.list_artifacts(conversation_id)

            # 评测侧独立取证: 清单与内容由环境的探针读, 不进入自报通道。
            # 读数一并放进返回的证据 —— 框架合并时按对象身份去重, 不会重复计数,
            # 于是这份证据脱离编排层也自包含 (单测与审计都能直接读)。
            probe_readings = await session.harness_probe(PROBE_WORKSPACE_FILES)

            if trial is not None and trial.final_listing is None:
                trial.final_listing = await self._collect_listing(conversation_id)

            return self._build_evidence(
                trace_id=trace_id,
                conversation_id=conversation_id,
                run_ids=run_ids,
                transcript=transcript,
                artifacts=artifacts,
                seed_files=sorted(seeds),
                probe_readings=probe_readings,
            )

        except asyncio.CancelledError:
            raise
        except TransientError:
            raise  # 框架对 TransientError 做指数退避重试
        except AgentRunError as e:
            raise self._with_elapsed(e, started) from None
        except Exception as e:
            raise self._with_elapsed(
                AgentRunError(f"AChat run failed: {e}", status="error"), started
            ) from e
        finally:
            if self.coordinator is None and self.cleanup_conversations:
                await self._safe_delete(conversation_id)

    @staticmethod
    def _build_evidence(
        *,
        trace_id: str,
        conversation_id: str,
        run_ids: list[str],
        transcript: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        seed_files: list[str],
        probe_readings: list[Observation],
    ) -> TrialEvidence:
        """按通道装配证据 —— 三条通道各自带来源, 互不替换。"""
        evidence = TrialEvidence(trace_id=trace_id)
        evidence.harness_state.extend(probe_readings)

        # 适配层交付的执行记录: transcript 是**被评判的产出物**, 不是关于环境的
        # 主张, 因此记 runner 级 (把它记成 subject 会让所有内容与质量类判据失效)。
        for message in transcript:
            evidence.transcript.append(
                Observation(
                    kind=EvidenceKind.TRANSCRIPT,
                    observed_by=ObservedBy.RUNNER,
                    value=message,
                    channel="messages_api",
                )
            )

        # 适配层交付的元数据 (会话/运行/产物清单来自 AChat 自己的记录, 不是 agent 散文)
        reported: dict[str, Any] = {
            "conversation_id": conversation_id,
            "run_ids": run_ids,
            "seed_files": seed_files,
            "artifacts": artifacts,
        }
        if trace_id == "":
            reported["trace_id_unavailable"] = (
                "tracing disabled — trace channel explicitly off (§14.1.2)"
            )
        evidence.subject_state.append(
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.RUNNER,
                value=reported,
                channel="adapter_metadata",
            )
        )

        # agent 最后一条回复 = 它对「自己做了什么」的自述。默认不得单独支撑通过。
        claim = next(
            (
                m.get("content", "")
                for m in reversed(transcript)
                if m.get("role") in ("agent", "assistant") and m.get("content")
            ),
            "",
        )
        if claim:
            evidence.subject_state.append(
                Observation(
                    kind=EvidenceKind.STATE,
                    observed_by=ObservedBy.SUBJECT,
                    value={"assistant_claims": claim},
                    channel="agent_self_report",
                )
            )
        return evidence

    # ── task 级会话配置 (env["agent_id"] / env["conversation"]) ──────────

    def _resolve_conversation(self, view: TaskView) -> ConversationSpec:
        """解析 task 级会话配置 → ConversationSpec (纯解析, 无 I/O)。

        优先级: env["conversation"] (全量覆盖, 含 agent 选择 — env["agent_id"]
        被忽略, 避免部分合并的优先级迷宫) > env["agent_id"] > 全局默认
        self.agent_id。conversation 未给 agent_ids 时回退全局默认 agent。

        非法组合抛 AgentRunError (含违规字段与合法值), 不静默回退 — 评测
        配置错误必须显性暴露, 否则回归结果失真。在 trial 开始 (建会话前)
        校验, 错误即该 trial 失败。
        """
        env = view.env or {}

        env_agent_id = env.get("agent_id")
        if env_agent_id is not None and (
            not isinstance(env_agent_id, str) or not env_agent_id.strip()
        ):
            raise AgentRunError(
                f"TaskView.env['agent_id'] 必须是非空 string, got "
                f"{type(env_agent_id).__name__}: {env_agent_id!r}",
                status="error",
            )

        conversation = env.get("conversation")
        if conversation is None:
            return ConversationSpec(
                mode="single", agent_ids=[env_agent_id or self.agent_id]
            )

        if not isinstance(conversation, dict):
            raise AgentRunError(
                f"TaskView.env['conversation'] 必须是 dict (键: mode/agent_ids/"
                f"/dispatch_mode), got {type(conversation).__name__}: {conversation!r}",
                status="error",
            )

        mode = conversation.get("mode", "single")
        if mode not in _CONVERSATION_MODES:
            raise AgentRunError(
                f"TaskView.env['conversation']['mode']={mode!r} 非法 — "
                f"合法值: {list(_CONVERSATION_MODES)}",
                status="error",
            )

        raw_agent_ids = conversation.get("agent_ids")
        if raw_agent_ids is None:
            # conversation 全量覆盖语义: env["agent_id"] 不参与回退
            agent_ids = [self.agent_id]
        elif not isinstance(raw_agent_ids, list) or not all(
            isinstance(a, str) and a.strip() for a in raw_agent_ids
        ):
            raise AgentRunError(
                f"TaskView.env['conversation']['agent_ids'] 必须是非空 string 列表, "
                f"got {raw_agent_ids!r}",
                status="error",
            )
        else:
            agent_ids = raw_agent_ids

        if mode == "single" and len(agent_ids) != 1:
            raise AgentRunError(
                f"TaskView.env['conversation']: mode='single' 要求恰好 1 个 agent "
                f"(got {len(agent_ids)}: {agent_ids!r})",
                status="error",
            )
        if mode == "group" and len(agent_ids) < 2:
            raise AgentRunError(
                f"TaskView.env['conversation']: mode='group' 要求 agent_ids 至少需要 "
                f"2 个 (got {len(agent_ids)}: {agent_ids!r})",
                status="error",
            )

        dispatch_mode = conversation.get("dispatch_mode")
        if dispatch_mode is not None and dispatch_mode not in _DISPATCH_MODES:
            raise AgentRunError(
                f"TaskView.env['conversation']['dispatch_mode']={dispatch_mode!r} 非法 "
                f"— 合法值: {list(_DISPATCH_MODES)}",
                status="error",
            )

        return ConversationSpec(
            mode=mode, agent_ids=list(agent_ids), dispatch_mode=dispatch_mode
        )

    # ── Send + completion ────────────────────────────────────────────────

    async def _send_and_wait(
        self, conversation_id: str, prompt: str, started: float
    ) -> list[str]:
        """发送 prompt 并等待完成, 返回 run_ids。

        ``in_process`` 只有当 agent 执行与本评测器在**同一进程**时才可用
        (事件在服务进程发布, 独立脚本订阅不到); 由嵌入方显式声明, 不做猜测。
        进程内通道先订阅 event_bus 再发送 (防丢快速失败 run 的 RunEndEvent);
        发送前失败 (订阅不可用等) 自动降级 HTTP 轮询; 发送后失败不重发
        (重复发送有副作用), 直接上抛。
        """
        channel = self.completion_channel

        if channel == "http":
            send = await self.client.send_message(conversation_id, prompt)
            await self._wait_http(conversation_id, send["run_ids"], started)
            return send["run_ids"]

        send_holder: list[dict[str, Any]] = []
        try:
            return await self._wait_in_process(conversation_id, prompt, started, send_holder)
        except AgentRunError:
            raise
        except Exception as e:
            if not send_holder:
                logger.warning(
                    "in-process completion channel unavailable (%s); falling back to HTTP polling", e
                )
                send = await self.client.send_message(conversation_id, prompt)
                await self._wait_http(conversation_id, send["run_ids"], started)
                return send["run_ids"]
            raise

    async def _wait_in_process(
        self,
        conversation_id: str,
        prompt: str,
        started: float,
        send_holder: list[dict[str, Any]],
    ) -> list[str]:
        from app.schemas.events import RunEndEvent
        from app.services.event_bus import event_bus

        ended: dict[str, RunEndEvent] = {}
        async with event_bus.subscribe() as queue:
            send = await self.client.send_message(conversation_id, prompt)
            send_holder.append(send)
            remaining = set(send["run_ids"])
            while remaining:
                timeout = self.run_timeout - (time.monotonic() - started)
                if timeout <= 0:
                    raise self._timeout_error(send["run_ids"], started)
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=min(timeout, self.poll_interval)
                    )
                except asyncio.TimeoutError:
                    continue
                if not isinstance(event, RunEndEvent):
                    continue
                if event.run_id in remaining:
                    remaining.discard(event.run_id)
                    ended[event.run_id] = event

        for rid, ev in ended.items():
            if ev.status != "complete":
                raise AgentRunError(
                    f"AChat run {rid} ended with status={ev.status}"
                    + (f": {ev.error}" if ev.error else ""),
                    run_ids=send["run_ids"],
                    status=ev.status,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )
        return send["run_ids"]

    async def _wait_http(
        self, conversation_id: str, run_ids: list[str], started: float
    ) -> None:
        """HTTP 降级: 轮询消息列表, 按 runId 过滤 message status 推导完成。"""
        while True:
            elapsed = time.monotonic() - started
            statuses = await self.client.run_message_statuses(conversation_id, run_ids)
            if statuses and all(
                self.client.is_terminal_message_status(statuses.get(r)) for r in run_ids
            ):
                for rid, status in statuses.items():
                    if status in _FAILED_MESSAGE_STATUSES:
                        raise AgentRunError(
                            f"AChat run {rid} message ended with status={status}",
                            run_ids=run_ids,
                            status="failed" if status == "error" else "aborted",
                            elapsed_ms=elapsed * 1000,
                        )
                return
            if elapsed >= self.run_timeout:
                raise self._timeout_error(run_ids, started)
            await asyncio.sleep(self.poll_interval)

    def _timeout_error(self, run_ids: list[str], started: float) -> AgentRunError:
        return AgentRunError(
            f"AChat run did not complete within {self.run_timeout}s",
            run_ids=run_ids,
            status="timeout",
            elapsed_ms=(time.monotonic() - started) * 1000,
        )

    @staticmethod
    def _with_elapsed(err: AgentRunError, started: float) -> AgentRunError:
        if not err.elapsed_ms:
            err.elapsed_ms = (time.monotonic() - started) * 1000
        return err

    # ── trace_id ─────────────────────────────────────────────────────────

    async def _resolve_trace_id(self, run_ids: list[str]) -> str:
        """进程内桥优先, Phoenix 属性过滤降级; 明确失败而非静默空值 (§14.1.2)。

        ``trace_enabled=False`` 时 trace 通道按配置显式关闭 — 返回空串并在
        自报状态里记录原因 (不判 trial 失败)。
        """
        if not self._trace_enabled():
            return ""

        run_id = run_ids[0] if run_ids else ""
        if not run_id:
            raise AgentRunError("no run ids to resolve trace_id for", status="error")

        if self._trace_resolver is not None:
            tid = await self._trace_resolver(run_id)
        else:
            tid = None
            # 进程内桥只在同进程时才可能命中: 走 HTTP 通道意味着 agent 在另一个
            # 进程, 等它只会白烧 trace_wait_timeout 秒再落到 Phoenix 回查。
            if self.completion_channel == "in_process":
                tid = await trace_bridge.wait_for_trace_id(
                    run_id, timeout=self.trace_wait_timeout
                )
            if tid is None:
                tid = await self._phoenix_trace_id(run_id)
        if not tid:
            raise AgentRunError(
                f"trace_id not found for run {run_id}: in-process bridge and "
                "Phoenix fallback both missed. Ensure trace_enabled=true and the "
                "RunTraceBridge is installed (§14.1.2).",
                run_ids=run_ids,
                status="unknown",
            )
        return tid

    @staticmethod
    def _trace_enabled() -> bool:
        try:
            from app.observability.tracer import is_trace_enabled

            return bool(is_trace_enabled())
        except Exception:  # noqa: BLE001
            return False

    async def _phoenix_trace_id(self, run_id: str) -> str | None:
        """按 span 的 run_id 找 trace_id。

        Phoenix 的 dataframe **没有** ``attributes`` 列: 属性是 ``attributes.<name>``
        摊平列, 而宿主的点号键被收进 ``attributes.agenthub`` 这个嵌套 dict。所以既
        不能判 ``"attributes" in df.columns``, 也不能 ``a.get("agenthub.run_id")``
        —— 两种写法都会永远取不到 (这就是本函数此前必定返回 None 的原因)。
        """
        try:
            from phoenix.client import Client as PhoenixClient

            from app.config import get_settings

            settings = get_settings()
            client = PhoenixClient(base_url=settings.phoenix_ui_url)
            for _ in range(2):  # BatchSpanProcessor 异步导出 → 重试一次
                df = await asyncio.to_thread(
                    lambda: client.spans.get_spans_dataframe(project_name="default")
                )
                if df is not None and not df.empty:
                    for record in df.to_dict("records"):
                        if self._record_run_id(record) == run_id:
                            return str(record.get("context.trace_id") or "") or None
                await asyncio.sleep(2.0)
        except Exception as e:  # noqa: BLE001 - Phoenix 不可用不阻断, 由上层定夺
            logger.warning("Phoenix trace_id fallback failed: %s", e)
        return None

    @staticmethod
    def _record_run_id(record: dict[str, Any]) -> str:
        """从一行 span 记录里取出 run_id, 兼容嵌套与摊平两种交付形状。"""
        nested = record.get("attributes.agenthub")
        if isinstance(nested, dict):
            value = nested.get("run_id")
            if value:
                return str(value)
        for column in ("attributes.agenthub.run_id", "attributes.run_id"):
            value = record.get(column)
            if value:
                return str(value)
        return ""

    # ── Transcript / 种子文件 ────────────────────────────────────────────

    @staticmethod
    def _normalize_transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """camelCase wire 消息 → transcript 条目 (role/content/parts/...)。"""
        transcript: list[dict[str, Any]] = []
        for m in messages:
            parts = m.get("parts") or []
            transcript.append(
                {
                    "id": m.get("id"),
                    "role": m.get("role"),
                    "agent_id": m.get("agentId"),
                    "content": _extract_text_from_parts(parts),
                    "status": m.get("status"),
                    "run_id": m.get("runId"),
                    "created_at": m.get("createdAt"),
                    "parts": parts,
                }
            )
        return transcript

    @staticmethod
    def _seed_files(view: TaskView) -> dict[str, str]:
        """TaskView.env 声明的种子文件: ``env["files"] = {path: content}``。"""
        env = view.env or {}
        files = env.get("files") or {}
        if not isinstance(files, dict):
            raise AgentRunError(
                "TaskView.env['files'] must be a {path: content} mapping",
                status="error",
            )
        return {str(k): str(v) for k, v in files.items()}

    async def _collect_listing(self, conversation_id: str) -> dict[str, dict[str, Any]]:
        return await collect_workspace_listing(self.client, conversation_id)

    async def _safe_delete(self, conversation_id: str) -> None:
        try:
            await self.client.delete_conversation(conversation_id)
        except Exception as e:  # noqa: BLE001 - 清理失败仅告警
            logger.warning("cleanup of trial conversation %s failed: %s", conversation_id, e)
