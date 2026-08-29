"""
EvalRunner — the core orchestration engine.

Coordinates the full evaluation lifecycle:
1. Load suite
2. For each task, run N trials (TransientError → exponential-backoff retry)
3. For each trial: snapshot → setup → run agent → get traces → grade → teardown → leak check
4. Aggregate results into a RunSummary (pass@k / pass^k / consistency / saturation)

Usage:
    runner = EvalRunner(agent_runner=my_runner)
    result = await runner.run_suite(suite)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import Any, Awaitable, Callable

from eval_harness.core.contract import (
    AgentRunner,
    EnvironmentManager,
    EvalContext,
    Grader,
    Storage,
    TraceProvider,
    TransientError,
)
from eval_harness.core.metrics import aggregate_metrics, extract_metrics, pass_at_k, pass_power_k
from eval_harness.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    RunResult,
    RunSummary,
    TaskSummary,
    TrialResult,
)
from eval_harness.graders import DEFAULT_GRADERS
from eval_harness.metrics.base import Metric
from eval_harness.metrics.llm_judge import LLMFn
from eval_harness.storage import MemoryStorage
from eval_harness.trace import PhoenixProvider


logger = logging.getLogger(__name__)


# ─── Default Environment (NoOp) ──────────────────────────────────────────────


class NoOpEnvironment:
    """默认无操作环境管理器"""

    async def setup(self, task: EvalTask) -> None:
        pass

    async def teardown(self, task: EvalTask) -> None:
        pass

    async def snapshot(self) -> dict[str, Any]:
        return {}

    async def verify_clean(self, baseline: dict[str, Any]) -> dict[str, Any]:
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict[str, Any]) -> None:
        return None


# ─── Progress Callback Type ──────────────────────────────────────────────────

ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


# ─── EvalRunner ───────────────────────────────────────────────────────────────


class EvalRunner:
    """
    核心编排器。

    接收项目注入的组件, 执行评测。
    所有组件都有默认值, 只需配置你关心的部分。

    使用方式:
        runner = EvalRunner(
            agent_runner=MyAgentRunner(),          # 必选
            trace_provider=PhoenixProvider(...),   # 可选, 默认 Phoenix
            storage=SqliteStorage(...),            # 可选, 默认 Memory
            environment=MyEnvironment(),           # 可选, 默认 NoOp
            graders=[MyCustomGrader()],            # 可选, 与内置合并 (同名覆盖)
            concurrency=1,                         # trial 并发数
        )
        result = await runner.run_suite(suite)
    """

    def __init__(
        self,
        agent_runner: AgentRunner,
        trace_provider: TraceProvider | None = None,
        storage: Storage | None = None,
        environment: EnvironmentManager | None = None,
        graders: list[Grader] | None = None,
        concurrency: int = 1,
        max_concurrent_graders: int = 4,
        per_trial_timeout: float = 300.0,
        max_trial_retries: int = 2,
        retry_base_delay: float = 1.0,
        grader_timeout: float = 60.0,
        verify_environment: bool = True,
        enable_grader_cache: bool = True,
        metrics_registry: dict[str, Metric] | None = None,
        llm_fn: LLMFn | None = None,
    ):
        """
        Args:
            agent_runner: Agent 运行器 (必选)
            trace_provider: Trace 数据提供者 (默认 Phoenix)
            storage: 结果存储 (默认 Memory; 组合暴露 storage.datasets)
            environment: 环境管理器 (默认 NoOp)
            graders: 额外评分器 (与内置合并, 同名覆盖)
            concurrency: trial 并发数
            max_concurrent_graders: 评分器最大并发数
            per_trial_timeout: 单个 trial 超时 (秒)
            max_trial_retries: TransientError 最大重试次数
            retry_base_delay: 重试指数退避基础延迟 (秒)
            grader_timeout: 单个 grader 评分超时 (秒)
            verify_environment: 是否做环境泄漏检测
            enable_grader_cache: 是否启用 grader 结果缓存 (prompt-hash)
            metrics_registry: LLM 输出质量指标注册表 (name → Metric),
                供 metric grader 分发; None = metric 类 grader 得配置错误结果
            llm_fn: LLM 函数 (system, user) → text, 注入未配置的指标与
                metric grader; None 不改变既有行为
        """
        self.agent_runner = agent_runner
        self.trace_provider = trace_provider or PhoenixProvider()
        self.storage = storage or MemoryStorage()
        self.environment = environment or NoOpEnvironment()
        self.concurrency = max(1, concurrency)
        self.max_concurrent_graders = max(1, max_concurrent_graders)
        self.per_trial_timeout = per_trial_timeout
        self.max_trial_retries = max(0, max_trial_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.grader_timeout = grader_timeout
        self.verify_environment = verify_environment
        self.enable_grader_cache = enable_grader_cache
        self.metrics_registry: dict[str, Metric] = dict(metrics_registry or {})
        self.llm_fn = llm_fn

        # 注册 grader: 内置 + 自定义 (自定义覆盖同名)
        self._graders: dict[str, Grader] = {}
        for g in DEFAULT_GRADERS:
            self._graders[g.name] = g
        if graders:
            for g in graders:
                self._graders[g.name] = g

        # 注入 storage 到需要它的 grader (如 HumanGrader 的评分请求落库)。
        # 覆盖式注入: runner 是组合根, grader 实例可能被多个 runner 复用。
        for g in self._graders.values():
            if hasattr(g, "storage"):
                g.storage = self.storage
            # metric 分发 grader: 注入指标注册表与 LLM 函数 (D1/D2)
            if hasattr(g, "metrics_registry"):
                g.metrics_registry = self.metrics_registry
            if hasattr(g, "llm_fn") and self.llm_fn is not None and g.llm_fn is None:
                g.llm_fn = self.llm_fn

        # LLM 函数注入未自行配置的指标 (指标实现持有 llm_fn 属性)
        if self.llm_fn is not None:
            for metric in self.metrics_registry.values():
                if getattr(metric, "llm_fn", None) is None:
                    metric.llm_fn = self.llm_fn

        # Grader 结果缓存 (runner 生命周期内, 内容寻址)
        self._grader_cache: dict[str, GraderResult] = {}

    # ── Public API ────────────────────────────────────────────────────────

    async def run_suite(
        self,
        suite: EvalSuite,
        callback: ProgressCallback | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """
        执行整个 suite。

        Args:
            suite: 评测套件
            callback: 进度回调 (用于 SSE 推送)
            run_id: 指定 run ID (API 层预生成, 便于启动即返回)

        Returns:
            RunResult: 完整运行结果
        """
        run = RunResult(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            suite_name=suite.name,
            status="running",
            started_at=time.time() * 1000,
        )

        try:
            # 保存 suite 定义与初始 run 记录 (保证启动后立即可查询)
            await self.storage.save_suite(suite)
            await self.storage.save_run(run)

            for task in suite.tasks:
                await self._emit(callback, "task_start", {
                    "task_id": task.id,
                    "task_description": task.description,
                })

                trials = await self._run_task_with_retries(task, callback, run_id=run.run_id)
                run.trials[task.id] = trials

                pass_rate = (
                    sum(1 for t in trials if t.success) / len(trials)
                    if trials else 0.0
                )
                await self._emit(callback, "task_complete", {
                    "task_id": task.id,
                    "trials": len(trials),
                    "pass_rate": pass_rate,
                })

            # 计算汇总
            run.summary = self._compute_summary(run, suite)
            run.status = "completed"

        except asyncio.CancelledError:
            run.status = "cancelled"
            raise

        except Exception as e:
            run.status = "failed"
            run.error = str(e)

        finally:
            # 所有退出路径 (含启动阶段被取消) 都落盘最终状态
            run.completed_at = time.time() * 1000
            await self.storage.save_run(run)

        return run

    async def cancel_run(self, run_id: str) -> bool:
        """
        取消正在运行的 suite。

        注意: 正在执行的 trial 会继续完成, 但后续 task 不再启动。
        """
        # TODO: 实现取消逻辑 (通过事件/标志位)
        return False

    # ── Task Execution ───────────────────────────────────────────────────

    async def _run_task_with_retries(
        self,
        task: EvalTask,
        callback: ProgressCallback | None,
        run_id: str = "",
    ) -> list[TrialResult]:
        """执行单个任务的多个 trial (TransientError 指数退避重试)"""
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _trial(index: int) -> TrialResult:
            async with semaphore:
                # trial_start 发一次 (重试不重复发; 事件按 (task_id, trial_index) 幂等)
                await self._emit(callback, "trial_start", {
                    "task_id": task.id,
                    "trial_index": index,
                })
                for attempt in range(self.max_trial_retries + 1):
                    try:
                        result = await self._run_trial(task, index, run_id=run_id)
                        await self._emit(callback, "trial_complete", {
                            "task_id": task.id,
                            "trial_index": index,
                            "success": result.success,
                        })
                        return result
                    except TransientError as e:
                        if attempt < self.max_trial_retries:
                            delay = self.retry_base_delay * (2**attempt)
                            logger.warning(
                                "TransientError in task %s trial %d (attempt %d), "
                                "retrying in %.2fs: %s",
                                task.id, index, attempt + 1, delay, e,
                            )
                            await asyncio.sleep(delay)
                            continue
                        # 重试用尽, 返回失败结果 (suite 继续)
                        return TrialResult(
                            trial_index=index,
                            trace_id="",
                            success=False,
                            grader_results=[],
                            metrics={},
                            transcript=[],
                            outcome={},
                            duration_ms=0.0,
                            error=(
                                f"TransientError after {self.max_trial_retries} "
                                f"retries: {e}"
                            ),
                        )

        trials = await asyncio.gather(
            *[_trial(i) for i in range(task.max_trials)],
            return_exceptions=False,
        )
        return list(trials)

    async def _run_trial(
        self,
        task: EvalTask,
        index: int,
        run_id: str = "",
    ) -> TrialResult:
        """执行单次 trial (含环境基线快照与泄漏检测)"""
        # 1. 拍摄环境基线快照 (setup 前, 即"干净"状态)
        try:
            baseline = await self.environment.snapshot()
        except Exception as e:
            logger.warning("Environment snapshot failed for task %s: %s", task.id, e)
            baseline = {}

        # 2. 准备环境
        await self.environment.setup(task)
        start_time = time.time() * 1000

        try:
            # 3. 运行 Agent (带超时)
            trace_id, transcript, outcome = await asyncio.wait_for(
                self.agent_runner.run(task),
                timeout=self.per_trial_timeout,
            )

            # 4. 获取 trace spans
            spans = await self.trace_provider.get_spans(trace_id)

            # 5. 提取过程指标
            metrics = extract_metrics(spans, task.tracked_metrics)
            elapsed = time.time() * 1000 - start_time
            metrics["latency_ms"] = elapsed

            # 6. 构建 trial result (临时 success=True, 评分后更新)
            trial = TrialResult(
                trial_index=index,
                trace_id=trace_id,
                success=True,
                grader_results=[],
                metrics=metrics,
                transcript=transcript,
                outcome=outcome,
                duration_ms=elapsed,
            )

            # 7. 运行评分器 (EvalContext 贯穿, shared_state 在 grader 间共享)
            context = EvalContext(
                run_id=run_id,
                task=task,
                trial=trial,
                spans=spans,
                shared_state={},
            )
            trial = await self._grade_trial(trial, spans, task, context)

            return trial

        except asyncio.TimeoutError:
            elapsed = time.time() * 1000 - start_time
            return TrialResult(
                trial_index=index,
                trace_id="",
                success=False,
                grader_results=[],
                metrics={"latency_ms": elapsed},
                transcript=[],
                outcome={},
                duration_ms=elapsed,
                error=f"Trial timed out after {self.per_trial_timeout}s",
            )

        except asyncio.CancelledError:
            raise

        except TransientError:
            # 交由 _run_task_with_retries 处理重试
            raise

        except Exception as e:
            elapsed = time.time() * 1000 - start_time
            return TrialResult(
                trial_index=index,
                trace_id="",
                success=False,
                grader_results=[],
                metrics={"latency_ms": elapsed},
                transcript=[],
                outcome={},
                duration_ms=elapsed,
                error=str(e),
            )

        finally:
            # 8. 清理环境
            await self.environment.teardown(task)

            # 9. 泄漏检测: 与基线比对, 泄漏则告警并恢复 (不判 trial 失败 —
            #    泄漏是环境问题不是 Agent 问题)
            if self.verify_environment:
                try:
                    verify = await self.environment.verify_clean(baseline)
                    if not verify.get("clean", False):
                        logger.warning(
                            "Environment leak detected in task %s trial %d: %s",
                            task.id,
                            index,
                            verify.get("differences"),
                        )
                        await self.environment.restore(baseline)
                except Exception as e:
                    logger.warning(
                        "Environment leak check failed for task %s trial %d: %s",
                        task.id, index, e,
                    )

    # ── Grading ──────────────────────────────────────────────────────────

    async def _grade_trial(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> TrialResult:
        """
        对一次 trial 运行所有评分器 (依赖拓扑 Pipeline)。

        1. 按 GraderConfig.dependencies 拓扑排序
        2. 依赖 grader 未通过 (或未配置/未运行) → 跳过记 0 分
        3. 单个 grader 超时 → 记 0 分失败
        4. MODEL 类 grader sample_count > 1 → 多采样聚合 (平均分/不确定性/置信度)
        5. sample_count == 1 且缓存开启 → prompt-hash 结果缓存
        """
        grader_results: dict[str, GraderResult] = {}

        for config in self._topological_sort(task.graders):
            grader = self._resolve_grader(config)

            if grader is None:
                grader_results[config.name] = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Unknown grader: {config.name}",
                )
                continue

            # 依赖检查: 所有依赖的 grader 必须已运行且通过
            unsatisfied_dep = self._first_unsatisfied_dependency(config, grader_results)
            if unsatisfied_dep is not None:
                grader_results[config.name] = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=(
                        f"依赖未满足: grader '{unsatisfied_dep}' 未通过或未配置"
                    ),
                )
                continue

            # 缓存命中 (多采样 deliberate 重试绕过缓存)
            use_cache = self.enable_grader_cache and config.sample_count <= 1
            if use_cache:
                cache_key = self._grader_cache_key(config, trial)
                cached = self._grader_cache.get(cache_key)
                if cached is not None:
                    hit = cached.model_copy(deep=True)
                    hit.details = {**hit.details, "cached": True}
                    grader_results[config.name] = hit
                    continue

            try:
                # 分发型 grader (MetricGrader) 经 context.grader_config 感知
                # 当前生效配置; 其余 grader 不读取该字段, 行为不变
                call_context = (
                    replace(context, grader_config=config)
                    if context is not None
                    else None
                )
                result = await asyncio.wait_for(
                    grader.grade(trial, spans, task, call_context),
                    timeout=self.grader_timeout,
                )
            except asyncio.TimeoutError:
                result = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader timeout after {self.grader_timeout}s",
                )
            except Exception as e:
                result = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader error: {e}",
                )

            # LLM Judge 多采样: 计算平均分/不确定性/置信度
            if config.type == GraderType.MODEL and config.sample_count > 1:
                result = await self._multi_sample(grader, result, config, trial, spans, task, context)

            if use_cache:
                self._grader_cache[cache_key] = result.model_copy(deep=True)

            grader_results[config.name] = result

        trial.grader_results = [
            grader_results[config.name] for config in task.graders
        ]

        # 根据评分策略计算最终成功状态
        trial.success = self._compute_trial_success(task, trial.grader_results)

        return trial

    def _resolve_grader(self, config: GraderConfig) -> Grader | None:
        """按配置名解析 grader 实例。

        metric 类配置 (type: metric) 的 name 即指标名, 不在 grader 注册表
        内时回退到 "metric" 分发器 (D1), 由其按 metric_name/config.name
        路由到注入的 Metric 实例; 其余类型保持未知 grader 语义。
        """
        grader = self._graders.get(config.name)
        if grader is not None:
            return grader
        if config.type == GraderType.METRIC:
            return self._graders.get("metric")
        return None

    async def _multi_sample(
        self,
        grader: Grader,
        first_result: GraderResult,
        config: GraderConfig,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None,
    ) -> GraderResult:
        """多采样评分: 平均分 + 不确定性 (极差/2) + 置信度 (1 - 不确定性)"""
        samples = [first_result]
        for _ in range(config.sample_count - 1):
            try:
                samples.append(await grader.grade(trial, spans, task, context))
            except Exception as e:
                samples.append(GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader error: {e}",
                ))

        scores = [s.score for s in samples]
        avg_score = sum(scores) / len(scores)
        uncertainty = (max(scores) - min(scores)) / 2
        confidence = max(0.0, 1.0 - uncertainty)
        # 多数样本通过才视为通过 (偶数采样平票判不通过)
        passed = sum(1 for s in samples if s.passed) * 2 > len(samples)

        return GraderResult(
            grader_name=first_result.grader_name,
            grader_type=first_result.grader_type,
            score=avg_score,
            passed=passed,
            explanation=f"Multi-sample avg over {len(samples)} samples",
            details={
                **first_result.details,
                "sample_scores": scores,
                "sample_explanations": [s.explanation for s in samples],
            },
            confidence=confidence,
            uncertainty=uncertainty,
            sample_count=len(samples),
        )

    def _first_unsatisfied_dependency(
        self,
        config: GraderConfig,
        grader_results: dict[str, GraderResult],
    ) -> str | None:
        """返回第一个未满足的依赖名; 全部满足返回 None。

        依赖未在本 task 中配置 (因而没有结果) 视为未满足。
        """
        for dep in config.dependencies:
            result = grader_results.get(dep)
            if result is None or not result.passed:
                return dep
        return None

    def _topological_sort(
        self,
        configs: list[GraderConfig],
    ) -> list[GraderConfig]:
        """按 dependencies 拓扑排序 (保持声明顺序稳定, 环依赖按声明序兜底)"""
        config_map = {c.name: c for c in configs}
        visited: set[str] = set()
        visiting: set[str] = set()
        ordered: list[GraderConfig] = []

        def visit(name: str) -> None:
            if name in visited or name not in config_map:
                return
            if name in visiting:
                # 环依赖: 跳过 (由依赖检查兜底记 0 分)
                return
            visiting.add(name)
            for dep in config_map[name].dependencies:
                visit(dep)
            visiting.discard(name)
            visited.add(name)
            ordered.append(config_map[name])

        for config in configs:
            visit(config.name)
        return ordered

    def _grader_cache_key(self, config: GraderConfig, trial: TrialResult) -> str:
        """prompt-hash 缓存 key: sha256(grader name + config + transcript/outcome)"""
        payload = json.dumps(
            {
                "grader": config.name,
                "config": config.config,
                "transcript": trial.transcript,
                "outcome": trial.outcome,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _compute_trial_success(
        self,
        task: EvalTask,
        grader_results: list[GraderResult],
    ) -> bool:
        """根据评分策略判断 trial 是否成功"""
        strategy = task.score_strategy
        threshold = task.score_threshold

        if strategy == "all_pass":
            return all(r.passed for r in grader_results) if grader_results else True

        elif strategy == "weighted":
            if not grader_results:
                return True
            # 使用 grader config 中的权重
            total_weight = sum(
                gc.weight for gc in task.graders
            )
            if total_weight == 0:
                return True
            weighted_score = sum(
                r.score * gc.weight
                for r, gc in zip(grader_results, task.graders)
            ) / total_weight
            return weighted_score >= threshold

        elif strategy == "hybrid":
            # required 必须通过
            required_pass = all(
                r.passed
                for r, gc in zip(grader_results, task.graders)
                if gc.required
            )
            if not required_pass:
                return False

            # 非 required 加权
            non_required = [
                (r, gc) for r, gc in zip(grader_results, task.graders)
                if not gc.required
            ]
            if not non_required:
                return True

            total_weight = sum(gc.weight for _, gc in non_required)
            if total_weight == 0:
                return True
            weighted_score = sum(
                r.score * gc.weight for r, gc in non_required
            ) / total_weight
            return weighted_score >= threshold

        return True

    # ── Summary Computation ──────────────────────────────────────────────

    def _compute_summary(
        self,
        run: RunResult,
        suite: EvalSuite,
    ) -> RunSummary:
        """计算 suite 级别的汇总 (含一致性检查 / 饱和度检测 / pending 单列)"""
        task_summaries: list[TaskSummary] = []
        all_trials: list[TrialResult] = []

        # 构建 task_id → description 映射
        task_descriptions = {t.id: t.description for t in suite.tasks}

        # 预计算 k_values (避免空 trials 时 KeyError)
        max_trials = max(
            (len(trials) for trials in run.trials.values()), default=0
        )
        k_values = list(range(1, max_trials + 1)) if max_trials > 0 else [1]

        for task_id, trials in run.trials.items():
            all_trials.extend(trials)

            # pending (人工评分未回传) 的 trial 单列, 不计入通过率
            pending_indices = {
                i for i, t in enumerate(trials) if self._trial_pending(t)
            }
            counted = [
                t for i, t in enumerate(trials) if i not in pending_indices
            ]

            p_at_k = {k: pass_at_k(counted, k) for k in k_values}
            p_pow_k = {k: pass_power_k(counted, k) for k in k_values}

            # 平均分 (仅计有评分结果的 trial)
            scores = [t.avg_score() for t in counted if t.grader_results]

            # trial 间一致性
            consistency = self._check_trial_consistency(counted)

            task_summaries.append(TaskSummary(
                task_id=task_id,
                task_description=task_descriptions.get(task_id, ""),
                total_trials=len(trials),
                pass_at_k=p_at_k,
                pass_power_k=p_pow_k,
                avg_score=sum(scores) / len(scores) if scores else 0.0,
                avg_metrics=aggregate_metrics(trials),
                failures=[
                    i for i, t in enumerate(trials)
                    if not t.success and i not in pending_indices
                ],
                pending_trials=sorted(pending_indices),
                consistent=consistency["consistent"],
                score_std_dev=consistency["std_dev"],
            ))

        # 全局汇总
        return RunSummary(
            total_tasks=len(run.trials),
            total_trials=len(all_trials),
            pass_at_k={
                k: sum(
                    ts.pass_at_k.get(k, 0.0) for ts in task_summaries
                ) / len(task_summaries)
                if task_summaries else 0.0
                for k in k_values
            },
            pass_power_k={
                k: sum(
                    ts.pass_power_k.get(k, 0.0) for ts in task_summaries
                ) / len(task_summaries)
                if task_summaries else 0.0
                for k in k_values
            },
            avg_score=(
                sum(ts.avg_score for ts in task_summaries) / len(task_summaries)
                if task_summaries else 0.0
            ),
            avg_metrics=aggregate_metrics(all_trials),
            task_summaries=task_summaries,
            failures=[ts.task_id for ts in task_summaries if ts.failures],
            saturation=self._detect_saturation(task_summaries),
        )

    @staticmethod
    def _trial_pending(trial: TrialResult) -> bool:
        """trial 是否在等待人工评分 (含 pending 状态的 grader 结果)"""
        return any(
            gr.details.get("status") == "pending" for gr in trial.grader_results
        )

    @staticmethod
    def _check_trial_consistency(trials: list[TrialResult]) -> dict[str, Any]:
        """检查 trial 间分数一致性 (std < 0.2 视为一致)"""
        scores = [
            t.avg_score() for t in trials if t.grader_results
        ]

        if len(scores) < 2:
            return {"consistent": True, "std_dev": 0.0, "scores": scores}

        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        std_dev = variance**0.5

        return {
            "consistent": std_dev < 0.2,
            "std_dev": std_dev,
            "scores": scores,
        }

    @staticmethod
    def _detect_saturation(
        task_summaries: list[TaskSummary],
        threshold: float = 0.95,
    ) -> dict[str, Any]:
        """饱和度检测: 超过半数 task 的 pass@1 ≥ 0.95 → 建议加难"""
        if not task_summaries:
            return {"is_saturated": False, "saturation_ratio": 0.0}

        saturated_tasks = [
            ts.task_id for ts in task_summaries
            if ts.pass_at_k.get(1, 0.0) >= threshold
        ]
        saturation_ratio = len(saturated_tasks) / len(task_summaries)
        is_saturated = saturation_ratio > 0.5

        return {
            "is_saturated": is_saturated,
            "saturation_ratio": saturation_ratio,
            "saturated_tasks": saturated_tasks,
            "recommendation": (
                "评测已饱和, 建议增加更有挑战性的任务" if is_saturated else None
            ),
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _emit(
        self,
        callback: ProgressCallback | None,
        event: str,
        data: dict[str, Any],
    ) -> None:
        """发送进度事件"""
        if callback is not None:
            try:
                await callback(event, data)
            except Exception:
                # 回调不应中断主流程
                pass
