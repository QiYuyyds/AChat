"""Unit tests for eval_harness.metrics.report — task 5.2 (render snapshots).

两种 fmt (markdown/json) × 两种输入 (批量结果 / run 结果) 的渲染断言,
含空结果边界与非法 fmt。
"""

import json

import pytest

from eval_harness.core.types import (
    GraderResult,
    GraderType,
    RunResult,
    RunSummary,
    TaskSummary,
    TrialResult,
)
from eval_harness.metrics.batch_evaluation import (
    BatchCaseResult,
    BatchEvaluationResult,
    BatchMetricSummary,
    MetricScore,
)
from eval_harness.metrics.report import render_batch_report, render_run_report


def make_batch_result() -> BatchEvaluationResult:
    return BatchEvaluationResult(
        results=[
            BatchCaseResult(
                index=0,
                input="什么是退款政策？",
                scores={
                    "answer_relevancy": MetricScore(
                        name="answer_relevancy",
                        score=0.9,
                        reason="切题",
                        threshold=0.5,
                        success=True,
                    ),
                    "faithfulness": MetricScore(
                        name="faithfulness",
                        score=1.0,
                        reason="忠于上下文",
                        threshold=0.5,
                        success=True,
                    ),
                },
                overall_pass=True,
            ),
            BatchCaseResult(
                index=1,
                input="公司有多少人？",
                scores={
                    "answer_relevancy": MetricScore(
                        name="answer_relevancy",
                        score=0.4,
                        reason="答非所问",
                        threshold=0.5,
                        success=False,
                    ),
                    "faithfulness": MetricScore(
                        name="faithfulness",
                        score=0.0,
                        reason="指标计算失败: boom",
                        threshold=0.5,
                        success=False,
                        error="boom",
                    ),
                },
                overall_pass=False,
            ),
        ],
        summary={
            "answer_relevancy": BatchMetricSummary(
                avg=0.65, min=0.4, max=0.9, pass_count=1, fail_count=1,
                pass_rate=0.5, threshold=0.5,
            ),
            "faithfulness": BatchMetricSummary(
                avg=0.5, min=0.0, max=1.0, pass_count=1, fail_count=1,
                pass_rate=0.5, threshold=0.9,
            ),
        },
        pass_count=1,
        fail_count=1,
        pass_rate=0.5,
    )


def make_run() -> RunResult:
    trial = TrialResult(
        trial_index=0,
        trace_id="trace_1",
        success=True,
        grader_results=[
            GraderResult(
                grader_name="code_based",
                grader_type=GraderType.CODE,
                score=1.0,
                passed=True,
            )
        ],
    )
    run = RunResult(run_id="run_demo123", suite_name="demo-suite", status="completed")
    run.trials = {"t1": [trial]}
    run.summary = RunSummary(
        total_tasks=1,
        total_trials=1,
        pass_at_k={1: 1.0},
        pass_power_k={1: 1.0},
        avg_score=1.0,
        task_summaries=[
            TaskSummary(
                task_id="t1",
                task_description="简单问答",
                total_trials=1,
                pass_at_k={1: 1.0},
                pass_power_k={1: 1.0},
                avg_score=1.0,
            )
        ],
        saturation={"is_saturated": True, "saturation_ratio": 1.0},
    )
    run.completed_at = run.started_at + 1500.0
    return run


# ─── 批量报告 ────────────────────────────────────────────────────────────────


class TestBatchReport:
    def test_markdown_snapshot(self):
        text = render_batch_report(make_batch_result())

        assert text.startswith("# Aeval 批量评测报告")
        # 概览段
        assert "用例数: 2" in text
        assert "通过 / 失败: 1 / 1" in text
        assert "pass_rate: 50.0%" in text
        # 指标汇总表
        assert "| 指标 | 平均分 | 最小 | 最大 | 通过 | 失败 | pass_rate | 阈值 |" in text
        assert "answer_relevancy | 0.6500 | 0.4000 | 0.9000 | 1 | 1 | 50.0% | 0.5000 |" in text
        assert "faithfulness" in text
        # 逐用例分数表 (含输入与失败标记与异常标注)
        assert "什么是退款政策？" in text
        assert "answer_relevancy=0.9000 (PASS)" in text
        assert "answer_relevancy=0.4000 (FAIL)" in text
        assert "[error: boom]" in text

    def test_json_is_pure_dump(self):
        result = make_batch_result()
        text = render_batch_report(result, fmt="json")

        assert json.loads(text) == result.model_dump()
        assert "什么是退款政策？" in text  # ensure_ascii=False

    def test_empty_batch_boundaries(self):
        empty = BatchEvaluationResult()
        markdown = render_batch_report(empty)
        assert "（无指标数据）" in markdown
        assert "（无用例）" in markdown

        assert json.loads(render_batch_report(empty, fmt="json")) == empty.model_dump()

    def test_invalid_fmt_rejected(self):
        with pytest.raises(ValueError):
            render_batch_report(make_batch_result(), fmt="html")


# ─── Run 报告 ────────────────────────────────────────────────────────────────


class TestRunReport:
    def test_markdown_snapshot(self):
        text = render_run_report(make_run())

        assert text.startswith("# Aeval 评测 Run 报告")
        assert "run_demo123" in text
        assert "demo-suite" in text
        assert "状态: completed" in text
        assert "耗时: 1500 ms" in text
        # pass@k / pass^k
        assert "Pass@k: @1=100.0%" in text
        assert "Pass^k: @1=100.0%" in text
        # 任务分解
        assert "## 任务分解" in text
        assert "t1" in text and "简单问答" in text
        assert "饱和度: is_saturated=True" in text

    def test_json_is_pure_dump(self):
        run = make_run()
        text = render_run_report(run, fmt="json")

        # mode="json": JSON 序列化会把 int key (pass@k 的 k) 转为字符串
        assert json.loads(text) == run.model_dump(mode="json")

    def test_run_without_summary_boundary(self):
        run = RunResult(run_id="run_pending", suite_name="s", status="running")
        text = render_run_report(run)
        assert "（无汇总 — run 未完成或汇总未计算）" in text
        assert "run_pending" in text

    def test_invalid_fmt_rejected(self):
        with pytest.raises(ValueError):
            render_run_report(make_run(), fmt="csv")
