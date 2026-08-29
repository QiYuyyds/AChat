"""
Dataset data models — EvalDatasetItem / EvalDataset with provenance.

A dataset is a curated pool of evaluation items. Each item carries provenance
(source_type / source_ref) and capability metadata; `to_suite()` converts a
dataset into an executable EvalSuite reusing the change-① Suite validation
(single source of truth for validation rules).
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from eval_harness.core.types import EvalSuite, EvalTask, GraderConfig
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


def now_ms() -> float:
    """当前时间 (epoch 毫秒)"""
    return time.time() * 1000


def new_dataset_id() -> str:
    return f"ds_{uuid.uuid4().hex[:12]}"


class SourceType(str, Enum):
    """数据集条目来源类型"""

    MANUAL = "manual"  # 手动编写
    TRACE_MINING = "trace_mining"  # 从真实 trace 挖掘
    LLM_GENERATED = "llm_generated"  # LLM 辅助生成 (含合成数据)
    ADVERSARIAL = "adversarial"  # 对抗样本 (手工构造, 手动的子类)
    REGRESSION = "regression"  # 从 run 失败 trial 提取


class DatasetError(Exception):
    """数据集操作失败 (导入/转换/升版等)。"""


class EvalDatasetItem(BaseModel):
    """评测数据集中的单个条目 (含溯源)"""

    id: str = Field(..., min_length=1, description="条目唯一标识 (数据集内)")
    prompt: str = Field(..., description="给 Agent 的输入")
    description: str = Field("", description="人类可读描述")
    graders: list[GraderConfig] = Field(
        default_factory=list,
        description="评分器配置 (空 = 待补; to_suite 与质量检查会标出)",
    )
    env: dict[str, Any] = Field(default_factory=dict, description="环境参数 (透传)")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="自定义元数据 (能力维度标签放 metadata.capabilities: list[str])",
    )
    source_type: SourceType = Field(SourceType.MANUAL, description="来源类型")
    source_ref: str = Field("", description="来源引用 (trace_id / run_id / 场景等)")
    created_at: float = Field(default_factory=now_ms, description="创建时间 (epoch ms)")


class EvalDataset(BaseModel):
    """评测数据集 — 一组相关的评测条目"""

    id: str = Field(default_factory=new_dataset_id, description="唯一标识")
    name: str = Field(..., min_length=1, description="数据集名称")
    description: str = Field("", description="描述")
    version: str = Field(
        "1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="语义化版本 (semver)",
    )
    tags: list[str] = Field(default_factory=list, description="标签 (分类/筛选)")
    capability_map: dict[str, float] = Field(
        default_factory=dict,
        description="能力维度 → 覆盖度 (0-1), 由 CoverageAnalyzer 更新",
    )
    items: list[EvalDatasetItem] = Field(default_factory=list, description="条目列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="自定义元数据")
    change_log: list[dict[str, Any]] = Field(
        default_factory=list,
        description="版本变更记录 [{version, change_type, note, at, item_count}]",
    )
    created_at: float = Field(default_factory=now_ms, description="创建时间 (epoch ms)")
    updated_at: float = Field(default_factory=now_ms, description="更新时间 (epoch ms)")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Dataset name cannot be empty")
        if len(v) > 128:
            raise ValueError("Dataset name too long (max 128 chars)")
        return v

    @model_validator(mode="after")
    def _validate_item_ids_unique(self) -> EvalDataset:
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            duplicates = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"Duplicate item IDs: {duplicates}")
        return self

    def get_item(self, item_id: str) -> EvalDatasetItem | None:
        """按 ID 获取条目"""
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def to_suite(self, name: str | None = None) -> EvalSuite:
        """
        转换为可执行的评测 Suite。

        条目 → 任务 (prompt/graders/env 直传); suite 元数据记录数据集
        ID 与版本, 使 run 结果可关联回数据集版本。复用 EvalSuite 的
        校验器 (任务 ID 唯一 / 至少一个任务 / grader 配置合法),
        非法条目 (缺 prompt/graders、ID 重复等) 拒绝转换并给出明确错误。
        """
        try:
            tasks = [
                EvalTask(
                    id=item.id,
                    description=item.description,
                    prompt=item.prompt,
                    graders=item.graders,
                    env=item.env,
                )
                for item in self.items
            ]
            return EvalSuite(
                name=name or self.name,
                description=self.description,
                tasks=tasks,
                metadata={
                    **self.metadata,
                    "dataset_id": self.id,
                    "dataset_version": self.version,
                },
            )
        except ValidationError as e:
            raise DatasetError(
                f"Dataset '{self.name}' (v{self.version}) → Suite conversion "
                f"failed — fix the items below and retry:\n{e}"
            ) from e


# grader 类型 → 内置注册名默认值 (LLM 生成/导入缺 name 时保证可被 runner 解析)
_TYPE_DEFAULT_NAMES: dict[str, str] = {
    "code": "code_based",
    "model": "model_based",
    "state": "state_check",
    "tool_calls": "tool_calls",
    "transcript": "transcript",
    "artifact": "artifact_check",
    "human": "human",
}


def make_grader_config(
    grader_type: str,
    name: str | None = None,
    **config: Any,
) -> GraderConfig:
    """
    便捷构造 GraderConfig (数据源/生成器使用)。

    Args:
        grader_type: grader 类型字符串 (如 "model" / "metric" / "tool_calls")
        name: grader 名称; 缺省时 metric 用 metric_name (无则 "metric"),
              其余类型映射到内置注册名 (model → model_based 等)
        **config: 类型特定配置 (如 metric_name/threshold/rubric)

    Raises:
        DatasetError: 类型不合法或名称不合法 (含具体字段信息)
    """
    from eval_harness.core.types import GraderType

    try:
        grader_t = GraderType(grader_type)
    except ValueError:
        valid = ", ".join(t.value for t in GraderType)
        raise DatasetError(
            f"Invalid grader type '{grader_type}' (valid: {valid})"
        ) from None

    if name is None:
        if grader_t == GraderType.METRIC:
            name = config.get("metric_name", "metric")
        else:
            name = _TYPE_DEFAULT_NAMES.get(grader_t.value, grader_t.value)

    try:
        return GraderConfig(type=grader_t, name=name, config=config)
    except ValidationError as e:
        raise DatasetError(f"Invalid grader config (name={name!r}):\n{e}") from e
