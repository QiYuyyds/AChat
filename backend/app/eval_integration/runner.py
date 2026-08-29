"""AChatAgentRunner — AgentRunner 契约的 AChat 实现 (任务 2.1)。

流程 (对照表 §14.1 已核对):
    1. POST /api/conversations         → 全新 sandbox 会话 (服务端默认)
    2. GET  fs/listdir                 → 种子前清单 (共享目录退化防御基线)
    3. POST fs/write (task.env.files)  → 写入种子文件
    4. GET  fs/listdir                 → 种子后基线清单 (verify_clean 基线)
    5. 订阅 event_bus → POST messages  → 先订阅再发送 (防丢快速失败的事件),
                                          取 runIds
    6. 等待完成                        → 进程内 RunEndEvent (主) / HTTP 消息
                                          状态轮询 (降级), 含超时
    7. GET messages                    → transcript
    8. trace_id                        → 进程内 SpanProcessor 桥 (主) /
                                          Phoenix 属性过滤 (降级)
    9. outcome                         → fs 递归读 (有界) + artifacts 清单

WorkspaceCoordinator 是 runner 与 AChatWorkspaceEnvironment 之间的共享
trial 状态单元: runner 发布会话与基线清单, 环境管理器据此做快照/校验/恢复。
未装配环境管理器时 runner 自行清理会话 (cleanup_conversations)。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from eval_harness.core.contract import TransientError
from eval_harness.core.types import EvalTask

from eval_integration.client import AChatApiClient
from eval_integration.errors import AgentRunError
from eval_integration import trace_bridge

logger = logging.getLogger(__name__)

CompletionChannel = Literal["auto", "in_process", "http"]

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

    runner (种子前后/末期基线) 与环境管理器 (teardown 兜底) 共用。
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
    """经 AChat HTTP API 执行评测任务, 返回 (trace_id, transcript, outcome)。"""

    def __init__(
        self,
        client: AChatApiClient,
        agent_id: str,
        *,
        run_timeout: float = 300.0,
        poll_interval: float = 2.0,
        completion_channel: CompletionChannel = "auto",
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
            coordinator: 与环境管理器共享的 trial 状态单元
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

    async def run(self, task: EvalTask) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        started = time.monotonic()

        conversation_id = await self.client.create_conversation(
            title=f"{self.conversation_title_prefix} {task.id}".strip(),
            agent_id=self.agent_id,
        )
        trial = self.coordinator.begin(conversation_id) if self.coordinator else None

        try:
            pre_seed = await self._collect_listing(conversation_id)
            if trial is not None:
                trial.pre_seed_files = pre_seed

            seeds = self._seed_files(task)
            for path in sorted(seeds):
                await self.client.fs_write(conversation_id, path, seeds[path])

            post_seed = await self._collect_listing(conversation_id)
            if trial is not None:
                trial.post_seed_listing = post_seed

            run_ids = await self._send_and_wait(conversation_id, task.prompt, started)

            transcript = self._normalize_transcript(
                await self.client.list_messages(conversation_id)
            )
            trace_id = await self._resolve_trace_id(run_ids)

            outcome_files = await self._collect_outcome_files(conversation_id)
            artifacts = await self.client.list_artifacts(conversation_id)
            outcome: dict[str, Any] = {
                "conversation_id": conversation_id,
                "run_ids": run_ids,
                "files": outcome_files,
                "artifacts": artifacts,
                "seed_files": sorted(seeds),
            }
            if trace_id == "":
                outcome["trace_id_unavailable"] = (
                    "tracing disabled — trace channel explicitly off (§14.1.2)"
                )

            if trial is not None:
                trial.final_listing = await self._collect_listing(conversation_id)
            return trace_id, transcript, outcome

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

    # ── Send + completion ────────────────────────────────────────────────

    async def _send_and_wait(
        self, conversation_id: str, prompt: str, started: float
    ) -> list[str]:
        """发送 prompt 并等待完成, 返回 run_ids。

        进程内通道先订阅 event_bus 再发送 (防丢快速失败 run 的 RunEndEvent);
        发送前失败 (订阅不可用等) 自动降级 HTTP 轮询; 发送后失败不重发
        (重复发送有副作用), 直接上抛。
        """
        channel = self.completion_channel
        if channel == "auto":
            channel = "in_process" if self._event_bus_available() else "http"

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

    @staticmethod
    def _event_bus_available() -> bool:
        try:
            from app.services.event_bus import event_bus  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    # ── trace_id ─────────────────────────────────────────────────────────

    async def _resolve_trace_id(self, run_ids: list[str]) -> str:
        """进程内桥优先, Phoenix 属性过滤降级; 明确失败而非静默空值 (§14.1.2)。

        ``trace_enabled=False`` 时 trace 通道按配置显式关闭 — 返回空串并在
        outcome 记录原因 (不判 trial 失败)。
        """
        if not self._trace_enabled():
            return ""

        run_id = run_ids[0] if run_ids else ""
        if not run_id:
            raise AgentRunError("no run ids to resolve trace_id for", status="error")

        if self._trace_resolver is not None:
            tid = await self._trace_resolver(run_id)
        else:
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
        """按 span 的 run_id 属性过滤 Phoenix span 表 (裸名 run_id / 约定 agenthub.run_id)。"""
        try:
            from phoenix.client import Client as PhoenixClient

            from app.config import get_settings

            settings = get_settings()
            client = PhoenixClient(base_url=settings.phoenix_ui_url)
            for _ in range(2):  # BatchSpanProcessor 异步导出 → 重试一次
                df = await asyncio.to_thread(
                    lambda: client.spans.get_spans_dataframe(project_name="default")
                )
                if df is not None and not df.empty and "attributes" in df.columns:
                    mask = df["attributes"].apply(
                        lambda a: isinstance(a, dict)
                        and (a.get("agenthub.run_id") == run_id or a.get("run_id") == run_id)
                    )
                    rows = df[mask]
                    if not rows.empty:
                        return str(rows.iloc[0].get("context.trace_id", "") or "") or None
                await asyncio.sleep(2.0)
        except Exception as e:  # noqa: BLE001 - Phoenix 不可用不阻断, 由上层定夺
            logger.warning("Phoenix trace_id fallback failed: %s", e)
        return None

    # ── Transcript / outcome ─────────────────────────────────────────────

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
    def _seed_files(task: EvalTask) -> dict[str, str]:
        """EvalTask.env 声明的种子文件: ``env["files"] = {path: content}``。"""
        env = task.env or {}
        files = env.get("files") or {}
        if not isinstance(files, dict):
            raise AgentRunError(
                "EvalTask.env['files'] must be a {path: content} mapping",
                status="error",
            )
        return {str(k): str(v) for k, v in files.items()}

    async def _collect_listing(self, conversation_id: str) -> dict[str, dict[str, Any]]:
        return await collect_workspace_listing(self.client, conversation_id)

    async def _collect_outcome_files(self, conversation_id: str) -> dict[str, str]:
        """读回 workspace 全部 (有界) 文件内容作为 outcome。"""
        files: dict[str, str] = {}
        listing = await self._collect_listing(conversation_id)
        for rel, info in sorted(listing.items()):
            if info.get("isDirectory") or len(files) >= _MAX_OUTCOME_FILES:
                continue
            if (info.get("size") or 0) > _MAX_OUTCOME_FILE_BYTES:
                files[rel] = "(skipped: file too large)"
                continue
            try:
                data = await self.client.fs_read(conversation_id, rel)
                files[rel] = str(data.get("content", ""))
            except Exception as e:  # noqa: BLE001 - 单文件读取失败不阻断
                files[rel] = f"(read failed: {e})"
        return files

    async def _safe_delete(self, conversation_id: str) -> None:
        try:
            await self.client.delete_conversation(conversation_id)
        except Exception as e:  # noqa: BLE001 - 清理失败仅告警
            logger.warning("cleanup of trial conversation %s failed: %s", conversation_id, e)
