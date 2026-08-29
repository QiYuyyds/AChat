"""
Core data types for the Aeval evaluation framework.

This module defines all the data models used throughout the framework:
- Task definition layer: EvalTask, EvalSuite, GraderConfig
- Run result layer: TrialResult, GraderResult, TaskSummary, RunSummary, RunResult
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Task Definition Layer ───────────────────────────────────────────────────


class GraderType(str, Enum):
    """评分器类型枚举"""

    CODE = "code"  # 确定性评分 (字符串匹配/正则/静态分析)
    MODEL = "model"  # LLM Judge
    STATE = "state"  # 环境状态检查
    TOOL_CALLS = "tool_calls"  # 工具调用验证
    TRANSCRIPT = "transcript"  # 转录记录分析
    ARTIFACT = "artifact"  # 产物检查
    METRIC = "metric"  # LLM 输出质量指标 (AnswerRelevancy/Faithfulness/...)
    CUSTOM = "custom"  # 自定义


class ScoreStrategy(str, Enum):
    """评分聚合策略"""

    ALL_PASS = "all_pass"  # 所有 grader 必须通过
    WEIGHTED = "weighted"  # 加权平均
    HYBRID = "hybrid"  # required 必须通过 + 非 required 加权


class GraderConfig(BaseModel):
    """单个评分器的配置"""

    type: GraderType
    name: str = Field(
        ...,
        min_length=1,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",
        description="评分器名称 (用于注册/查找)",
    )
    weight: float = Field(1.0, ge=0.0, description="权重 (用于加权评分)")
    required: bool = Field(False, description="是否必须通过")
    sample_count: int = Field(
        1, ge=1, le=10, description="采样次数 (LLM Judge 多采样计算 confidence)"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="依赖的其他 grader 名称 (拓扑排序, 依赖未通过则跳过)",
    )
    config: dict[str, Any] = Field(default_factory=dict, description="类型特定的配置")


class EvalTask(BaseModel):
    """单个评测任务"""

    id: str = Field(..., min_length=1, description="唯一标识")
    description: str = Field("", description="人类可读描述")
    prompt: str = Field(..., description="给 Agent 的输入")
    graders: list[GraderConfig] = Field(..., min_length=1, description="评分器列表")
    env: dict[str, Any] = Field(default_factory=dict, description="环境参数 (透传给 AgentRunner)")
    max_trials: int = Field(3, ge=1, description="默认 trial 数")
    score_strategy: ScoreStrategy = Field(ScoreStrategy.HYBRID, description="评分聚合策略")
    score_threshold: float = Field(0.7, ge=0.0, le=1.0, description="通过阈值 (用于 WEIGHTED/HYBRID)")
    tracked_metrics: list[str] = Field(
        default_factory=lambda: [
            "n_turns",
            "n_toolcalls",
            "n_total_tokens",
            "latency_ms",
        ],
        description="从 trace 提取的过程指标",
    )

    def get_grader_config(self, name: str) -> dict[str, Any]:
        """获取指定名称的评分器配置"""
        for g in self.graders:
            if g.name == name:
                return g.config
        return {}


class EvalSuite(BaseModel):
    """评测套件 (一组任务)"""

    name: str = Field(..., description="套件名称")
    description: str = Field("", description="套件描述")
    version: str = Field(
        "1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="语义化版本 (semver)",
    )
    tasks: list[EvalTask] = Field(..., min_length=1, description="任务列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="自定义元数据")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Suite name cannot be empty")
        if len(v) > 128:
            raise ValueError("Suite name too long (max 128 chars)")
        return v

    @model_validator(mode="after")
    def _validate_task_ids_unique(self) -> "EvalSuite":
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            duplicates = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"Duplicate task IDs: {duplicates}")
        return self

    @classmethod
    def from_yaml(cls, path: str) -> "EvalSuite":
        """从 YAML 文件加载评测套件 (含严格校验, 错误带文件路径上下文)"""
        from eval_harness.core.suite import load_suite

        return load_suite(path)


# ─── Run Result Layer ─────────────────────────────────────────────────────────


class GraderResult(BaseModel):
    """单个评分器的评分结果"""

    grader_name: str
    grader_type: GraderType
    score: float = Field(..., ge=0.0, le=1.0, description="评分 0.0-1.0")
    passed: bool = Field(..., description="是否通过")
    explanation: str = Field("", description="评分理由")
    details: dict[str, Any] = Field(default_factory=dict, description="类型特定的详情")
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="置信度 (多采样时 = 1 - 不确定性)"
    )
    uncertainty: float = Field(
        0.0, ge=0.0, le=1.0, description="不确定性 (多采样极差的一半)"
    )
    sample_count: int = Field(1, ge=1, description="评分采样次数")
    duration_ms: float = Field(0.0, description="评分耗时 (毫秒)")


class TrialResult(BaseModel):
    """单次 trial 的完整结果"""

    trial_index: int = Field(..., ge=0, description="第几次 trial (0-based)")
    trace_id: str = Field("", description="OTel trace ID")
    success: bool = Field(True, description="最终是否成功")
    grader_results: list[GraderResult] = Field(default_factory=list, description="各 grader 的评分")
    metrics: dict[str, float] = Field(default_factory=dict, description="过程指标")
    transcript: list[dict[str, Any]] = Field(default_factory=list, description="完整对话记录")
    outcome: dict[str, Any] = Field(default_factory=dict, description="环境最终状态")
    duration_ms: float = Field(0.0, description="总耗时 (毫秒)")
    error: str | None = Field(None, description="错误信息 (如果失败)")

    def avg_score(self) -> float:
        """计算所有 grader 的平均分"""
        if not self.grader_results:
            return 0.0
        return sum(r.score for r in self.grader_results) / len(self.grader_results)


class TaskSummary(BaseModel):
    """单个任务的汇总 (跨 trials)"""

    task_id: str
    task_description: str = ""
    total_trials: int
    pass_at_k: dict[int, float] = Field(default_factory=dict, description="{k: rate}")
    pass_power_k: dict[int, float] = Field(default_factory=dict, description="{k: rate}")
    avg_score: float = Field(0.0, description="所有 trial 的平均分")
    avg_metrics: dict[str, float] = Field(default_factory=dict, description="平均过程指标")
    failures: list[int] = Field(default_factory=list, description="失败的 trial 索引")
    pending_trials: list[int] = Field(
        default_factory=list, description="等待人工评分的 trial 索引 (不计入通过率)"
    )
    consistent: bool = Field(True, description="trial 间分数是否一致 (std < 0.2)")
    score_std_dev: float = Field(0.0, description="trial 间分数标准差")


class RunSummary(BaseModel):
    """一次 suite 运行的汇总"""

    total_tasks: int
    total_trials: int
    pass_at_k: dict[int, float] = Field(default_factory=dict, description="全局 pass@k")
    pass_power_k: dict[int, float] = Field(default_factory=dict, description="全局 pass^k")
    avg_score: float = Field(0.0, description="全局平均分")
    avg_metrics: dict[str, float] = Field(default_factory=dict, description="全局平均指标")
    task_summaries: list[TaskSummary] = Field(default_factory=list, description="每个任务的汇总")
    failures: list[str] = Field(default_factory=list, description="未通过的任务 ID")
    saturation: dict[str, Any] = Field(
        default_factory=dict,
        description="饱和度检测结果 (is_saturated/saturation_ratio/recommendation)",
    )


class RunResult(BaseModel):
    """一次 suite 运行的完整结果"""

    run_id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    suite_name: str = ""
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    started_at: float = Field(default_factory=lambda: time.time() * 1000)
    completed_at: float | None = None
    trials: dict[str, list[TrialResult]] = Field(
        default_factory=dict, description="task_id → trials"
    )
    summary: RunSummary | None = None
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        """运行总耗时 (毫秒)"""
        if self.completed_at is not None:
            return self.completed_at - self.started_at
        return None
