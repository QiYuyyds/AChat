"""Dataset quality checks and capability coverage analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_eval.dataset.models import EvalDataset
from agent_eval.dataset.sources.regression import normalize_prompt

# 单条 prompt 长度告警阈值 (字符)
PROMPT_LENGTH_WARN = 10_000
# 每个能力维度视为充分覆盖所需的最少条目数
COVERAGE_FULL_ITEMS = 5.0


@dataclass
class QualityIssue:
    """单条质量问题"""

    code: str  # empty_prompt / missing_graders / duplicate_prompt / long_prompt / duplicate_item_id
    severity: str  # error / warning
    item_id: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "item_id": self.item_id,
            "message": self.message,
        }


@dataclass
class QualityReport:
    """数据集质量报告"""

    total_items: int = 0
    errors: list[QualityIssue] = field(default_factory=list)
    warnings: list[QualityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_items": self.total_items,
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


class DatasetQualityChecker:
    """数据集质量检查 — errors 阻塞转换, warnings 仅提示"""

    def check(self, dataset: EvalDataset) -> QualityReport:
        from agent_eval.dataset.models import EvalDatasetItem

        items = [
            i if isinstance(i, EvalDatasetItem) else EvalDatasetItem(**i)
            for i in dataset.items
        ]
        report = QualityReport(total_items=len(items))

        seen_prompts: dict[str, str] = {}
        seen_ids: dict[str, str] = {}

        for item in items:
            # 1. 空 prompt (error)
            if not item.prompt.strip():
                report.errors.append(QualityIssue(
                    code="empty_prompt", severity="error", item_id=item.id,
                    message=f"Item '{item.id}' has an empty prompt",
                ))

            # 2. 缺 graders (error — to_suite 无法执行)
            if not item.graders:
                report.errors.append(QualityIssue(
                    code="missing_graders", severity="error", item_id=item.id,
                    message=f"Item '{item.id}' has no graders configured",
                ))

            # 3. 重复 prompt (warning, 归一化比较)
            if item.prompt.strip():
                normalized = normalize_prompt(item.prompt)
                if normalized in seen_prompts:
                    report.warnings.append(QualityIssue(
                        code="duplicate_prompt", severity="warning", item_id=item.id,
                        message=(
                            f"Item '{item.id}' duplicates prompt of "
                            f"'{seen_prompts[normalized]}'"
                        ),
                    ))
                else:
                    seen_prompts[normalized] = item.id

            # 4. 超长 prompt (warning)
            if len(item.prompt) > PROMPT_LENGTH_WARN:
                report.warnings.append(QualityIssue(
                    code="long_prompt", severity="warning", item_id=item.id,
                    message=(
                        f"Item '{item.id}' prompt is very long "
                        f"({len(item.prompt)} chars, warn threshold {PROMPT_LENGTH_WARN})"
                    ),
                ))

            # 5. 重复条目 ID (error — to_suite 要求任务 ID 唯一)
            if item.id in seen_ids:
                report.errors.append(QualityIssue(
                    code="duplicate_item_id", severity="error", item_id=item.id,
                    message=f"Item id '{item.id}' is used more than once",
                ))
            else:
                seen_ids[item.id] = item.id

        return report


@dataclass
class CoverageReport:
    """能力维度覆盖度报告"""

    total_items: int = 0
    untagged_items: int = 0
    coverage: dict[str, float] = field(default_factory=dict)  # 维度 → 0-1
    insufficient: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_items": self.total_items,
            "untagged_items": self.untagged_items,
            "coverage": self.coverage,
            "insufficient": self.insufficient,
        }


class CoverageAnalyzer:
    """
    分析数据集对能力维度的覆盖度。

    条目的能力维度标签取 metadata.capabilities (list[str])。
    覆盖度 = min(1, 该维度条目数 / COVERAGE_FULL_ITEMS) — 前几个条目
    贡献最大, 达到 COVERAGE_FULL_ITEMS 个即视为充分覆盖。
    """

    def __init__(self, full_items_per_dim: float = COVERAGE_FULL_ITEMS,
                 insufficient_below: float = 0.6):
        """
        Args:
            full_items_per_dim: 视为充分覆盖的条目数
            insufficient_below: 覆盖度低于该值列入覆盖不足清单
        """
        self.full_items_per_dim = max(1.0, full_items_per_dim)
        self.insufficient_below = insufficient_below

    def analyze(
        self,
        dataset: EvalDataset,
        expected_capabilities: list[str] | None = None,
    ) -> CoverageReport:
        """
        Args:
            dataset: 数据集
            expected_capabilities: 额外要求覆盖的维度 (即使条目未标注也计入报告)
        """
        report = CoverageReport(total_items=len(dataset.items))
        dim_counts: dict[str, int] = {}

        for item in dataset.items:
            caps = item.metadata.get("capabilities", [])
            if not isinstance(caps, list) or not caps:
                report.untagged_items += 1
                continue
            for cap in caps:
                dim_counts[str(cap)] = dim_counts.get(str(cap), 0) + 1

        dims = set(dim_counts) | set(expected_capabilities or [])
        for dim in sorted(dims):
            count = dim_counts.get(dim, 0)
            coverage = min(1.0, count / self.full_items_per_dim)
            report.coverage[dim] = round(coverage, 3)
            if coverage < self.insufficient_below:
                report.insufficient.append({
                    "capability": dim,
                    "item_count": count,
                    "coverage": report.coverage[dim],
                })

        return report
