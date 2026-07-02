## Context

当前 `evaluate_task_result_report` 用 8 道串行 fail-fast 门控判断子任务是否完成。其中 ⑤(criteria 精确字符串 set 匹配)、⑧(verification 命令 13 个正则白名单)、⑨(required evidence 子串匹配) 用确定性规则做语义判断，导致单文件任务、自定义验证脚本、非标准 build 命令被系统性误判。

项目已支持自主创建审查检验 Agent（DAG 中的 review task），且 Orchestrator 本身就是 LLM 驱动的。硬编码门控做的事——"这个结果够不够好"——本质是语义判断，应该交给 LLM 做。

前置变更 `fix-dispatch-evidence-grading` 已完成：`report_task_result` 终止化、workspace 分级、前缀救回、`dispatch.retry` 事件。本变更在此基础上把判断逻辑从规则切换到 LLM。

## Goals / Non-Goals

**Goals:**
- 用 Orchestrator LLM 替代 ⑤⑧⑨ 三道硬编码门控做语义判断
- `evaluate_task_result_report` 降级为 advisory evidence collector，不再阻断
- 审查 Agent 通过 DAG + replan 实现反馈环
- 派发时附带明确约束，子 Agent 保留自主决策

**Non-Goals:**
- 改变 DAG 调度模型（不引入回边、不做中途提醒）
- 改变 harness loop 的 attempt/replan 计数（MAX_CHILD_TASK_ATTEMPTS=4, MAX_DISPATCH_ROUNDS=4）
- 改变 `report_task_result` 协议结构
- 删除 ③(failed commands) 和 ⑥(target paths) 的客观检查——它们降级为 advisory 但仍然收集
- 新增独立 LLM evaluator 模块——复用 Orchestrator 自身的 adapter

## Decisions

### Decision 1: 复用 Orchestrator adapter 做 LLM 校验，不新增模块

**Choice**: 在 `_evaluate_child_task_result` 中，当 `evaluate_task_result_report` 返回 advisory issues 时，调用 Orchestrator 的 adapter 做一次单轮 LLM 校验。输入为 task contract + agent report + advisory issues，输出为 `{pass: bool, feedback: str}`。

**Alternative**: 新增独立的 `LLMEvaluator` 类，配置独立的 model/temperature。Rejected——项目已有 Orchestrator adapter 和 prompt 体系，新增模块增加维护成本且与"审查 Agent 可自主创建"的设计冲突。

**Implementation**: 新增 `_evaluate_with_llm` 方法在 `orchestrator.py` 中。构造一个精简 prompt（不含 plan_tasks 工具，只含一个返回 JSON 的指令），调用 `consume_stream` 做单轮推理。LLM 返回 `{"pass": true/false, "feedback": "..."}` JSON。

### Decision 2: advisory evidence collector 的返回结构

**Choice**: `evaluate_task_result_report` 返回新类型 `TaskEvidenceSummary`：

```python
@dataclass
class TaskEvidenceSummary:
    advisory_issues: list[str]   # 旧门控发现的可能问题（不阻断）
    has_report: bool             # agent 是否调用了 report_task_result
    report_status: str | None    # agent 自报告的 status
    evidence: RunToolEvidence     # 原始客观证据
```

旧 `TaskResultReportEvaluation(ok, error)` 的消费者（`_evaluate_child_task_result`）改为：
1. 调用 `evaluate_task_result_report` 获取 `TaskEvidenceSummary`
2. 如果 `not has_report` 或 `report_status != "complete"` → 直接 fail（这两条保留为硬规则，不是语义判断）
3. 否则调用 `_evaluate_with_llm(task, report, summary)` → 获取 pass/fail + feedback
4. 如果 LLM 说 fail → harness loop 用 feedback 作为 continuation prompt

**保留为硬规则的检查**（不降级）：
- ② `report.status != "complete"` → 仍然直接 fail（agent 自己说没完成，不需要 LLM 判断）
- ④ `acceptanceResults` 里有 `passed=false` → 仍然直接 fail（agent 自己说某条没通过）

**降级为 advisory 的检查**：
- ③ failed commands → advisory issue "有失败命令: python -c bad (exit 1)"
- ⑤ criteria coverage → advisory issue "task 声明了 criteria 'X' 但 report 中未精确匹配"（LLM 可判断是否语义等价）
- ⑥ target paths → advisory issue "target path 'src/foo.py' 未在 file_writes 中找到"
- ⑦ required commands → advisory issue "required command 'pytest' 未在 evidence 中找到成功记录"
- ⑧ verification command → advisory issue "未找到白名单内的 verification 命令成功记录"
- ⑨ required evidence → advisory issue "required evidence 'screenshot attached' 未在 report 中找到"

### Decision 3: 审查 Agent 通过 replan 实现反馈环

**Choice**: 审查 Agent 是 DAG 中的 review task，`dependsOn` 实现任务。审查 Agent 用 `report_task_result` 报告 `status: "failed"` + feedback。DAG 中 review task 标记为 failed。

Orchestrator 在 dispatch stage 结束后检查：如果 review task failed，且对应的 implementation task completed → 触发 replan。replan prompt 包含 review task 的 feedback。Orchestrator LLM 创建新 plan：implementation task 重试，continuation prompt 包含 review feedback。

**关键点**：不需要新机制——现有 replan 已经在 dispatch stage 中处理 failed task。变化是：
- 旧逻辑：task failed 因为 evidence gate 不通过 → harness loop 重试 → 用尽后 replan
- 新逻辑：implementation task passed（LLM 说了 pass），但 review task failed → Orchestrator 在 dispatch stage 结束时看到 review failed → 触发 replan

**实现改动**：在 `_run_dispatch_stage` 的 round 结束逻辑中，检查是否有 review task failed 且其依赖的 implementation task completed → 触发 replan（即使 implementation task 本身 passed）。

### Decision 4: 派发时附带约束——prompt 指导而非系统强制

**Choice**: 在 `orchestrator_prompts.py` 的 plan prompt 中增加指导文本，让 Orchestrator LLM 在 `plan_tasks` 的 task 描述中写清约束（引用资料、注释、格式）。不修改 `DispatchPlanItem` schema——约束写在 `task.task` 文本字段里。

**Alternative**: 在 `DispatchPlanItem` 新增 `constraints: list[str]` 字段。Rejected——增加 schema 复杂度，且约束是 task 描述的自然组成部分，不需要单独字段。

## Risks / Trade-offs

- **[Risk] LLM 校验增加延迟** → 每次 task 完成后多一次 LLM 调用。Mitigation: 用精简 prompt（无工具、单轮），选择轻量 model（如 gpt-4o-mini）；对于 advisory issues 为空的 case 跳过 LLM 调用直接 pass。

- **[Risk] LLM 过于宽松（什么都 pass）** → Mitigation: 保留 ②④ 两条硬规则（agent 自报告 status 和 acceptanceResults 不通过时直接 fail）。advisory issues 会提示 LLM 注意具体问题。

- **[Risk] LLM 过于严格（什么都 fail）** → Mitigation: evaluation prompt 明确指示"如果 task 基本完成，advisory issues 是次要问题，应该 pass 并在 feedback 中指出改进建议"。

- **[Risk] 审查 Agent replan 循环不终止** → Mitigation: 复用 `MAX_DISPATCH_ROUNDS=4` 上限。review task failed → replan → 如果 4 轮后仍 failed → aggregate stage 照常运行，Orchestrator 向用户报告"审查未通过但已达到最大重试次数"。

- **[Risk] BREAKING: `TaskResultReportEvaluation` → `TaskEvidenceSummary` 类型变更** → Mitigation: `evaluate_task_result_report` 的所有消费者在同一个变更中修改。外部调用者（如测试）需要更新。
