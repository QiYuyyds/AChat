# Aeval 设计系统性审查 — 历史归档

> **归档说明**: 本文原为 `docs/eval-harness-design.md` §17.13，于 v0.10 (2026-08-29) 归档独立成文。
> 权威状态口径: v0.7 已修复 13 项缺陷，延后 2 项 (Prompt 注入 / 资源限制，均 P2)，见主文档附录 C 变更记录。
> 主文档正文现仅保留"风险 → 防御机制"映射表；本文为审查过程的原始记录，不再随正文更新。

---


> 本节系统性地审查当前设计方案的瑕疵、漏洞和与参考资料的不一致

### 17.13.1 与 Anthropic 评测原则的对齐度审查

| Anthropic 原则 | 当前设计 | 对齐度 | 问题 |
|---|---|---|---|
| **Task → Trial → Transcript → Outcome** | ✅ 完整覆盖 | ⭐⭐⭐⭐⭐ | 无 |
| **多种 Grader (code/model/human)** | ✅ 6 个内置 + 自定义 | ⭐⭐⭐⭐ | Human Grader 未设计 |
| **Capability vs Regression** | ✅ pass@k / pass^k 区分 | ⭐⭐⭐⭐⭐ | 无 |
| **环境隔离** | ✅ EnvironmentManager | ⭐⭐⭐⭐ | 缺少环境快照/恢复机制 |
| **多 Trial 聚合** | ✅ asyncio.gather | ⭐⭐⭐⭐ | 缺少 trial 间一致性检查 |
| **评估过程而不仅是结果** | ⚠️ tracked_metrics | ⭐⭐⭐ | 缺少 step-level 评分 |
| **Eval 即代码** | ✅ YAML + Python | ⭐⭐⭐⭐⭐ | 无 |
| **迭代改进** | ⚠️ 版本管理 | ⭐⭐⭐ | 缺少 eval 结果驱动的迭代建议 |
| **Signal vs Noise** | ⚠️ 基础覆盖 | ⭐⭐⭐ | 缺少统计显著性检验 |
| **Eval Saturation** | ❌ 无 | ⭐ | 缺少饱和度检测 |

### 17.13.2 设计缺陷 (Critical)

#### 缺陷 1: pass@k 计算逻辑有 Bug

> ✅ **已修复** — 见 §6.3 `_compute_summary()` 中的 `pass_at_k()` / `pass_power_k()` 实现

```python
# 当前实现
def pass_at_k(trials, k):
    successes = sum(1 for t in trials if t.success)
    return min(1.0, successes / k)  # ❌ 错误!
```

**问题**: 当 `trials=3, k=1` 时, 如果 3 次全成功, `pass@1 = 3/3 = 1.0` (正确);
但如果 `trials=3, k=3`, 如果 1 次成功, `pass@3 = 1/3 = 0.33` (错误, 应该是 1.0, 因为 3 次中至少 1 次成功)。

**正确实现**:
```python
def pass_at_k(trials, k):
    """k 次尝试中至少一次成功的概率估计"""
    n = len(trials)
    if n == 0:
        return 0.0
    successes = sum(1 for t in trials if t.success)
    # 如果 k <= n, 直接检查是否有成功
    if k <= n:
        return 1.0 if successes > 0 else 0.0
    # 如果 k > n, 用二项分布估计
    # 单次成功概率 p = successes / n
    # P(至少一次成功 in k 次) = 1 - (1-p)^k
    p = successes / n
    return 1.0 - (1.0 - p) ** k

def pass_power_k(trials, k):
    """k 次尝试全部成功的概率估计"""
    n = len(trials)
    if n == 0:
        return 0.0
    successes = sum(1 for t in trials if t.success)
    # 如果 k <= n, 检查前 k 次是否全成功
    if k <= n:
        return 1.0 if all(t.success for t in trials[:k]) else 0.0
    # 如果 k > n, 用二项分布估计
    p = successes / n
    return p ** k
```

#### 缺陷 2: Trial 间环境泄漏

> ✅ **已修复** — 见 §6.2 `_run_trial()` 中的环境泄漏检测逻辑 (snapshot/verify/restore)

当前 `EnvironmentManager.setup()/teardown()` 在每次 trial 前后调用, 但没有验证环境是否真正被清理。

```python
# 当前设计缺少环境验证
async def _run_trial(self, task, index):
    await self.environment.setup(task)  # 假设 setup 创建干净环境
    # ... run agent ...
    await self.environment.teardown(task)  # 假设 teardown 清理环境
    # ❌ 没有验证环境是否真的被清理干净!
```

**建议**:
```python
async def _run_trial(self, task, index):
    env_snapshot_before = await self.environment.snapshot()
    await self.environment.setup(task)
    
    # ... run agent ...
    
    await self.environment.teardown(task)
    env_snapshot_after = await self.environment.snapshot()
    
    # 验证环境已恢复
    if env_snapshot_before != env_snapshot_after:
        logger.warning(f"环境泄漏 detected in trial {index}")
        # 强制恢复
        await self.environment.restore(env_snapshot_before)
```

#### 缺陷 3: 缺少 Human-in-the-Loop 评分路径

> ✅ **已修复** — 见 §8.2 `HumanGrader` 完整设计 + Dashboard 评分 UI 预留

Anthropic 文档强调 Human Grader 是评估链的重要环节, 但当前设计只有 code/model 两种。

```python
# 缺少 HumanGrader
class HumanGrader(Grader):
    """人类评分器 — 将评分任务推送给人类专家"""
    
    async def grade(self, trial, spans, task):
        # 1. 生成人类评分任务
        human_task = self._create_human_task(trial, task)
        
        # 2. 推送给人类 (Dashboard / 邮件 / Slack)
        await self._dispatch_to_human(human_task)
        
        # 3. 等待人类评分 (异步)
        human_result = await self._wait_for_human_score(human_task.id)
        
        return GraderResult(
            grader_name="human",
            score=human_result.score,
            passed=human_result.score >= task.score_threshold,
            explanation=human_result.feedback,
            details=human_result.details,
        )
```

#### 缺陷 4: Grader 结果缺少 Confidence 区间

> ✅ **已修复** — 见 §4.2 `GraderResult` 已添加 `confidence`, `uncertainty`, `sample_count` 字段

当前 GraderResult 只有一个 `score: float`, 没有置信度信息。

```python
# 当前设计
class GraderResult(BaseModel):
    score: float           # 单一分数
    passed: bool           # 二元判定
    # ❌ 缺少 confidence, uncertainty

# 建议
def GraderResult(BaseModel):
    score: float
    confidence: float = 1.0   # 置信度 (0-1)
    uncertainty: float = 0.0  # 不确定性 (标准差)
    passed: bool
```

对于 LLM Judge, 可以通过多次采样来估计 confidence:
```python
# LLM Judge 多次采样
scores = []
for _ in range(3):
    score = await self._judge_once(trial, rubric)
    scores.append(score)

final_score = sum(scores) / len(scores)
uncertainty = (max(scores) - min(scores)) / 2
confidence = 1.0 - uncertainty
```

#### 缺陷 5: 缺少 Eval Saturation 检测

> ✅ **已修复** — 见 §6.3 `_compute_summary()` 中的 `_detect_saturation()` 实现

Anthropic 文档强调: 当所有评测用例都被 Agent 100% 通过时, 说明评测已经"饱和", 需要更有挑战性的用例。

```python
# 缺少饱和度检测
class EvalSaturationDetector:
    """检测评测是否饱和 (所有任务都被轻松通过)"""
    
    def detect(self, run_result: RunResult, threshold: float = 0.95) -> dict:
        """返回饱和度报告"""
        saturated_tasks = []
        for task_id, trials in run_result.trials.items():
            pass_rate = sum(1 for t in trials if t.success) / len(trials)
            if pass_rate >= threshold:
                saturated_tasks.append(task_id)
        
        saturation_ratio = len(saturated_tasks) / len(run_result.trials) if run_result.trials else 0
        
        return {
            "saturated_tasks": saturated_tasks,
            "saturation_ratio": saturation_ratio,
            "is_saturated": saturation_ratio > 0.5,
            "recommendation": "需要更有挑战性的任务" if saturation_ratio > 0.5 else None,
        }
```

### 17.13.3 设计缺陷 (Major)

#### 缺陷 6: 缺少 Step-Level 评估

> ✅ **已修复** — 见 §8.2 `StepLevelGrader` 完整设计 + §8.1 清单已收录

Anthropic 文档强调 "评估过程而不仅是结果"。当前设计只有 trial-level 评分, 没有 step-level 评分。

```python
# 当前: 只对最终结果评分
trial.success = True/False  # 二元判定

# 缺少: 对每一步的评分
step_results = [
    {"step": 0, "action": "read_file", "correct": True},
    {"step": 1, "action": "analyze", "correct": True},
    {"step": 2, "action": "write_output", "correct": False},  # 这一步做错了!
]
```

**建议**: 在 Grader 中支持 step-level 分析
```python
class StepLevelGrader(Grader):
    """步骤级评分器 — 分析每一步的正确性"""
    
    async def grade(self, trial, spans, task):
        # 从 spans 提取步骤信息
        steps = self._extract_steps(spans)
        
        step_scores = []
        for step in steps:
            # 评估每一步的正确性
            score = await self._evaluate_step(step, task)
            step_scores.append(score)
        
        return GraderResult(
            score=sum(step_scores) / len(step_scores),
            details={"step_scores": step_scores},
        )
```

#### 缺陷 7: 缺少 Prompt Injection 防御

> ⏳ **延后 P2** — 当前 AgentRunner 直接将 prompt 传给 Agent, 没有安全检查。建议增加 `PromptSecurityChecker` 层。

当前 AgentRunner 直接将 prompt 传给 Agent, 没有安全检查。

```python
# 当前设计缺少安全检查
class EvalRunner:
    async def _run_trial(self, task, index):
        trace_id, transcript, outcome = await self.agent_runner.run(task)
        # ❌ 没有检查 prompt 是否包含注入攻击
```

**建议**: 增加安全检查层
```python
class PromptSecurityChecker:
    """Prompt 安全检查器"""
    
    def check(self, prompt: str) -> dict:
        """检查 prompt 是否包含注入攻击"""
        return {
            "has_injection": False,
            "risk_level": "low",
            "details": [],
        }
```

#### 缺陷 8: 缺少 Eval 版本追踪

> ✅ **已修复** — 见 §4.1 `EvalSuite` 已添加 `version`, `commit_hash`, `created_at`, `updated_at` 字段 + §18.5.1 语义化版本管理

当前 Suite 只有简单的 name, 没有完整的版本历史。

```python
# 当前设计
class EvalSuite(BaseModel):
    name: str
    tasks: list[EvalTask]
    # ❌ 没有 version, commit_hash, created_at

# 建议
def EvalSuite(BaseModel):
    name: str
    version: str = "1.0.0"
    commit_hash: str = ""  # Git commit SHA
    created_at: float
    updated_at: float
    tasks: list[EvalTask]
    metadata: dict = {}
```

#### 缺陷 9: 缺少 Trial 间一致性检查

> ✅ **已修复** — 见 §6.3 `_compute_summary()` 中的 `_check_trial_consistency()` 调用 + `TaskSummary.consistent` / `score_std_dev` 字段

当前 design 运行多个 trial 但没有检查 trial 间的一致性。

```python
# 缺少一致性检查
class TrialConsistencyChecker:
    """检查多个 trial 的结果是否一致"""
    
    def check(self, trials: list[TrialResult]) -> dict:
        scores = [t.grader_results[0].score for t in trials if t.grader_results]
        if not scores:
            return {"consistent": False, "reason": "No scores"}
        
        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        std_dev = variance ** 0.5
        
        return {
            "consistent": std_dev < 0.2,  # 标准差 < 0.2 认为一致
            "std_dev": std_dev,
            "variance": variance,
            "scores": scores,
        }
```

#### 缺陷 10: 缺少 Grader 结果缓存

> ✅ **已修复** — 见 §6.1 `EvalRunner.__init__()` 中的 `_grader_cache` + `enable_grader_cache` 配置

Model-based grader 调用 LLM 很贵, 相同输入没有缓存。

```python
# 缺少缓存机制
class CachedModelGrader(ModelBasedGrader):
    """带缓存的 LLM Judge"""
    
    def __init__(self, cache: Cache | None = None):
        super().__init__()
        self.cache = cache or MemoryCache()
    
    async def grade(self, trial, spans, task):
        # 计算缓存 key
        cache_key = self._compute_cache_key(trial, task)
        
        # 检查缓存
        cached = await self.cache.get(cache_key)
        if cached:
            return cached
        
        # 调用 LLM
        result = await super().grade(trial, spans, task)
        
        # 存入缓存
        await self.cache.set(cache_key, result, ttl=3600)
        return result
```

### 17.13.4 设计缺陷 (Minor)

#### 缺陷 11: RunResult 存储可能过大

> ✅ **已修复** — 见 §9.2 `SqliteStorage` 已分离 runs/trials/suites 表 + `list_runs()` 只返回元数据

当前设计将整个 RunResult 作为 JSON 存入 SQLite。如果 trial 数多、transcript 长, 单条记录可能很大。

```python
# 建议: 分离大字段
class SqliteStorage:
    async def save_run(self, run: RunResult):
        # 主表: 只存元数据
        await db.execute(
            "INSERT INTO runs (...) VALUES (...)",
            (run.run_id, run.suite_name, run.status, ...)
        )
        
        # 明细表: trial 数据单独存储
        for task_id, trials in run.trials.items():
            for trial in trials:
                await db.execute(
                    "INSERT INTO trials (run_id, task_id, data) VALUES (?, ?, ?)",
                    (run.run_id, task_id, json.dumps(trial.dict())),
                )
```

#### 缺陷 12: 缺少重试机制

> ✅ **已修复** — 见 §6.2 `_run_task_with_retries()` 中的指数退避重试逻辑 + `max_trial_retries` 配置

Agent 执行可能因为网络/超时等原因失败, 当前设计没有重试。

```python
# 缺少重试
async def _run_trial_with_retry(self, task, index, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return await self._run_trial(task, index)
        except TransientError as e:
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)  # 指数退避
                continue
            raise
```

#### 缺陷 13: 缺少资源限制

> ⏳ **延后 P2** — 建议增加 `ResourceLimitedRunner` 包装器 (cgroup/rlimit)。当前依赖 AgentRunner 实现方自行控制。

Agent 执行可能消耗大量内存/CPU, 没有资源限制。

```python
# 缺少资源限制
class ResourceLimitedRunner:
    def __init__(self, max_memory_mb=1024, max_cpu_percent=80):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_percent = max_cpu_percent
    
    async def run(self, task):
        # 设置资源限制
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (self.max_memory_mb * 1024 * 1024, -1))
        return await self._inner_runner.run(task)
```

#### 缺陷 14: 缺少时区处理

> ✅ **已修复** — 见 §4.1 所有时间戳统一使用 `now_ms()` 返回 UTC epoch ms, 并在文档中明确标注

所有时间戳都是 epoch ms, 但没有时区信息。

```python
# 建议: 统一使用 UTC
def now_ms() -> float:
    """返回 UTC 时间戳 (毫秒)"""
    import time
    return time.time() * 1000
```

#### 缺陷 15: 缺少输入验证

> ✅ **已修复** — 见 §4.1 `EvalSuite` 已添加 `@validator` (tasks 非空/唯一性/长度限制) + `GraderConfig` 已添加 `@validator` (name/weight/sample_count)

当前设计没有对 Suite YAML 进行严格的输入验证。

```python
# 建议: 增加严格验证
class EvalSuite(BaseModel):
    # ...
    
    @validator('tasks')
    def validate_tasks(cls, v):
        if not v:
            raise ValueError("Suite must have at least one task")
        if len(v) > 10000:
            raise ValueError("Too many tasks (max 10000)")
        
        # 检查 task id 唯一性
        ids = [t.id for t in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task IDs")
        
        return v
```

### 17.13.5 功能缺失清单

| 缺失功能 | 影响 | 优先级 | 建议 |
|---|---|---|---|
| **Human Grader** | 无法让人类专家评分 | P0 | 增加 HumanGrader + Dashboard 评分 UI |
| **Eval Saturation 检测** | 评测饱和后无法感知 | P0 | 增加饱和度检测 + 告警 |
| **Step-Level 评估** | 无法分析中间步骤 | P1 | 增加 StepLevelGrader |
| **统计显著性检验** | A/B 对比无置信度 | P1 | 增加 t-test / bootstrap |
| **环境快照/恢复** | 环境泄漏无法检测 | P1 | 增加 snapshot/restore |
| **Trial 间一致性** | 结果波动大无法感知 | P1 | 增加一致性检查 |
| **Grader 结果缓存** | LLM Judge 重复付费 | P1 | 增加结果缓存 |
| **Prompt 安全检查** | 注入攻击风险 | P2 | 增加安全检查层 |
| **Eval 版本追踪** | 无法回溯历史 | P2 | 增加 Git 集成 |
| **资源限制** | Agent 可能耗尽资源 | P2 | 增加 cgroup/rlimit |

### 17.13.6 架构层面的建议

#### 建议 1: 引入 Pipeline 概念

当前 Grader 是独立运行的, 建议引入 Pipeline 支持 Grader 间的依赖关系。

```python
# 当前: Grader 之间无依赖
graders = [grader_a, grader_b, grader_c]

# 建议: Pipeline 支持依赖
pipeline = Pipeline([
    ("state_check", [], {"required": True}),      # 无依赖
    ("tool_calls", ["state_check"], {"required": True}),  # 依赖 state_check 通过
    ("model_based", ["state_check"], {"required": False}), # 依赖 state_check 通过
])
```

#### 建议 2: 引入 Eval Context

当前 Grader 之间无法共享状态, 建议引入 Context 对象。

```python
@dataclass
class EvalContext:
    """评测上下文 — 在 Grader 间共享"""
    run_id: str
    task: EvalTask
    trial: TrialResult
    spans: list[dict]
    shared_state: dict[str, Any] = {}  # Grader 可以写入
    
    def set(self, key: str, value: Any):
        self.shared_state[key] = value
    
    def get(self, key: str, default=None):
        return self.shared_state.get(key, default)
```

#### 建议 3: 分离 Eval Suite 定义和执行

当前 EvalSuite 既定义任务又包含执行配置, 建议分离。

```python
# 当前: 定义 + 执行混在一起
class EvalSuite(BaseModel):
    name: str
    tasks: list[EvalTask]  # 定义
    # 执行配置?

# 建议: 分离
class EvalSuiteDefinition(BaseModel):
    """Suite 定义 (可复用, 可版本化)"""
    name: str
    version: str
    tasks: list[EvalTask]

class EvalRunConfig(BaseModel):
    """执行配置 (每次运行独立)"""
    suite_name: str
    suite_version: str
    concurrency: int
    max_trials: int
    environment: dict
```

### 17.13.7 总结

| 类别 | 数量 | 关键问题 |
|---|---|---|
| **Critical (必须修复)** | 5 | pass@k 逻辑错误, 环境泄漏, 缺 Human Grader, 缺 confidence, 缺饱和度检测 |
| **Major (应该修复)** | 5 | 缺 step-level 评估, 缺安全检查, 缺版本追踪, 缺一致性检查, 缺缓存 |
| **Minor (可以修复)** | 5 | 存储过大, 缺重试, 缺资源限制, 缺时区, 缺输入验证 |
| **功能缺失** | 10 | Human Grader, Saturation, Step-Level, 显著性检验 等 |
| **架构建议** | 3 | Pipeline, Eval Context, 定义/执行分离 |

**建议优先修复顺序**:
1. 修复 pass@k 计算逻辑 (Critical, 影响所有统计结果)
2. 增加环境快照/恢复 (Critical, 影响评测可靠性)
3. 增加 Human Grader (Critical, 对齐 Anthropic 原则)
4. 增加 Eval Saturation 检测 (Critical, 评测有效性)
5. 增加 Grader confidence (Critical, 评分可靠性)
6. 增加 Step-Level 评估 (Major, 对齐 Anthropic 原则)
7. 增加 Pipeline 概念 (架构, Grader 依赖管理)
8. 增加缓存机制 (Major, 成本优化)
