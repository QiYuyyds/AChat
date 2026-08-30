# 架构

Aeval 是一个由 OTel trace 驱动的 Agent 评测框架。本文描述它的模块划分、执行数据流与扩展点设计。

## 1. 定位

- **开发时工具**：开发者在本地或 CI 里运行评测，验证 Agent 改动是否引入退化；非生产环境持续运行
- **单向依赖**：`agent_eval` 不依赖任何宿主应用（`import app.*` 被测试固化为禁令）；宿主 → 框架单向依赖
- **自部署**：无用户系统 / 认证 / 权限；多租户不在范围内

## 2. 模块布局

```
agent_eval/
├── core/           # 编排与统计
│   ├── types.py      # 数据模型: EvalSuite/EvalTask/GraderConfig → TrialResult/TaskSummary/RunSummary/RunResult
│   ├── contract.py   # 协议: AgentRunner(必选)/TraceProvider/Grader/Storage/EnvironmentManager + TransientError/EvalContext
│   ├── suite.py      # YAML 加载 + 严格校验 (校验器在 Pydantic 模型上)
│   ├── metrics.py    # pass@k / pass^k 统计 (含 k>n 二项外推) + 过程指标聚合
│   └── runner.py     # EvalRunner — 核心编排器
├── graders/        # 9 个内置评分器 + 注册表
├── metrics/        # LLM 质量指标 (RAG 四件套/LLM judge/批量/报告/pytest 插件)
├── dataset/        # 数据集构建 (5 类数据源/质量检查/覆盖度/semver 升版)
├── storage/        # Memory + SQLite (runs/suites/人工评分请求)
├── trace/          # Phoenix TraceProvider (懒加载)
├── api/            # FastAPI 工厂: create_app (寄宿挂载) + create_standalone_app (/v1 独立)
│   └── routes/       # suites/tasks/runs(含 SSE 流)/compare/graders/datasets/metrics
├── examples/       # MockAgentRunner / MockTraceProvider
└── cli.py          # eval-suite 命令行 (typer)
```

## 3. 执行数据流

```
suite.yaml ──load──▶ EvalSuite
                        │
                        ▼
               ┌─── EvalRunner.run_suite ───┐
               │  (per task, N trials)       │
               │                             │
   snapshot ──▶│ setup → agent.run(prompt)   │── TransientError? ──▶ 指数退避重试
               │   │                         │── 超时?            ──▶ 记失败, 不重试
               │   ├─ trace_provider.spans   │── 单 trial 失败     ──▶ 继续其余 trial
               │   ├─ graders (拓扑序)        │
               │   ├─ teardown               │
               │   └─ verify_clean (泄漏检测) │── 泄漏 → restore + 告警(不判失败)
               └─────────────────────────────┘
                        │
                        ▼
              RunSummary (pass@k / pass^k / 一致性 / 饱和度)
                        │
                        ▼
              Storage (Memory / SQLite)  ──▶  API / CLI / Dashboard
```

关键行为约定：

- **并发模型**：trial 并发默认 1（`asyncio.Semaphore` 可调）；评分器内部另有并发上限
- **失败处理**：只有 `TransientError` 重试（实现方显式包装）；单 trial 失败不影响其他
- **泄漏检测**：`verify_clean` 报告不干净 → 自动 restore + 告警；trial 成败只由评分决定
- **评分依赖**：grader 按 `dependencies` 拓扑排序执行；依赖未通过 → 跳过并给 0 分解释
- **grader 缓存**：同 run 内按 prompt-hash 缓存评分结果（可关）
- **取消**：`POST /runs/{run_id}/cancel` → 后台任务取消，进行中的 trial 被打断，已完成部分保留可查

## 4. 统计语义

- **pass@k**（能力）：k 次尝试中至少成功一次的任务比例。`k ≤ n` 直接判定；`k > n` 按 `1 - (1-p)^k` 二项外推
- **pass^k**（可靠性）：k 次全部成功的比例。`k > n` 按 `p^k` 外推
- **一致性**：trial 间分数标准差（≥ 0.2 记不一致）
- **饱和度**：过半任务 pass@1 ≥ 0.95 → 报告饱和，提示任务缺挑战性
- **pending**：人工评分未回传的 trial 不计入通过率，回传后汇总重算

## 5. API 部署形态

| 形态 | 入口 | 前缀 | 说明 |
|------|------|------|------|
| 寄宿挂载 | `create_app()` | 由宿主决定（如 AChat `/api/eval`） | 挂进已有 FastAPI；可注入真实 runner |
| 独立部署 | `create_standalone_app()` | `/v1` | 复用 `create_app` 全部路由挂 `/v1`；`X-Aeval-Version` 头 + `/v1/meta` 能力清单 |

兼容承诺：同一大版本内 URL 与响应结构向后兼容；破坏性变更升 `/v2` 并保留并行期。SSE 流（`GET /runs/{run_id}/stream`）采用快照+增量协议，刷新可恢复。

## 6. 扩展点

全部协议见 `core/contract.py`（接入指南有实现示例）：

- `AgentRunner`（必选）— 执行任务，返回 `(trace_id, transcript, outcome)`
- `TraceProvider` — 从 trace 后端拉 span 数据
- `Grader` — 逐 trial 评分（`EvalContext` 贯穿共享状态）
- `Storage` — 持久化 runs/suites/人工评分请求
- `EnvironmentManager` — 每 trial 环境 setup/teardown/快照/泄漏检测/恢复

LLM 侧依赖统一经 `LLMFn` 回调（`(system, user) → text`）与指标注册表注入；缺配置返回带原因的 0 分结果而非崩溃。

## 7. Dashboard

`apps/dashboard`（Next.js 16 App Router）：总览 / Suites+YAML 导入 / Run 报告（SSE 实时）/ Trial 下钻（transcript、grader 分解、Phoenix 外链）/ A/B 对比 / 数据集管理 / Task 库 / Settings。经 rewrites 把 `/api/eval/*` 代理到后端（`EVAL_BACKEND_URL`）。

## 8. 测试策略

- 纯单元：core 统计 / suite 校验 / graders / storage（覆盖率目标 ≥90%，`scripts/check_eval_coverage.sh`）
- MockRunner 端到端：超时隔离 / 瞬态重试 / 依赖跳过 / 泄漏检测的完整行为矩阵
- API：CliRunner / TestClient / 挂载回归（宿主侧）
- 导入隔离：AST 扫描 `agent_eval` 拒绝 `app.*`（`tests/test_import_isolation.py`）
