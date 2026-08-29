"""Unit tests for eval_harness.metrics.batch_evaluation — task 1.2 (stub llm_fn)."""

import asyncio
import json

import pytest

from eval_harness.metrics.answer_relevancy import AnswerRelevancyMetric
from eval_harness.metrics.batch_evaluation import (
    BatchEvaluationRequest,
    BatchEvaluator,
    BatchTestCase,
    UnknownMetricsError,
)
from eval_harness.metrics.faithfulness import FaithfulnessMetric
from eval_harness.metrics.llm_judge import LLMNotConfiguredError


def stub_llm(score: float = 0.9, reason: str = "ok"):
    """恒定分数的 stub llm_fn, 记录调用。"""
    calls: list[tuple[str, str]] = []

    async def llm_fn(system: str, user: str) -> str:
        calls.append((system, user))
        return json.dumps({"score": score, "reason": reason}, ensure_ascii=False)

    llm_fn.calls = calls  # type: ignore[attr-defined]
    return llm_fn


def sequential_llm(*scores: float):
    """按调用次序返回分数, 耗尽后重复最后一个。"""
    responses = [
        json.dumps({"score": s, "reason": f"s{s}"}, ensure_ascii=False) for s in scores
    ]
    calls: list[tuple[str, str]] = []

    async def llm_fn(system: str, user: str) -> str:
        calls.append((system, user))
        if len(responses) > 1:
            return responses.pop(0)
        return responses[0]

    llm_fn.calls = calls  # type: ignore[attr-defined]
    return llm_fn


def make_registry(llm_fn, threshold: float = 0.5) -> dict:
    return {
        "answer_relevancy": AnswerRelevancyMetric(llm_fn=llm_fn, threshold=threshold),
        "faithfulness": FaithfulnessMetric(llm_fn=llm_fn, threshold=threshold),
    }


def make_request(**overrides) -> BatchEvaluationRequest:
    payload = {
        "test_cases": [
            {"input": "q1", "actual_output": "a1", "context": ["doc1"]},
            {"input": "q2", "actual_output": "a2", "context": ["doc1"]},
        ],
        "metrics": ["answer_relevancy", "faithfulness"],
    }
    payload.update(overrides)
    return BatchEvaluationRequest(**payload)


# ─── 汇总语义 ────────────────────────────────────────────────────────────────


class TestSummary:
    async def test_all_pass(self):
        llm = stub_llm(0.9)
        result = await BatchEvaluator(make_registry(llm)).evaluate(make_request())

        assert result.pass_count == 2
        assert result.fail_count == 0
        assert result.pass_rate == 1.0

        # 每条用例含两指标的 score/success/reason, 且保持输入顺序
        assert [r.input for r in result.results] == ["q1", "q2"]
        for case in result.results:
            assert set(case.scores) == {"answer_relevancy", "faithfulness"}
            for score in case.scores.values():
                assert score.score == 0.9
                assert score.success is True
                assert score.reason == "ok"
            assert case.overall_pass is True

        # 汇总含各指标平均分
        assert set(result.summary) == {"answer_relevancy", "faithfulness"}
        for s in result.summary.values():
            assert s.avg == 0.9
            assert s.pass_count == 2 and s.fail_count == 0

    async def test_partial_pass(self):
        # 单指标两用例: 第 1 条 0.9 (pass), 第 2 条 0.2 (fail)
        # (调用次序 = 用例声明次序 — 每次调用先同步记序再 await)
        llm = sequential_llm(0.9, 0.2)
        result = await BatchEvaluator(make_registry(llm)).evaluate(
            make_request(metrics=["answer_relevancy"])
        )

        assert result.pass_count == 1
        assert result.fail_count == 1
        assert result.pass_rate == 0.5
        assert result.results[0].overall_pass is True
        assert result.results[1].overall_pass is False
        # 平均分 (0.9 + 0.2) / 2
        assert result.summary["answer_relevancy"].avg == pytest.approx(0.55)
        assert result.summary["answer_relevancy"].min == 0.2
        assert result.summary["answer_relevancy"].max == 0.9

    async def test_empty_cases(self):
        llm = stub_llm()
        result = await BatchEvaluator(make_registry(llm)).evaluate(
            make_request(test_cases=[])
        )
        assert result.results == []
        assert result.summary == {}
        assert result.pass_count == 0 and result.fail_count == 0
        assert result.pass_rate == 0.0
        assert llm.calls == []  # 无用例 → 零 LLM 调用


# ─── 解析前置与配置错误 ──────────────────────────────────────────────────────


class TestResolutionAndConfig:
    async def test_unknown_metrics_error_without_llm_calls(self):
        llm = stub_llm()
        with pytest.raises(UnknownMetricsError) as exc:
            await BatchEvaluator(make_registry(llm)).evaluate(
                make_request(metrics=["answer_relevancy", "nope"])
            )
        assert exc.value.unknown == ["nope"]
        assert "nope" in str(exc.value)
        assert llm.calls == []  # 解析前置: 未发任何 LLM 调用

    async def test_unknown_metrics_checked_before_llm_config(self):
        # 未注册名先报 (即使 LLM 也未注入, 解析错误优先且携带无效名列表)
        with pytest.raises(UnknownMetricsError):
            await BatchEvaluator(make_registry(None)).evaluate(
                make_request(metrics=["ghost"])
            )

    async def test_missing_llm_fn_raises_explicitly(self):
        evaluator = BatchEvaluator(make_registry(None), llm_fn=None)
        with pytest.raises(LLMNotConfiguredError):
            await evaluator.evaluate(make_request())

    async def test_evaluator_llm_injected_into_unconfigured_metrics(self):
        # 注册表指标未持有 llm_fn, BatchEvaluator 注入自身 llm_fn (与 EvalRunner 同约定)
        evaluator = BatchEvaluator(make_registry(None), llm_fn=stub_llm(0.8))
        result = await evaluator.evaluate(make_request())
        assert result.pass_rate == 1.0


# ─── 单条异常隔离 ────────────────────────────────────────────────────────────


class TestIsolation:
    async def test_metric_exception_recorded_in_case_result(self):
        # answer_relevancy 的 stub 恒返回垃圾 → LLMJudgeError (重试用尽);
        # faithfulness 正常 → 整批完成, 异常记入该条该指标
        broken_registry = {
            "answer_relevancy": AnswerRelevancyMetric(
                llm_fn=_garbage_llm(), threshold=0.5
            ),
            "faithfulness": FaithfulnessMetric(llm_fn=stub_llm(0.9), threshold=0.5),
        }
        result = await BatchEvaluator(broken_registry).evaluate(make_request())

        assert len(result.results) == 2  # 整批未中断
        for case in result.results:
            ar = case.scores["answer_relevancy"]
            assert ar.score == 0.0
            assert ar.success is False
            assert ar.error is not None
            assert "指标计算失败" in ar.reason
            assert case.scores["faithfulness"].success is True
            assert case.overall_pass is False

    async def test_missing_context_case_handled_by_metric_semantics(self):
        # 用例未提供 context 且指标为 faithfulness → 按其语义 score=0 + 明确理由,
        # 不中断整批 (spec 场景)
        llm = stub_llm(1.0)
        result = await BatchEvaluator(make_registry(llm)).evaluate(
            make_request(
                test_cases=[
                    {"input": "q1", "actual_output": "a1"},  # 无 context
                    {"input": "q2", "actual_output": "a2", "context": ["doc"]},
                ]
            )
        )
        first_faith = result.results[0].scores["faithfulness"]
        assert first_faith.score == 0.0
        assert first_faith.success is False
        assert "无上下文" in first_faith.reason
        assert first_faith.error is None  # 语义失败而非异常
        assert result.results[1].overall_pass is True  # 其余计算不受影响


def _garbage_llm():
    async def llm_fn(system: str, user: str) -> str:
        return "this is not json at all"

    return llm_fn


# ─── thresholds 覆盖 ────────────────────────────────────────────────────────


class TestThresholds:
    async def test_explicit_threshold_overrides_default(self):
        llm = stub_llm(0.6)
        result = await BatchEvaluator(make_registry(llm)).evaluate(
            make_request(thresholds={"answer_relevancy": 0.8})
        )

        for case in result.results:
            ar = case.scores["answer_relevancy"]
            assert ar.threshold == 0.8
            assert ar.success is False  # 0.6 < 0.8 (显式阈值优先)
            # 未显式给出的指标沿用 Metric 默认阈值
            faith = case.scores["faithfulness"]
            assert faith.threshold == 0.5
            assert faith.success is True  # 0.6 >= 0.5
            assert case.overall_pass is False

        # summary 记录实际使用阈值
        assert result.summary["answer_relevancy"].threshold == 0.8
        assert result.summary["faithfulness"].threshold == 0.5

    async def test_threshold_boundary_inclusive(self):
        llm = stub_llm(0.8)
        result = await BatchEvaluator(make_registry(llm)).evaluate(
            make_request(metrics=["answer_relevancy"], thresholds={"answer_relevancy": 0.8})
        )
        assert result.results[0].scores["answer_relevancy"].success is True


# ─── 并发上限 ────────────────────────────────────────────────────────────────


class TestConcurrency:
    @staticmethod
    def _tracking_llm(state: dict):
        async def llm_fn(system: str, user: str) -> str:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
            await asyncio.sleep(0.01)
            state["current"] -= 1
            return json.dumps({"score": 0.9, "reason": "ok"})

        return llm_fn

    @staticmethod
    def _many_cases(n: int) -> BatchEvaluationRequest:
        return BatchEvaluationRequest(
            test_cases=[
                BatchTestCase(input=f"q{i}", actual_output="a") for i in range(n)
            ],
            metrics=["answer_relevancy"],
        )

    async def test_concurrency_cap_respected(self):
        state = {"current": 0, "max": 0}
        registry = {
            "answer_relevancy": AnswerRelevancyMetric(
                llm_fn=self._tracking_llm(state), threshold=0.5
            )
        }
        result = await BatchEvaluator(registry, concurrency=2).evaluate(
            self._many_cases(8)
        )
        assert result.pass_count == 8
        assert state["max"] == 2  # 恰好受限并发, 不多不少

    async def test_concurrency_one_is_serial(self):
        state = {"current": 0, "max": 0}
        registry = {
            "answer_relevancy": AnswerRelevancyMetric(
                llm_fn=self._tracking_llm(state), threshold=0.5
            )
        }
        await BatchEvaluator(registry, concurrency=1).evaluate(self._many_cases(4))
        assert state["max"] == 1

    async def test_concurrency_floor_is_one(self):
        # 非法并发数收敛到 1, 不崩溃
        state = {"current": 0, "max": 0}
        registry = {
            "answer_relevancy": AnswerRelevancyMetric(
                llm_fn=self._tracking_llm(state), threshold=0.5
            )
        }
        await BatchEvaluator(registry, concurrency=0).evaluate(self._many_cases(2))
        assert state["max"] == 1
