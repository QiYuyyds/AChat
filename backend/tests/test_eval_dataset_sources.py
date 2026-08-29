"""Unit tests for eval_harness dataset sources / quality / coverage / versioning."""

import pytest
from eval_harness.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    RunResult,
    TrialResult,
)
from eval_harness.dataset.models import DatasetError, EvalDataset, SourceType
from eval_harness.dataset.quality import CoverageAnalyzer, DatasetQualityChecker
from eval_harness.dataset.sources.llm_generator import (
    DatasetGenerationError,
    LLMDatasetGenerator,
)
from eval_harness.dataset.sources.manual import (
    DatasetImportError,
    import_from_content,
    parse_dataset_payload,
)
from eval_harness.dataset.sources.regression import (
    RegressionExtractor,
    normalize_prompt,
)
from eval_harness.dataset.sources.trace_mining import TraceMiner
from eval_harness.dataset.version import DatasetVersionManager


def grader(**overrides) -> GraderConfig:
    defaults = dict(type=GraderType.MODEL, name="model_based", config={"rubric": "r"})
    defaults.update(overrides)
    return GraderConfig(**defaults)


def item(idx: str, prompt: str = "p", **overrides) -> dict:
    base = {
        "id": idx,
        "prompt": prompt,
        "description": "d",
        "graders": [{"type": "model", "name": "model_based", "config": {"rubric": "r"}}],
        "metadata": {"capabilities": ["qa"]},
    }
    base.update(overrides)
    return base


# ─── 2.1 Manual import ───────────────────────────────────────────────────────


class TestManualImport:
    def test_yaml_roundtrip(self):
        yaml_text = """
name: support-suite
description: 客服场景
version: 1.0.0
tags: ["support"]
items:
  - id: refund-policy
    prompt: "退款政策是什么？"
    description: "政策问答"
    graders:
      - type: model
        name: model_based
        config:
          rubric: "回答需包含退款政策"
    metadata:
      capabilities: ["qa"]
"""
        dataset = import_from_content(yaml_text, format="yaml")
        assert dataset.name == "support-suite"
        assert dataset.tags == ["support"]
        assert len(dataset.items) == 1
        assert dataset.items[0].source_type == SourceType.MANUAL
        assert dataset.items[0].graders[0].name == "model_based"
        assert dataset.items[0].created_at > 0

    def test_json_roundtrip(self):
        import json

        payload = {
            "name": "json-ds",
            "items": [
                item("i1", prompt="hello"),
                item("i2", prompt="world"),
            ],
        }
        dataset = import_from_content(json.dumps(payload), format="json")
        assert [i.id for i in dataset.items] == ["i1", "i2"]
        assert dataset.items[0].graders[0].type == GraderType.MODEL

    def test_missing_prompt_rejected_with_item_and_field(self):
        payload = {
            "name": "bad",
            "items": [item("i1"), {"id": "i2", "graders": [{"type": "model", "name": "model_based"}]}],
        }
        with pytest.raises(DatasetImportError) as exc:
            parse_dataset_payload(payload)
        msg = str(exc.value)
        assert "i2" in msg
        assert "prompt" in msg

    def test_missing_graders_rejected_with_item_and_field(self):
        payload = {
            "name": "bad",
            "items": [{"id": "i1", "prompt": "p"}],
        }
        with pytest.raises(DatasetImportError) as exc:
            parse_dataset_payload(payload)
        msg = str(exc.value)
        assert "i1" in msg
        assert "graders" in msg

    def test_empty_prompt_rejected(self):
        payload = {"name": "bad", "items": [item("i1", prompt="   ")]}
        with pytest.raises(DatasetImportError) as exc:
            parse_dataset_payload(payload)
        assert "prompt" in str(exc.value)

    def test_invalid_grader_type_rejected(self):
        payload = {"name": "bad", "items": [item("i1", graders=[{"type": "magic", "name": "x"}])]}
        with pytest.raises(DatasetImportError) as exc:
            parse_dataset_payload(payload)
        assert "magic" in str(exc.value)

    def test_duplicate_item_ids_rejected(self):
        payload = {"name": "bad", "items": [item("i1"), item("i1")]}
        with pytest.raises(DatasetImportError):
            parse_dataset_payload(payload)

    def test_non_mapping_rejected(self):
        with pytest.raises(DatasetImportError):
            import_from_content("- a\n- b\n", format="yaml")

    def test_source_type_ref_defaults(self):
        payload = {"name": "adv", "items": [item("i1")]}
        dataset = parse_dataset_payload(payload, source_type=SourceType.ADVERSARIAL, source_ref="hand-crafted")
        assert dataset.items[0].source_type == SourceType.ADVERSARIAL
        assert dataset.items[0].source_ref == "hand-crafted"

    def test_yaml_syntax_error_wrapped(self):
        with pytest.raises(DatasetImportError) as exc:
            import_from_content("a: [unclosed", format="yaml")
        assert "Invalid YAML" in str(exc.value)


# ─── 2.2 Trace Mining ────────────────────────────────────────────────────────


class FakeTraceProvider:
    """trace_id → spans 的内存 provider"""

    def __init__(self, traces: dict[str, list[dict]]):
        self.traces = traces

    async def get_trace_ids(self, filters=None, limit=100):
        return list(self.traces)[:limit]

    async def get_spans(self, trace_id):
        return self.traces.get(trace_id, [])


def span(name: str, start: float, end: float, status: str = "OK",
         attributes: dict | None = None) -> dict:
    return {
        "name": name,
        "attributes": attributes or {},
        "start_time": str(start),
        "end_time": str(end),
        "status": {"status_code": status},
        "trace_id": "",
        "span_id": name,
    }


class TestTraceMining:
    async def test_failed_tasks_mining(self):
        provider = FakeTraceProvider({
            "trace_ok": [span("root", 0, 10, "OK", {"input.value": "正常问题"})],
            "trace_bad": [span("root", 0, 10, "ERROR", {"input.value": "失败问题"})],
        })
        miner = TraceMiner(provider)
        report = await miner.mine("failed_tasks")

        assert report.strategy == "failed_tasks"
        assert len(report.items) == 1
        mined = report.items[0]
        assert mined.prompt == "失败问题"
        assert mined.source_type == SourceType.TRACE_MINING
        assert mined.source_ref == "trace_bad"
        assert mined.id.startswith("mined_failed_tasks_")
        assert report.skipped == []

    async def test_missing_prompt_counts_as_skipped(self):
        provider = FakeTraceProvider({
            "trace_no_input": [span("root", 0, 10, "ERROR")],
        })
        miner = TraceMiner(provider)
        report = await miner.mine("failed_tasks")

        assert report.items == []
        assert len(report.skipped) == 1
        assert report.skipped[0]["trace_id"] == "trace_no_input"
        assert "no user input" in report.skipped[0]["reason"]

    async def test_long_running_p90_multiplier(self):
        traces = {}
        for i in range(10):
            traces[f"trace_{i}"] = [
                span("root", 0, 10.0 + i, "OK", {"input.value": f"q{i}"})
            ]
        # 时长 10..19; p90≈18 → 阈值 = 18×1.0 = 18 → 仅 trace_9 (19) 超阈值
        provider = FakeTraceProvider(traces)
        miner = TraceMiner(provider, long_running_multiplier=1.0)
        report = await miner.mine("long_running")

        assert len(report.items) == 1
        assert report.items[0].source_ref == "trace_9"

    async def test_diverse_sampling_deterministic(self):
        traces = {
            f"trace_{i}": [span("root", 0, 1, "OK", {"input.value": f"q{i}"})]
            for i in range(50)
        }
        miner = TraceMiner(FakeTraceProvider(traces))
        report_a = await miner.mine("diverse_sampling", limit=5)
        report_b = await miner.mine("diverse_sampling", limit=5)

        assert len(report_a.items) == 5
        assert [i.id for i in report_a.items] == [i.id for i in report_b.items]

    async def test_user_dissatisfied_not_implemented(self):
        miner = TraceMiner(FakeTraceProvider({}))
        with pytest.raises(NotImplementedError):
            await miner.mine("user_dissatisfied")

    async def test_limit_caps_output(self):
        traces = {
            f"trace_{i}": [span("root", 0, 1, "ERROR", {"input.value": f"q{i}"})]
            for i in range(10)
        }
        miner = TraceMiner(FakeTraceProvider(traces))
        report = await miner.mine("failed_tasks", limit=3)
        assert len(report.items) == 3

    async def test_input_message_list_fallback(self):
        provider = FakeTraceProvider({
            "t1": [span("root", 0, 1, "ERROR", {
                "llm.input_messages": [{"content": "列表形态输入"}],
            })],
        })
        report = await TraceMiner(provider).mine("failed_tasks")
        assert report.items[0].prompt == "列表形态输入"


# ─── 2.3 LLM generation ──────────────────────────────────────────────────────


def make_llm_fn(response: str | Exception):
    calls: list[tuple[str, str]] = []

    async def fn(system: str, user: str) -> str:
        calls.append((system, user))
        if isinstance(response, Exception):
            raise response
        return response

    fn.calls = calls
    return fn


VALID_LLM_OUTPUT = """```json
[
  {"id": "check-balance", "description": "查余额", "prompt": "帮我查余额",
   "capabilities": ["qa"],
   "graders": [{"type": "model", "name": "model_based", "config": {"rubric": "报出余额"}}]},
  {"id": "transfer", "description": "转账", "prompt": "给张三转100元",
   "graders": [{"type": "tool_calls", "name": "tool_calls",
                "config": {"required_tools": ["transfer_money"]}}]}
]
```"""


class TestLLMGenerator:
    async def test_generate_valid_items(self):
        llm_fn = make_llm_fn(VALID_LLM_OUTPUT)
        gen = LLMDatasetGenerator(llm_fn)
        report = await gen.generate("银行助手场景", capabilities=["qa", "finance"], count=2)

        assert len(report.items) == 2
        assert report.invalid == []
        first = report.items[0]
        assert first.source_type == SourceType.LLM_GENERATED
        assert first.source_ref == "银行助手场景"
        assert first.metadata["capabilities"] == ["qa"]
        # 未写 capabilities 的条目继承场景能力维度
        assert report.items[1].metadata["capabilities"] == ["qa", "finance"]
        # grader 配置可解析
        assert report.items[1].graders[0].name == "tool_calls"

    async def test_missing_llm_fn_fails_explicitly(self):
        gen = LLMDatasetGenerator(None)
        with pytest.raises(DatasetGenerationError) as exc:
            await gen.generate("场景")
        assert "LLM function not configured" in str(exc.value)

    async def test_unparseable_output_raises(self):
        gen = LLMDatasetGenerator(make_llm_fn("not json at all"))
        with pytest.raises(DatasetGenerationError) as exc:
            await gen.generate("场景")
        assert "not a parsable JSON array" in str(exc.value)

    async def test_invalid_items_reported_not_silently_dropped(self):
        raw = """[
          {"id": "good", "prompt": "ok", "graders": [{"type": "model", "name": "model_based"}]},
          {"id": "no-graders", "prompt": "缺评分器"},
          {"prompt": "缺 id 和评分器"}
        ]"""
        report = await LLMDatasetGenerator(make_llm_fn(raw)).generate("场景")

        assert len(report.items) == 1
        assert report.items[0].id == "good"
        assert len(report.invalid) == 2
        assert any("graders" in i["error"] for i in report.invalid)

    async def test_all_invalid_raises(self):
        raw = """[{"id": "bad", "prompt": "p"}]"""
        with pytest.raises(DatasetGenerationError) as exc:
            await LLMDatasetGenerator(make_llm_fn(raw)).generate("场景")
        assert "no valid items" in str(exc.value)


# ─── 2.4 Regression extraction ───────────────────────────────────────────────


def trial(idx: int, success: bool, trace_id: str = "", transcript: list | None = None,
          error: str | None = None) -> TrialResult:
    return TrialResult(
        trial_index=idx,
        trace_id=trace_id,
        success=success,
        grader_results=[],
        metrics={},
        transcript=transcript or [],
        outcome={},
        error=error,
    )


def suite_with(task_id: str = "t1", prompt: str = "默认 prompt") -> EvalSuite:
    return EvalSuite(
        name="s",
        tasks=[EvalTask(
            id=task_id,
            prompt=prompt,
            graders=[grader()],
            env={"ws": "/tmp/x"},
        )],
    )


def run_with(trials: dict[str, list[TrialResult]], run_id: str = "run_x") -> RunResult:
    return RunResult(run_id=run_id, suite_name="s", status="completed", trials=trials)


class TestRegressionExtraction:
    def test_extract_failed_trials(self):
        run = run_with({
            "t1": [trial(0, True), trial(1, False, trace_id="trace_dead", transcript=[{"content": "失败输入"}])],
        })
        suite = suite_with()
        items, report = RegressionExtractor().extract_from_run(run, suite)

        assert report.failed_trials == 1
        assert len(items) == 1
        reg = items[0]
        assert reg.prompt == "失败输入"
        assert reg.source_type == SourceType.REGRESSION
        assert reg.source_ref == "trace_dead"
        # graders/env 从 suite task 复用 → 再评测可直接运行
        assert reg.graders == suite.tasks[0].graders
        assert reg.env == {"ws": "/tmp/x"}
        assert reg.id == "regression_t1_1"

    def test_dedup_per_task_first_failed_trial_wins(self):
        run = run_with({
            "t1": [
                trial(0, False, trace_id="trace_first", transcript=[{"content": "同一输入"}]),
                trial(1, False, trace_id="trace_second"),
                trial(2, False, trace_id="trace_third"),
            ],
        })
        items, report = RegressionExtractor().extract_from_run(run, suite_with())

        assert report.failed_trials == 3
        assert len(items) == 1
        assert items[0].source_ref == "trace_first"

    def test_prompt_fallback_to_suite_task(self):
        run = run_with({"t1": [trial(0, False, trace_id="trace_x")]})
        items, _ = RegressionExtractor().extract_from_run(run, suite_with(prompt="套件里的 prompt"))
        assert items[0].prompt == "套件里的 prompt"

    def test_no_prompt_source_skipped(self):
        run = run_with({"ghost": [trial(0, False, trace_id="trace_y")]})
        items, report = RegressionExtractor().extract_from_run(run, None)
        assert items == []
        assert report.skipped[0]["task_id"] == "ghost"

    def test_max_items_cap(self):
        run = run_with({
            f"t{i}": [trial(0, False, trace_id=f"trace_{i}", transcript=[{"content": f"q{i}"}])]
            for i in range(5)
        })
        items, report = RegressionExtractor().extract_from_run(run, None, max_items=2)
        assert len(items) == 2
        assert len(report.skipped) == 3

    def test_passing_run_extracts_nothing(self):
        run = run_with({"t1": [trial(0, True)]})
        items, report = RegressionExtractor().extract_from_run(run, suite_with())
        assert items == []
        assert report.failed_trials == 0


class TestRegressionMerge:
    def test_merge_dedupes_on_normalized_prompt(self):
        extractor = RegressionExtractor()
        dataset = EvalDataset(name="d", items=[
            {"id": "orig", "prompt": "Why did  the  task   fail?", "graders": [grader()]},
        ])
        new_items, _ = extractor.extract_from_run(run_with({
            "t1": [trial(0, False, trace_id="trace_z", transcript=[{"content": "why did  the task fail?"}])],
        }))
        # 注意: 归一化只折叠空白不转小写 — 大小写不同视为不同 prompt
        merged_dataset, report = extractor.merge_into_dataset(dataset, [
            {"id": "reg_t1_0", "prompt": "Why did the task fail?", "graders": [grader()]},
            {"id": "reg_t1_1", "prompt": "全新问题", "graders": [grader()]},
        ])

        assert report.merged == 1
        assert report.merged_skipped[0]["item_id"] == "reg_t1_0"
        assert [i.id for i in merged_dataset.items] == ["orig", "reg_t1_1"]

    def test_repeated_runs_do_not_bloat_dataset(self):
        extractor = RegressionExtractor()
        dataset = EvalDataset(name="d", items=[])
        failed_run = run_with({
            "t1": [trial(0, False, trace_id="trace_z", transcript=[{"content": "同一失败"}])],
        })
        items, _ = extractor.extract_from_run(failed_run)

        dataset, r1 = extractor.merge_into_dataset(dataset, items)
        dataset, r2 = extractor.merge_into_dataset(dataset, extractor.extract_from_run(
            run_with({"t1": [trial(0, False, trace_id="trace_z2", transcript=[{"content": "同一失败"}])]})
        )[0])

        assert r1.merged == 1
        assert r2.merged == 0
        assert len(dataset.items) == 1

    def test_id_conflict_resolved(self):
        extractor = RegressionExtractor()
        dataset = EvalDataset(name="d", items=[
            {"id": "reg_t1_0", "prompt": "旧任务 prompt", "graders": [grader()]},
        ])
        dataset, _ = extractor.merge_into_dataset(dataset, [
            {"id": "reg_t1_0", "prompt": "新任务 prompt", "graders": [grader()]},
        ])
        ids = [i.id for i in dataset.items]
        assert len(ids) == len(set(ids)) == 2


def test_normalize_prompt():
    assert normalize_prompt("  a  \n  b\t c  ") == "a b c"


# ─── 2.5 Quality & Coverage ──────────────────────────────────────────────────


class TestQualityChecker:
    def test_errors_and_warnings_listed_with_item_ids(self):
        dataset = EvalDataset(name="d", items=[
            item("dup", prompt="same question"),
            item("dup2", prompt="same   question"),
            {"id": "empty", "prompt": "", "graders": [grader()]},
            {"id": "nog", "prompt": "ok", "graders": []},
            item("long", prompt="x" * 10_001),
        ])
        report = DatasetQualityChecker().check(dataset)

        assert report.total_items == 5
        assert not report.ok

        error_codes = {e.code for e in report.errors}
        assert error_codes == {"empty_prompt", "missing_graders"}
        assert {e.item_id for e in report.errors if e.code == "empty_prompt"} == {"empty"}
        assert {e.item_id for e in report.errors if e.code == "missing_graders"} == {"nog"}

        warning_codes = {w.code for w in report.warnings}
        assert warning_codes == {"duplicate_prompt", "long_prompt"}
        dup_ids = {w.item_id for w in report.warnings if w.code == "duplicate_prompt"}
        assert dup_ids == {"dup2"}

    def test_ok_dataset(self):
        dataset = EvalDataset(name="d", items=[item("a", prompt="q1"), item("b", prompt="q2")])
        report = DatasetQualityChecker().check(dataset)
        assert report.ok
        assert report.warnings == []

    def test_duplicate_item_id_is_error(self):
        # EvalDataset 构造器本身会拒绝重复 ID; model_copy 绕过校验模拟
        # 存储层/合并路径产生的脏数据, 检查器兜底报 error
        base = EvalDataset(name="d", items=[item("same"), item("other")])
        dirty = base.model_copy(update={"items": [item("same"), item("same")]})
        report = DatasetQualityChecker().check(dirty)
        assert any(e.code == "duplicate_item_id" for e in report.errors)


class TestCoverageAnalyzer:
    def test_coverage_counts_and_insufficient(self):
        dataset = EvalDataset(name="d", items=[
            item("a", metadata={"capabilities": ["qa"]}),
            item("b", metadata={"capabilities": ["qa"]}),
            item("c", metadata={"capabilities": ["rag"]}),
            item("d", metadata={"capabilities": []}),
        ])
        report = CoverageAnalyzer().analyze(dataset)

        assert report.total_items == 4
        assert report.untagged_items == 1
        assert report.coverage["qa"] == round(2 / 5, 3)
        assert report.coverage["rag"] == round(1 / 5, 3)
        assert {i["capability"] for i in report.insufficient} == {"qa", "rag"}

    def test_full_coverage_not_insufficient(self):
        dataset = EvalDataset(name="d", items=[
            item(f"i{i}", metadata={"capabilities": ["qa"]}) for i in range(5)
        ])
        report = CoverageAnalyzer().analyze(dataset)
        assert report.coverage["qa"] == 1.0
        assert report.insufficient == []

    def test_expected_capabilities_included_even_if_absent(self):
        dataset = EvalDataset(name="d", items=[item("a", metadata={"capabilities": ["qa"]})])
        report = CoverageAnalyzer().analyze(dataset, expected_capabilities=["code_gen"])
        assert report.coverage["code_gen"] == 0.0
        assert {"capability": "code_gen", "item_count": 0, "coverage": 0.0} in report.insufficient

    def test_empty_dataset(self):
        report = CoverageAnalyzer().analyze(EvalDataset(name="d"))
        assert report.coverage == {}
        assert report.total_items == 0


# ─── 2.6 Version management ──────────────────────────────────────────────────


class TestVersionManager:
    def test_bump_rules(self):
        assert DatasetVersionManager.bump_version("1.2.3", "major") == "2.0.0"
        assert DatasetVersionManager.bump_version("1.2.3", "minor") == "1.3.0"
        assert DatasetVersionManager.bump_version("1.2.3", "patch") == "1.2.4"

    def test_bump_records_change_log(self):
        dataset = EvalDataset(name="d", version="1.0.0", items=[item("a")])
        updated = DatasetVersionManager.bump(dataset, "minor", change_note="加入回归样本")

        assert updated.version == "1.1.0"
        assert updated is not dataset  # 副本, 原数据集不变
        assert dataset.version == "1.0.0"
        assert len(updated.change_log) == 1
        entry = updated.change_log[0]
        assert entry["version"] == "1.1.0"
        assert entry["change_type"] == "minor"
        assert entry["note"] == "加入回归样本"
        assert entry["item_count"] == 1
        assert updated.updated_at >= dataset.updated_at

    def test_change_log_accumulates(self):
        dataset = EvalDataset(name="d")
        dataset = DatasetVersionManager.bump(dataset, "minor", "add")
        dataset = DatasetVersionManager.bump(dataset, "patch", "fix typo")
        dataset = DatasetVersionManager.bump(dataset, "major", "rewrite")
        assert [c["change_type"] for c in dataset.change_log] == ["minor", "patch", "major"]
        assert dataset.version == "2.0.0"

    def test_invalid_version_rejected(self):
        with pytest.raises(DatasetError):
            DatasetVersionManager.bump_version("1.0", "minor")
        with pytest.raises(DatasetError):
            DatasetVersionManager.bump_version("x.y.z", "minor")
        with pytest.raises(DatasetError):
            DatasetVersionManager.bump_version("1.0.0", "huge")
