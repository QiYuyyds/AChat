## ADDED Requirements

### Requirement: 在线规则评测

系统 SHALL 在每次 agent run 结束后自动执行规则评测，从 trace span 数据计算指标，不调用 LLM，结果写入 Phoenix 作为 trace 级 eval annotation。

- `backend/app/observability/eval_rules.py` SHALL 提供 `run_rule_evaluations(trace_id, spans) -> list[EvalScore]`
- 评测 SHALL 默认开启（`eval_rule_enabled=True`）
- 评测 SHALL 异步执行（`asyncio.create_task`），不阻塞 agent run 响应
- 评测结果 SHALL 通过 Phoenix Python SDK 写入，挂在对应 trace 上
- 在线规则指标 SHALL 包含以下全部指标：

| 指标 | 计算方式 |
|------|---------|
| Task Completion | 最后一轮 `llm.generate` 的 `finish_reason == "end_turn"` ? 1.0 : 0.0 |
| Max Turns Exceeded | `total_turns >= MAX_TURNS` ? 0.0 : 1.0 |
| Tool Success Rate | `成功工具调用数 / 总工具调用数` |
| Redundant Tool Calls | `重复(工具名+参数)调用数 / 总调用数` |
| Turns to Complete | `total_turns` |
| Token Usage | `sum(input_tokens) + sum(output_tokens)` |
| Latency per Turn | `duration_ms / total_turns` |
| Tool vs LLM Time Ratio | `sum(tool duration) / sum(llm duration)` |
| Dispatch Depth | `max(dispatch_depth)` |
| Parallelism Degree | `max(并发子 agent run 数)` |
| Subagent Count | `count(tool.dispatch spans)` |
| Subagent Task Completion | `子 agent end_turn 比例` |
| Error Detection | `有 error span ? 0.0 : 1.0` |

#### Scenario: 在线评测自动执行

- **WHEN** 一次 agent run 完成
- **THEN** 系统自动从该 trace 的 span 数据计算全部规则指标
- **AND** 结果写入 Phoenix，作为该 trace 的 eval annotation
- **AND** Phoenix UI 中该 trace 下方显示所有规则评分

#### Scenario: 在线评测不阻塞主链路

- **WHEN** agent run 完成后触发在线评测
- **THEN** 评测 SHALL 通过 `asyncio.create_task` 异步执行
- **AND** agent run 的 HTTP 响应不等待评测完成

#### Scenario: 在线评测关闭

- **WHEN** `eval_rule_enabled=False`
- **THEN** agent run 完成后不执行规则评测
- **AND** 不产生 eval annotation

### Requirement: 离线 LLM-as-judge 评测

系统 SHALL 提供手动触发的 LLM-as-judge 评测，通过 API 对指定 trace 调用 DashScope LLM 进行深度评判，结果写入 Phoenix 作为 trace 级 eval annotation。

- `POST /api/eval/judge/{trace_id}` SHALL 触发 LLM-as-judge 评测
- 评测 SHALL 默认关闭（`eval_judge_enabled=False`），`eval_judge_enabled=False` 时 API 返回 403
- 评测 SHALL 从 Phoenix 拉取指定 trace 的 span 数据 + LLM 输入输出摘要
- 评测 SHALL 调用 DashScope LLM（复用已有 `LLM_API_KEY` + `LLM_MODEL` 配置）
- 评测结果 SHALL 写回 Phoenix，作为同一 trace 的 eval annotation
- LLM-as-judge 指标 SHALL 包含以下全部指标：

| 指标 | LLM 判定内容 |
|------|-------------|
| Tool Selection Accuracy | 模型选的工具对不对（给定任务 + 工具列表 + 实际选择） |
| Subtask Granularity | Orchestrator 拆的子任务粒度是否合理 |
| Subtask Overlap | 子任务是否有重叠 |
| Subtask Coverage | 子任务是否覆盖了用户需求 |
| Aggregation Fidelity | 最终回答是否整合了子 Agent 产出 |
| Information Loss | 子 Agent 产出在聚合中丢失比例 |
| Faithfulness | 回答是否忠于检索内容 |
| Answer Relevance | 回答是否切题 |
| Answer Quality | 综合质量 |

#### Scenario: 手动触发 LLM-as-judge

- **WHEN** 调用 `POST /api/eval/judge/trace_xxx` 且 `eval_judge_enabled=True`
- **THEN** 系统从 Phoenix 拉取该 trace 的 span 数据
- **AND** 调用 DashScope LLM 对 9 个指标进行评判
- **AND** 结果写回 Phoenix，作为该 trace 的 eval annotation
- **AND** API 返回评测结果 JSON

#### Scenario: LLM-as-judge 关闭时拒绝

- **WHEN** `eval_judge_enabled=False` 且调用 `POST /api/eval/judge/trace_xxx`
- **THEN** API 返回 403 Forbidden
- **AND** 不触发 LLM 调用

#### Scenario: trace 不存在

- **WHEN** 调用 `POST /api/eval/judge/nonexistent_trace` 且 trace 在 Phoenix 中不存在
- **THEN** API 返回 404 Not Found

### Requirement: Agent 全过程评测指标体系

系统 SHALL 定义 Agent 全过程评测的 5 个维度指标体系，涵盖任务完成、工具调用质量、步骤效率、提示词效果、回答质量。

- **任务完成维度** SHALL 包含：Task Completion Rate、Output Artifact Exists、Max Turns Exceeded
- **工具调用质量维度** SHALL 包含：Tool Success Rate、Tool Selection Accuracy、Tool Call Efficiency、Redundant Tool Calls
- **步骤效率维度** SHALL 包含：Turns to Complete、Token Efficiency、Latency per Turn、Tool vs LLM Time Ratio
- **提示词效果维度** SHALL 包含：RAG Injection Impact、Memory Injection Impact、Prompt Version A/B
- **回答质量维度** SHALL 包含：Faithfulness、Answer Relevance、Answer Quality
- 各指标 SHALL 明确标注评测方式（在线规则 / LLM-as-judge / 离线对比）

#### Scenario: 全过程指标可查

- **WHEN** 测试人员在 Phoenix UI 查看一个 solo agent run trace
- **THEN** 该 trace 的 eval annotation SHALL 包含任务完成、工具调用质量、步骤效率维度的在线规则评分
- **AND** 若已手动触发 LLM-as-judge，还包含回答质量维度的 LLM 评分

### Requirement: 多 Agent 协作评测指标体系

系统 SHALL 定义多 Agent 协作评测的 4 个维度指标体系，涵盖任务拆解质量、调度效率、子 Agent 质量、聚合质量。

- **任务拆解质量维度** SHALL 包含：Subtask Count、Subtask Granularity、Subtask Overlap、Subtask Coverage
- **调度效率维度** SHALL 包含：Dispatch Depth、Parallelism Degree、Sequential Bottleneck、Total Agent Runs
- **子 Agent 质量维度** SHALL 包含：Subagent Task Completion、Subagent Output Relevance、Subagent Token Waste
- **聚合质量维度** SHALL 包含：Aggregation Fidelity、Information Loss、Redundancy in Final Output
- 多 Agent 协作指标 SHALL 仅在 coordinated / subagent 模式的 trace 上计算（solo 模式无派发数据时跳过）

#### Scenario: 协作指标仅在多 Agent 场景计算

- **WHEN** 一次 coordinated 模式 agent run 完成
- **THEN** 在线规则评测 SHALL 计算调度效率维度指标（Dispatch Depth、Parallelism Degree 等）
- **AND** LLM-as-judge 评测 SHALL 计算任务拆解质量与聚合质量维度指标

#### Scenario: solo 模式跳过协作指标

- **WHEN** 一次 solo 模式 agent run 完成
- **THEN** 协作维度指标 SHALL 跳过（无 `tool.dispatch` span）
- **AND** 全过程维度指标正常计算

### Requirement: Trace + Eval 一体化展示

系统 SHALL 通过 Phoenix 原生能力实现 trace 数据与评测评分的一体化展示。

- 在线规则评测结果 SHALL 在 agent run 完成后立即写入 Phoenix，作为 trace 级 eval annotation
- LLM-as-judge 评测结果 SHALL 在手动触发后写入 Phoenix，作为同一 trace 的 eval annotation
- Phoenix UI 中同一 trace 的瀑布流下方 SHALL 直接展示所有 eval scores
- Phoenix UI SHALL 支持按 eval score 筛选和排序 trace 列表

#### Scenario: trace + eval 一体化查看

- **WHEN** 测试人员在 Phoenix UI 打开一个已完成在线评测的 trace
- **THEN** 瀑布流下方 SHALL 显示所有规则评测评分
- **AND** 若已手动触发 LLM-as-judge，还显示 LLM 评判评分
- **AND** 两种评测结果在同一页面展示，无需跳转

#### Scenario: 按 eval score 筛选

- **WHEN** 测试人员在 Phoenix UI 按 `Tool Success Rate < 0.5` 筛选 trace
- **THEN** 列表 SHALL 只展示工具成功率低于 50% 的 trace
