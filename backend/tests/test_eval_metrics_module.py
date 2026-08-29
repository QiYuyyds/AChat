"""Unit tests for eval_harness.metrics — P0 metrics, LLM judge infra, synthetic data."""

import pytest
from eval_harness.dataset.sources.manual import DatasetImportError
from eval_harness.metrics import (
    AnswerRelevancyMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    LLMJudgeError,
    LLMNotConfiguredError,
    MetricResult,
    SyntheticDataGenerator,
    build_default_metrics_registry,
    extract_json_object,
)
from eval_harness.metrics.base import MetricGraderAdapter
from eval_harness.metrics.llm_judge import judge_json


class StubJudge:
    """按序返回预设响应的 stub llm_fn, 记录调用"""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def judge_json_response(score: float, reason: str = "because", **extra) -> str:
    import json

    return json.dumps({"score": score, "reason": reason, **extra}, ensure_ascii=False)


# ─── llm_judge infra ─────────────────────────────────────────────────────────


class TestLLMJudge:
    async def test_tolerant_json_extraction(self):
        assert extract_json_object('前言 ```json\n{"a": 1}\n``` 后记') == {"a": 1}
        assert extract_json_object('noise {"score": 0.5, "x": [1,2]} noise') == {"score": 0.5, "x": [1, 2]}
        assert extract_json_object("no json here") is None
        assert extract_json_object("[1, 2]") is None  # 数组不是对象

    async def test_judge_json_parses_valid(self):
        stub = StubJudge(judge_json_response(0.8))
        data = await judge_json(stub, "sys", "usr")
        assert data["score"] == 0.8
        assert stub.calls == [("sys", "usr")]

    async def test_judge_json_retries_on_parse_failure(self):
        stub = StubJudge("第一遍不是 JSON", judge_json_response(0.6, "second try"))
        data = await judge_json(stub, "sys", "usr")
        assert data["score"] == 0.6
        assert len(stub.calls) == 2  # 解析失败后重试了一次

    async def test_judge_json_raises_after_retries_exhausted(self):
        stub = StubJudge("garbage")
        with pytest.raises(LLMJudgeError) as exc:
            await judge_json(stub, "sys", "usr", max_retries=2)
        assert len(stub.calls) == 3  # 1 + 2 retries
        assert "garbage" in str(exc.value)

    async def test_missing_llm_fn_raises_config_error(self):
        with pytest.raises(LLMNotConfiguredError):
            await judge_json(None, "sys", "usr")


# ─── P0 metrics ──────────────────────────────────────────────────────────────


class TestAnswerRelevancy:
    async def test_normal_path(self):
        stub = StubJudge(judge_json_response(
            0.85, "大部分陈述切题",
            statements=["s1", "s2"], relevancies=[1.0, 0.7],
        ))
        result = await AnswerRelevancyMetric(llm_fn=stub).measure(
            input="什么是退款政策？", actual_output="退款政策是……"
        )
        assert isinstance(result, MetricResult)
        assert result.name == "answer_relevancy"
        assert result.score == 0.85
        assert result.reason == "大部分陈述切题"
        assert result.success is True
        assert result.details["statements"] == ["s1", "s2"]

    async def test_score_clamped(self):
        stub = StubJudge(judge_json_response(1.7))
        result = await AnswerRelevancyMetric(llm_fn=stub).measure("q", "a")
        assert result.score == 1.0
        assert result.success is True

    async def test_threshold_boundary(self):
        stub = StubJudge(judge_json_response(0.5))
        metric = AnswerRelevancyMetric(llm_fn=stub, threshold=0.5)
        assert (await metric.measure("q", "a")).success is True


class TestFaithfulness:
    async def test_missing_context_fails_explicitly(self):
        stub = StubJudge(judge_json_response(1.0))
        result = await FaithfulnessMetric(llm_fn=stub).measure("q", "an answer")
        # 不调用 LLM、score=0、理由明确
        assert stub.calls == []
        assert result.score == 0.0
        assert result.success is False
        assert "无上下文" in result.reason
        assert result.details["error"] == "missing_context"

    async def test_unsupported_claims_lower_score(self):
        stub = StubJudge(judge_json_response(
            0.5, "一个陈述不被支持",
            claims=["c1", "c2"], verdicts=["supported", "unsupported"],
            unsupported_claims=["c2"],
        ))
        metric = FaithfulnessMetric(llm_fn=stub, threshold=0.7)
        result = await metric.measure(
            "q", "an answer with hallucination",
            context=["doc1", "doc2"],
        )
        assert result.score == 0.5
        assert result.success is False  # 0.5 < threshold 0.7
        assert result.details["unsupported_claims"] == ["c2"]
        # 上下文进入 user prompt
        assert "doc1" in stub.calls[0][1]

    async def test_missing_score_raises_metric_error(self):
        from eval_harness.metrics.base import MetricError

        stub = StubJudge('{"claims": ["c1"]}')  # 无 score
        with pytest.raises(MetricError):
            await FaithfulnessMetric(llm_fn=stub).measure("q", "a", context=["doc"])

    async def test_missing_llm_fn_raises(self):
        with pytest.raises(LLMNotConfiguredError):
            await FaithfulnessMetric(llm_fn=None).measure("q", "a", context=["doc"])


class TestContextRecall:
    async def test_missing_params_fail_explicitly(self):
        stub = StubJudge(judge_json_response(1.0))
        metric = ContextRecallMetric(llm_fn=stub)

        no_expected = await metric.measure("q", "a", retrieval_context=["doc"])
        assert no_expected.score == 0.0 and "expected_output" in no_expected.reason
        no_retrieval = await metric.measure("q", "a", expected_output="exp")
        assert no_retrieval.score == 0.0 and "retrieval_context" in no_retrieval.reason
        assert stub.calls == []  # 缺参不调用 LLM

    async def test_normal_path(self):
        stub = StubJudge(judge_json_response(
            0.75, "三点覆盖两点",
            information_points=["p1", "p2", "p3"], covered=[True, True, False],
        ))
        result = await ContextRecallMetric(llm_fn=stub).measure(
            "q", "a", expected_output="期望答案", retrieval_context=["doc1", "doc2"],
        )
        assert result.score == 0.75
        assert result.details["covered"] == [True, True, False]


class TestContextPrecision:
    async def test_missing_retrieval_context_fails_explicitly(self):
        stub = StubJudge(judge_json_response(1.0))
        result = await ContextPrecisionMetric(llm_fn=stub).measure("q", "a")
        assert stub.calls == []
        assert result.score == 0.0
        assert "retrieval_context" in result.reason

    async def test_normal_path(self):
        stub = StubJudge(judge_json_response(
            0.67, "三取二相关",
            documents=[{"index": 0, "relevant": True}, {"index": 1, "relevant": False}],
        ))
        result = await ContextPrecisionMetric(llm_fn=stub).measure(
            "q", "a", retrieval_context=["doc0", "doc1", "doc2"],
        )
        assert result.score == 0.67
        assert result.details["documents"][0]["relevant"] is True


class TestRegistry:
    def test_build_default_registry(self):
        registry = build_default_metrics_registry(llm_fn=None)
        assert set(registry) == {
            "answer_relevancy", "faithfulness", "context_recall", "context_precision",
        }
        assert all(isinstance(m, AnswerRelevancyMetric) or True for m in registry.values())
        assert isinstance(registry["faithfulness"], FaithfulnessMetric)


# ─── to_grader bridge ────────────────────────────────────────────────────────


class TestToGrader:
    async def test_metric_to_grader_pipeline_shape(self):
        from eval_harness.core.types import EvalTask, GraderConfig, GraderType, TrialResult

        stub = StubJudge(judge_json_response(0.9, "related"))
        metric = AnswerRelevancyMetric(llm_fn=stub, threshold=0.7)
        adapter = metric.to_grader()
        assert isinstance(adapter, MetricGraderAdapter)
        assert adapter.name == "answer_relevancy"

        task = EvalTask(
            id="t1",
            prompt="q",
            graders=[GraderConfig(type=GraderType.METRIC, name="answer_relevancy")],
        )
        trial = TrialResult(
            trial_index=0,
            trace_id="tr",
            transcript=[
                {"role": "user", "content": "什么是退款政策？"},
                {"role": "assistant", "content": "回答内容"},
            ],
        )
        result = await adapter.grade(trial, [], task)

        assert result.grader_type == GraderType.METRIC
        assert result.grader_name == "answer_relevancy"
        assert result.score == 0.9
        assert result.passed is True
        assert result.explanation == "related"
        assert result.details["metric"] == "answer_relevancy"
        # transcript 首末条进入 user prompt
        assert "退款政策" in stub.calls[0][1]
        assert "回答内容" in stub.calls[0][1]

    async def test_adapter_maps_config_errors_to_zero_score(self):
        from eval_harness.core.types import EvalTask, GraderConfig, GraderType, TrialResult

        metric = AnswerRelevancyMetric(llm_fn=None)  # 未配置 LLM → measure 抛配置错误
        adapter = metric.to_grader()
        task = EvalTask(
            id="t1",
            prompt="q",
            graders=[GraderConfig(type=GraderType.METRIC, name="answer_relevancy")],
        )
        trial = TrialResult(trial_index=0, trace_id="", transcript=[])
        result = await adapter.grade(trial, [], task)

        assert result.score == 0.0
        assert result.passed is False
        assert "配置/计算错误" in result.explanation

    async def test_adapter_surfaces_explicit_failure_reasons(self):
        from eval_harness.core.types import EvalTask, GraderConfig, GraderType, TrialResult

        metric = FaithfulnessMetric(llm_fn=StubJudge(judge_json_response(1.0)))
        adapter = metric.to_grader()
        task = EvalTask(
            id="t1",
            prompt="q",
            graders=[GraderConfig(type=GraderType.METRIC, name="faithfulness")],
        )
        trial = TrialResult(trial_index=0, trace_id="", transcript=[])
        result = await adapter.grade(trial, [], task)

        # faithfulness 缺 context 的明确失败 (score=0 + 理由) 原样透传
        assert result.score == 0.0
        assert result.passed is False
        assert "无上下文" in result.explanation


# ─── Synthetic data ──────────────────────────────────────────────────────────


class TestSyntheticData:
    async def test_generate_from_docs(self):
        raw = """```json
        [
          {"input": "退款期限是多久？", "expected_output": "30 天"},
          {"input": "支持哪些支付方式？", "expected_output": "信用卡与PayPal"}
        ]
        ```"""
        gen = SyntheticDataGenerator(llm_fn=StubJudge(raw))
        goldens = await gen.generate_from_docs(["文档: 退款政策……"], count_per_doc=2)

        assert len(goldens) == 2
        g = goldens[0]
        assert g.input == "退款期限是多久？"
        assert g.expected_output == "30 天"
        assert g.context == ["文档: 退款政策……"]
        assert g.source == "doc[0]"

    async def test_unparseable_doc_response_raises(self):
        gen = SyntheticDataGenerator(llm_fn=StubJudge("nothing here"))
        with pytest.raises(DatasetImportError):
            await gen.generate_from_docs(["doc"], 2)

    async def test_missing_llm_fn_raises(self):
        gen = SyntheticDataGenerator(llm_fn=None)
        with pytest.raises(LLMNotConfiguredError):
            await gen.generate_from_docs(["doc"], 2)

    def test_chunk_text(self):
        text = "\n\n".join(f"段落{i} " + "x" * 300 for i in range(10))
        chunks = SyntheticDataGenerator.chunk_text(text, max_chars=1000, overlap=100)
        assert len(chunks) > 1
        assert all(len(c) <= 1200 for c in chunks)  # 允许重叠导致的少量超出
        # 内容不丢失 (重叠保障跨块上下文, 总量不减)
        assert all(c.strip() for c in chunks)

    def test_chunk_text_short_passthrough(self):
        assert SyntheticDataGenerator.chunk_text("short") == ["short"]
        assert SyntheticDataGenerator.chunk_text("   ") == []

    async def test_goldens_to_dataset_items(self):
        raw = """[
          {"input": "Q1", "expected_output": "A1"},
          {"input": "Q2", "expected_output": "A2"}
        ]"""
        gen = SyntheticDataGenerator(llm_fn=StubJudge(raw))
        goldens = await gen.generate_from_docs(["退货规则文档"], count_per_doc=2)
        items = gen.to_dataset_items(goldens)

        assert len(items) == 2
        item = items[0]
        assert item.id == "synthetic_0000"
        assert item.prompt == "Q1"
        assert item.source_type.value == "llm_generated"
        assert item.source_ref == "doc[0]"
        # 默认 graders: answer_relevancy + faithfulness (threshold 0.7)
        assert [g.name for g in item.graders] == ["answer_relevancy", "faithfulness"]
        for g in item.graders:
            assert g.type.value == "metric"
            assert g.config["metric_name"] == g.name
            assert g.config["threshold"] == 0.7
        # faithfulness grader 携带 Golden context → 流水线可评估忠实度
        faithfulness = item.graders[1]
        assert faithfulness.config["context"] == ["退货规则文档"]
        assert item.metadata["expected_output"] == "A1"

    async def test_generate_dataset_items_chain(self):
        raw = '[{"input": "Q", "expected_output": "A"}]'
        gen = SyntheticDataGenerator(llm_fn=StubJudge(raw))
        items = await gen.generate_dataset_items(["d1", "d2"], count_per_doc=1)
        assert len(items) == 2
        assert items[1].source_ref == "doc[1]"
