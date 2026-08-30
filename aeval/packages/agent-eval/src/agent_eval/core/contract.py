"""
Integration contracts for the Aeval evaluation framework.

This module defines the Protocol interfaces that projects must implement
to integrate with Aeval. Only AgentRunner is required; all others have
default implementations.

Contracts:
    - AgentRunner (REQUIRED): Run an agent task and return trace_id + transcript + outcome
    - TraceProvider (optional): Fetch trace spans from a trace backend
    - Grader (optional): Score a single trial result
    - Storage (optional): Persist run/suite results
    - EnvironmentManager (optional): Setup/teardown environment for each trial
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    RunResult,
    TrialResult,
)


# ─── Errors ───────────────────────────────────────────────────────────────────


class TransientError(Exception):
    """
    瞬态错误 — AgentRunner 实现方显式抛出。

    框架仅对 TransientError 做指数退避重试 (默认最多 2 次)。
    httpx 超时/网络抖动等属于实现层知识, 由实现方判断并包装为本异常;
    其他异常 (含 asyncio.TimeoutError) 不重试, 直接记失败。
    """


# ─── Evaluation Context ───────────────────────────────────────────────────────


@dataclass
class EvalContext:
    """
    单次 trial 的评分上下文 — 贯穿评分调用, 在 grader 之间共享。

    Attributes:
        run_id: 所属 run 的 ID
        task: 任务定义
        trial: 本次 trial 的结果 (评分过程中可能被填充)
        spans: trace span 列表
        shared_state: 同一 trial 内各 grader 间共享的可变状态
        grader_config: 当前评分调用的 grader 配置 (runner 每次调用前以
            replace() 注入; 供 name 与配置名不一致的分发型 grader 定位
            自己的配置, 如 MetricGrader)
    """

    run_id: str
    task: EvalTask
    trial: TrialResult
    spans: list[dict[str, Any]] = field(default_factory=list)
    shared_state: dict[str, Any] = field(default_factory=dict)
    grader_config: GraderConfig | None = None


# ─── Required Contract ────────────────────────────────────────────────────────


@runtime_checkable
class AgentRunner(Protocol):
    """
    项目必须实现: 运行 Agent 并返回 trace。

    这是唯一的必选接入点。框架通过这个接口与 Agent 系统交互，
    不需要知道 Agent 的内部实现细节。

    Example:
        class MyAgentRunner:
            async def run(self, task: EvalTask) -> tuple[str, list[dict], dict]:
                # 1. 准备环境
                # 2. 发送 prompt 给 Agent
                # 3. 等待 Agent 完成
                # 4. 收集 trace_id, transcript, outcome
                return trace_id, transcript, outcome
    """

    async def run(
        self,
        task: EvalTask,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """
        执行一个评测任务。

        Args:
            task: 评测任务定义

        Returns:
            tuple: (trace_id, transcript, outcome)
                - trace_id: OTel trace ID
                - transcript: 完整对话记录
                - outcome: 环境最终状态

        Raises:
            AgentRunError: Agent 执行失败 (超时/崩溃/被拦截)
        """
        ...


# ─── Optional Contracts ───────────────────────────────────────────────────────


@runtime_checkable
class TraceProvider(Protocol):
    """
    Trace 数据获取。

    默认提供 Phoenix 实现, 可自定义以支持其他后端 (Jaeger, Tempo, ...)。
    """

    async def get_spans(
        self,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """
        获取一个 trace 的所有 span。

        Args:
            trace_id: OTel trace ID

        Returns:
            span 列表, 每个 span 是 dict, 包含:
                - name: str          # span 名称
                - attributes: dict   # span 属性
                - start_time: str    # 开始时间
                - end_time: str      # 结束时间
                - status: dict       # 状态
        """
        ...

    async def get_trace_ids(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[str]:
        """
        查询 trace ID 列表 (用于历史记录浏览)。

        Args:
            filters: 过滤条件 (时间范围/状态/标签等)
            limit: 返回数量限制

        Returns:
            trace ID 列表
        """
        ...


@runtime_checkable
class Grader(Protocol):
    """
    评分器接口。

    内置 6 个通用实现, 项目可自定义以适配业务逻辑。
    """

    name: str  # 评分器唯一名称

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        """
        对一次 trial 进行评分。

        Args:
            trial: trial 结果 (含 transcript/outcome/metrics)
            spans: trace span 列表 (用于分析过程)
            task: 任务定义 (含 grader config)
            context: 评分上下文 (run_id/task/trial/spans/shared_state)

        Returns:
            GraderResult: 评分结果
        """
        ...


@runtime_checkable
class Storage(Protocol):
    """
    结果持久化。

    默认 SQLite, 可选 PostgreSQL / Memory。
    """

    # ── Run 操作 ──

    async def save_run(self, run: RunResult) -> None:
        """保存运行结果"""
        ...

    async def get_run(self, run_id: str) -> RunResult | None:
        """获取运行结果"""
        ...

    async def list_runs(
        self, suite_name: str | None = None, limit: int = 50
    ) -> list[RunResult]:
        """列出运行历史"""
        ...

    async def delete_run(self, run_id: str) -> bool:
        """删除运行结果"""
        ...

    # ── Suite 操作 ──

    async def save_suite(self, suite: EvalSuite) -> None:
        """保存评测套件"""
        ...

    async def get_suite(self, name: str) -> EvalSuite | None:
        """获取评测套件"""
        ...

    async def list_suites(self) -> list[EvalSuite]:
        """列出所有评测套件"""
        ...

    async def delete_suite(self, name: str) -> bool:
        """删除评测套件"""
        ...

    # ── 人工评分请求 (可选, HumanGrader pending 语义使用) ──

    async def save_human_score_request(self, request: dict[str, Any]) -> None:
        """保存人工评分请求"""
        ...

    async def list_human_score_requests(
        self, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出人工评分请求"""
        ...


@runtime_checkable
class EnvironmentManager(Protocol):
    """
    环境管理。可选, 默认无操作 (NoOpEnvironment)。

    用于:
    - workspace 隔离 (每次 trial 从干净环境开始)
    - 数据准备 (注入测试数据)
    - 资源清理 (删除临时文件/数据库)
    - 泄漏检测 (trial 前后环境一致性校验)
    """

    async def setup(self, task: EvalTask) -> None:
        """trial 开始前: 准备环境"""
        ...

    async def teardown(self, task: EvalTask) -> None:
        """trial 结束后: 清理环境"""
        ...

    async def snapshot(self) -> dict[str, Any]:
        """
        拍摄环境基线快照 (JSON 可序列化)。

        Returns:
            环境状态快照, 传给 verify_clean / restore
        """
        ...

    async def verify_clean(self, baseline: dict[str, Any]) -> dict[str, Any]:
        """
        校验环境是否与基线一致。

        Args:
            baseline: snapshot() 返回的基线快照

        Returns:
            {"clean": bool, "differences": [...]}
        """
        ...

    async def restore(self, baseline: dict[str, Any]) -> None:
        """将环境恢复到基线状态"""
        ...
