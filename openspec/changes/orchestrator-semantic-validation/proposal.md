## Why

当前的证据门控系统用正则匹配和精确字符串集合做语义判断——`python -c` 不在 13 个验证命令正则白名单里就判失败，agent 没逐字复制 criteria 字符串就判"missing acceptance criteria"。这些本该是 LLM 做的语义判断，却用确定性规则实现，导致单文件任务、自定义验证脚本、非标准 build 命令被系统性误判。项目本身已支持自主创建审查检验 Agent，硬编码门控不仅能力不足，更是多此一举。

## What Changes

- **evaluate_task_result_report 降级为 advisory evidence collector** **BREAKING**：返回值从 `TaskResultReportEvaluation(ok: bool, error: str)` 改为 `TaskEvidenceSummary(advisory_issues: list[str], evidence: RunToolEvidence)`，不再阻断流程。8 道串行 fail-fast 门控中的 ⑤(criteria 精确字符串匹配)⑧(verification 命令正则白名单)⑨(required evidence 子串匹配)降级为 advisory——收集到"可能的问题"但不返回 fail。
- **Orchestrator LLM 语义校验**：在子 Agent `report_task_result` 之后、DAG 继续之前，调用 Orchestrator LLM 做一次语义校验。输入为 task contract + agent report + advisory evidence summary，输出为 pass/fail + 具体反馈文本。替代硬编码门控的判断角色。
- **审查 Agent 委托校验**：当 DAG 中存在审查检验 Agent（`dependsOn` 实现任务的 review task）时，Orchestrator 将校验委托给该 Agent。审查 Agent 报告问题后，Orchestrator 在 replan 时把审查反馈写进 continuation prompt，驱动实现 Agent 重试。
- **派发时附带明确约束**：Orchestrator 在 `plan_tasks` 的 task 描述中写清约束条件（引用资料、代码注释、输出格式等），作为子 Agent 的行为约束。子 Agent 保留自主决策权，Orchestrator 仅督促不改写。
- **DAG 结构不变**：反馈环通过 replan（新 dispatch round）实现，不引入 DAG 回边。每个 round 仍是标准 DAG，审查 Agent 报告问题 → Orchestrator replan → 新 round 中实现 Agent 带 feedback 重试。
- **去掉 normalize_task_contract 自动追加**：`CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` 和 `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE` 不再自动追加到 code task 的 contract 中。是否需要构建验证由 Orchestrator LLM 在派发时根据 workspace 类型自行决定，写进 task 描述。

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `orchestrator`：任务完成校验从确定性门控改为 Orchestrator LLM 语义仲裁；`evaluate_task_result_report` 降级为 advisory evidence collector；审查 Agent 可通过 DAG + replan 实现反馈环；派发时附带明确约束；`normalize_task_contract` 不再自动追加硬编码 criteria/evidence。

## Impact

- **`task_result_report.py`**：`evaluate_task_result_report` 返回类型从 `TaskResultReportEvaluation` 改为 `TaskEvidenceSummary`；8 道门控逻辑从 fail-fast 改为 advisory issue collection。
- **`orchestrator.py`**：新增 `_evaluate_with_llm` 方法，在 `_evaluate_child_task_result` 中调用 Orchestrator LLM 做语义校验；replan continuation prompt 使用 LLM 反馈替代 error string。
- **`orchestrator_prompts.py`**：新增 evaluation prompt template（喂给 Orchestrator LLM 的校验提示词）；子 Agent prompt 已有约束传递机制，增强约束写法指导。
- **`dispatch_plan.py`**：`normalize_task_contract` 移除 `CODE_TASK_RUNNABLE_*` 自动追加逻辑；`_append_unique` 对这些常量的调用删除。
- **`agent_runner.py`**：`consume_stream` 的 `require_task_report` 终止行为不变（已在 `fix-dispatch-evidence-grading` 中实现）。
- **Tests**：现有 `test_task_result_evaluate.py` 需大改（返回类型变了）；新增 LLM evaluation 的 mock 测试。
