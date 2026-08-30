"""Metric report rendering — RunResult / BatchEvaluationResult → Markdown or JSON text.

纯渲染函数 (无 I/O、无模板依赖): 批量报告含逐条分数与汇总统计; run 报告含
pass@k / pass^k / 任务分解。可直接写入 stdout 或文件, 供 CI 脚本与 Phase 2
CLI 消费 (交互视图由 Dashboard 覆盖, 不做 HTML/图表)。
"""

from __future__ import annotations

import json
from typing import Literal

from agent_eval.core.types import RunResult
from agent_eval.metrics.batch_evaluation import BatchEvaluationResult

ReportFormat = Literal["markdown", "json"]


def render_batch_report(
    result: BatchEvaluationResult,
    fmt: ReportFormat = "markdown",
) -> str:
    """渲染批量评测结果 (markdown: 分数表 + 汇总段; json: 纯 dump)。"""
    if fmt == "json":
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    if fmt != "markdown":
        raise ValueError(f"Unsupported report format: {fmt!r} (use 'markdown' or 'json')")
    return _render_batch_markdown(result)


def render_run_report(run: RunResult, fmt: ReportFormat = "markdown") -> str:
    """渲染 run 结果 (markdown: 汇总 + 任务分解; json: 纯 dump)。"""
    if fmt == "json":
        return json.dumps(run.model_dump(), ensure_ascii=False, indent=2)
    if fmt != "markdown":
        raise ValueError(f"Unsupported report format: {fmt!r} (use 'markdown' or 'json')")
    return _render_run_markdown(run)


# ─── Markdown 渲染 ───────────────────────────────────────────────────────────


def _score(value: float) -> str:
    return f"{value:.4f}"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _render_batch_markdown(result: BatchEvaluationResult) -> str:
    lines: list[str] = ["# Aeval 批量评测报告", "", "## 概览", ""]

    total = result.pass_count + result.fail_count
    lines.append(f"- 用例数: {total}")
    lines.append(f"- 通过 / 失败: {result.pass_count} / {result.fail_count}")
    lines.append(f"- pass_rate: {_pct(result.pass_rate)}")
    lines.append("")

    lines += ["## 指标汇总", ""]
    if result.summary:
        lines.append("| 指标 | 平均分 | 最小 | 最大 | 通过 | 失败 | pass_rate | 阈值 |")
        lines.append("|------|--------|------|------|------|------|-----------|------|")
        for name, s in result.summary.items():
            lines.append(
                f"| {name} | {_score(s.avg)} | {_score(s.min)} | {_score(s.max)} "
                f"| {s.pass_count} | {s.fail_count} | {_pct(s.pass_rate)} | {_score(s.threshold)} |"
            )
    else:
        lines.append("（无指标数据）")
    lines.append("")

    lines += ["## 逐用例结果", ""]
    if result.results:
        lines.append("| # | 输入 | 各指标分数 | 结果 |")
        lines.append("|---|------|------------|------|")
        for case in result.results:
            parts = []
            for name, s in case.scores.items():
                mark = "PASS" if s.success else "FAIL"
                cell = f"{name}={_score(s.score)} ({mark})"
                if s.error:
                    cell += f" [error: {s.error}]"
                parts.append(cell)
            verdict = "PASS" if case.overall_pass else "FAIL"
            lines.append(f"| {case.index} | {case.input} | {' / '.join(parts)} | {verdict} |")
    else:
        lines.append("（无用例）")
    lines.append("")
    return "\n".join(lines)


def _render_run_markdown(run: RunResult) -> str:
    lines: list[str] = ["# Aeval 评测 Run 报告", "", "## 概览", ""]

    lines.append(f"- Run ID: {run.run_id}")
    lines.append(f"- Suite: {run.suite_name}")
    lines.append(f"- 状态: {run.status}")
    if run.duration_ms is not None:
        lines.append(f"- 耗时: {run.duration_ms:.0f} ms")
    lines.append("")

    summary = run.summary
    lines += ["## 汇总", ""]
    if summary is None:
        lines.append("（无汇总 — run 未完成或汇总未计算）")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- 总任务 / 总 trial: {summary.total_tasks} / {summary.total_trials}")
    lines.append(
        "- Pass@k: "
        + ", ".join(
            f"@{k}={_pct(v)}" for k, v in sorted(summary.pass_at_k.items())
        )
    )
    lines.append(
        "- Pass^k: "
        + ", ".join(
            f"@{k}={_pct(v)}" for k, v in sorted(summary.pass_power_k.items())
        )
    )
    lines.append(f"- 平均分: {_score(summary.avg_score)}")
    lines.append("")

    lines += ["## 任务分解", ""]
    if summary.task_summaries:
        lines.append("| 任务 | 描述 | Trials | Pass@1 | Pass^1 | 平均分 | 失败 Trials |")
        lines.append("|------|------|--------|--------|--------|--------|-------------|")
        for ts in summary.task_summaries:
            pass1 = _pct(ts.pass_at_k.get(1, 0.0))
            pow1 = _pct(ts.pass_power_k.get(1, 0.0))
            failures = ", ".join(str(i) for i in ts.failures) if ts.failures else "—"
            lines.append(
                f"| {ts.task_id} | {ts.task_description} | {ts.total_trials} "
                f"| {pass1} | {pow1} | {_score(ts.avg_score)} | {failures} |"
            )
    else:
        lines.append("（无任务汇总）")

    saturation = summary.saturation
    if saturation:
        lines.append("")
        lines.append(
            f"饱和度: is_saturated={saturation.get('is_saturated', False)}, "
            f"ratio={saturation.get('saturation_ratio', 0.0)}"
        )
    lines.append("")
    return "\n".join(lines)
