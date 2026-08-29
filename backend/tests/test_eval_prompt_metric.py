"""Unit tests for eval_harness.metrics.prompt_metric — task 3.2 (stub llm_fn)."""

import json

import pytest

from eval_harness.metrics.answer_relevancy import AnswerRelevancyMetric
from eval_harness.metrics.llm_judge import LLMNotConfiguredError
from eval_harness.metrics.prompt_metric import (
    PromptComparisonResult,
    PromptMetric,
    PromptTemplateError,
    PromptVariant,
)


def make_judge_llm():
    """按渲染 prompt 中的变体标记返回分数的 stub judge, 记录调用。"""
    calls: list[tuple[str, str]] = []

    async def judge(system: str, user: str) -> str:
        calls.append((system, user))
        score = 0.9 if "VARIANT_A" in user else 0.5
        return json.dumps({"score": score, "reason": "stub"}, ensure_ascii=False)

    judge.calls = calls  # type: ignore[attr-defined]
    return judge


def make_gen_llm():
    """生成 stub: 返回带序号的输出, 记录 (system, user) 调用。"""
    calls: list[tuple[str, str]] = []
    counter = {"n": 0}

    async def gen(system: str, user: str) -> str:
        calls.append((system, user))
        counter["n"] += 1
        return f"gen-{counter['n']}"

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def make_prompt_metric(gen_llm, judge_llm) -> PromptMetric:
    return PromptMetric(
        variants=[
            PromptVariant(name="A", template="VARIANT_A: {question}"),
            PromptVariant(name="B", template="VARIANT_B: {question}"),
        ],
        metrics=[AnswerRelevancyMetric(llm_fn=judge_llm, threshold=0.5)],
        llm_fn=gen_llm,
    )


class TestCompare:
    async def test_two_variants_avg_and_single_winner(self):
        gen, judge = make_gen_llm(), make_judge_llm()
        pm = make_prompt_metric(gen, judge)

        results = await pm.compare(context={"question": "什么是退款政策？"}, n_trials=3)

        assert [r.variant_name for r in results] == ["A", "B"]
        # 各变体 3 次试验平均分
        assert results[0].metric_scores["answer_relevancy"] == pytest.approx(0.9)
        assert results[1].metric_scores["answer_relevancy"] == pytest.approx(0.5)
        # 恰有一个 winner (求和最大者)
        assert [r.winner for r in results] == [True, False]

        # 变体明细保留: 3 trials, 逐 trial 分数与生成输出
        assert len(results[0].trials) == 3
        assert results[0].trials[0].scores == {"answer_relevancy": 0.9}
        assert results[0].trials[0].output == "gen-1"
        assert results[1].trials[2].output == "gen-6"

        # 生成: 渲染后的变体作为 system prompt (user 为空串)
        assert "VARIANT_A: 什么是退款政策？" in gen.calls[0][0]
        assert gen.calls[0][1] == ""
        # 打分: judge 的 user prompt 携带渲染 prompt 与生成输出
        assert "VARIANT_A: 什么是退款政策？" in judge.calls[0][1]
        assert "gen-1" in judge.calls[0][1]

    async def test_single_trial(self):
        pm = make_prompt_metric(make_gen_llm(), make_judge_llm())
        results = await pm.compare(context={"question": "q"}, n_trials=1)
        assert len(results[0].trials) == 1

    async def test_missing_template_key_raises_template_error(self):
        gen, judge = make_gen_llm(), make_judge_llm()
        pm = PromptMetric(
            variants=[
                PromptVariant(name="A", template="VARIANT_A: {question}"),
                PromptVariant(name="B", template="VARIANT_B: {question} {missing_key}"),
            ],
            metrics=[AnswerRelevancyMetric(llm_fn=judge)],
            llm_fn=gen,
        )
        with pytest.raises(PromptTemplateError) as exc:
            await pm.compare(context={"question": "q"}, n_trials=1)
        # 错误标明变体与缺失 key (渲染逐变体进行, 发生在打分前)
        assert "B" in str(exc.value)
        assert "missing_key" in str(exc.value)
        # A 渲染成功已生成 1 次; B 渲染失败后整批终止
        assert len(gen.calls) == 1

    async def test_positional_placeholder_is_template_error(self):
        pm = PromptMetric(
            variants=[PromptVariant(name="P", template="{question} {}", )],
            metrics=[AnswerRelevancyMetric(llm_fn=make_judge_llm())],
            llm_fn=make_gen_llm(),
        )
        with pytest.raises(PromptTemplateError):
            await pm.compare(context={"question": "q"}, n_trials=1)

    async def test_missing_llm_fn_raises_config_error(self):
        pm = make_prompt_metric(make_gen_llm(), make_judge_llm())
        pm.llm_fn = None
        with pytest.raises(LLMNotConfiguredError):
            await pm.compare(context={"question": "q"}, n_trials=1)

    async def test_invalid_n_trials_rejected(self):
        pm = make_prompt_metric(make_gen_llm(), make_judge_llm())
        with pytest.raises(ValueError):
            await pm.compare(context={"question": "q"}, n_trials=0)

    def test_empty_variants_or_metrics_rejected(self):
        with pytest.raises(ValueError):
            PromptMetric(variants=[], metrics=[AnswerRelevancyMetric()], llm_fn=None)
        with pytest.raises(ValueError):
            PromptMetric(
                variants=[PromptVariant(name="a", template="x")],
                metrics=[],
                llm_fn=None,
            )


class TestDeclareWinner:
    def test_sum_semantics(self):
        # v1 求和语义: 均分求和最大者获胜 (单指标更高但总分更低者不赢)
        r1 = PromptComparisonResult(
            variant_name="r1", metric_scores={"m1": 0.6, "m2": 0.6}
        )
        r2 = PromptComparisonResult(
            variant_name="r2", metric_scores={"m1": 0.9, "m2": 0.2}
        )
        winner = PromptMetric.declare_winner([r1, r2])

        assert winner is r1
        assert r1.winner is True and r2.winner is False  # 恰一 winner

    def test_tie_takes_first_and_clears_stale_flags(self):
        r1 = PromptComparisonResult(variant_name="r1", metric_scores={"m": 0.5}, winner=True)
        r2 = PromptComparisonResult(variant_name="r2", metric_scores={"m": 0.5})
        winner = PromptMetric.declare_winner([r1, r2])
        assert winner is r1
        assert [r.winner for r in (r1, r2)] == [True, False]

    def test_empty_results_rejected(self):
        with pytest.raises(ValueError):
            PromptMetric.declare_winner([])
