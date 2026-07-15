# Design: add-execution-plan

## Context

AChat 的 Agent 编排采用 ReAct 循环（`_run_react_loop`）：每轮 `call_once` → 模型输出文本或 tool_calls → 执行工具 → 下一轮。模型隐式规划，用户无法预知 Agent 的工作安排，也无法感知当前进展。

现有的 `dispatch_plan` 工具是 coordinated 模式的 DAG 调度机制，面向子 Agent 派发，不适合 solo 模式的个人工作计划展示。

本设计在现有 ReAct 循环上叠加一层轻量的"执行计划"能力：模型主动创建结构化步骤列表，系统追踪进度，前端渲染为 checklist 卡片。不改变循环本身，不引入审批或验证 gate。

## Goals / Non-Goals

**Goals:**

- 模型通过工具主动生成结构化执行计划，用户可实时看到进度
- 简单任务（1-2步）不生成计划，由模型自主判断
- 计划步骤支持状态实时更新（pending → in_progress → done/failed）
- 允许模型在执行过程中动态追加步骤
- `complexity` 参数用于后续统计，不影响执行逻辑
- 和现有 `artifact_ref` 注入路径对称——工具产出事件 → consume_stream 注入 part

**Non-Goals:**

- 不做用户审批（Phase 1 无审批，不同于 `dispatch_plan` 的 `plan_approval_enabled`）
- 不做 Plan 可编辑（用户不能增删改步骤）
- 不做 coordinated 模式下 Plan 与 `dispatch_plan` 的联动（Phase 2，见下方 Future Work）
- 不改变 ReAct 循环核心逻辑
- 不做 Plan 模板或自动提取（从 thinking 里提取 plan）
- 不做 Prompt 深度调优（仅提供初始指导词，持续优化留后续迭代）

**Future Work:**

- **Phase 2 — Coordinated 联动**：coordinated 模式下 `create_plan` 可用，协调者先展示总体计划再 `dispatch_plan` 分发子任务。需要设计 plan step 与 dispatch event 的映射（一个 plan step 可能对应一个 dispatch task，也可能多个 step 对应一个 task），以及 `dispatch_plan` 工具的联动改动。这是独立 change，涉及 `dispatch_plan` 的 spec 变更。
- **Prompt 优化**：持续优化模型对"简单 vs 复杂"的判断准确率。可能方向：(1) Plan 模板——常见任务类型的预定义步骤，减少模型规划负担；(2) 基于 complexity 统计的 prompt 迭代——收集模型自评的 complexity 数据，分析误判模式，针对性优化指导词；(3) 少量 few-shot 示例嵌入 prompt。

## Decisions

### D1: Plan 状态存储——内存注册表 + parts 同步

**选择**：轻量 in-memory `plan_registry`（`dict[str, PlanState]`），run 结束即清除。

**备选**：
- A) 只存 parts 里 → tool handler 拿不到 parts_buffer，无法修改
- B) 持久化到 DB → 过重，parts 里已有完整状态，无需额外表

**理由**：tool handler 需要读取当前 steps 才能修改状态，但 handler 拿不到 parts_buffer。内存注册表最轻量，run 结束后 parts 里保留最终状态，注册表清理无副作用。

### D2: Plan 变更通过事件驱动——对称于 artifact 路径

**选择**：tool handler 返回结果 → `_execute_tool_call_to_result` 检测特定工具名 → 生成对应事件 → consume_stream 处理事件更新 parts_buffer 和 SSE。

**流程**：
```
create_plan:
  handler → ok({ planId, steps, complexity })
  _execute_tool_call_to_result → 检测 tc.name == "create_plan"
    → 追加 PlanCreatedEvent
  consume_stream:
    plan.created → push execution_plan part + part.start 事件

plan_step:
  handler → 从 plan_registry 读取/更新 → ok({ planId, updatedSteps })
  _execute_tool_call_to_result → 检测 tc.name == "plan_step"
    → 追加 PlanStepUpdateEvent
  consume_stream:
    plan.step_update → 更新 parts_buffer 里的 execution_plan steps
    → SSE publish PlanStepUpdateEvent

add_plan_steps:
  handler → 从 plan_registry 读取/更新 → ok({ planId, addedCount })
  _execute_tool_call_to_result → 检测 tc.name == "add_plan_steps"
    → 追加 PlanStepUpdateEvent
  consume_stream: 同 plan_step
```

**理由**：和 `write_artifact → artifact.create → artifact_ref part` 完全对称，符合现有架构模式。

### D3: plan_step 自动推进前一步

**选择**：调用 `plan_step(planId, stepId)` 时，系统自动把同一 plan 里当前 `in_progress` 的步骤标为 `done`，然后标记 `stepId` 为 `in_progress`。

**备选**：
- A) 模型手动标每一步 done → 浪费 tool call，模型容易忘记
- B) 系统根据 tool call 语义自动匹配 → 语义鸿沟太大，不可靠

**理由**：顺序执行是绝大多数场景，自动推进简单有效。如果模型跳步（从 s1 直接到 s3），s1 也会被标 done——这是合理的，因为模型明确说了"我开始做 s3 了"。

### D4: Run 结束时 Plan 终态清理

**选择**：`consume_stream` 收到 `run.end` 时，遍历当前 message 的 `execution_plan` parts，根据 run status 把未完成步骤标为 done/failed/skipped，发布最终 `PlanStepUpdateEvent`。

**理由**：防止用户看到"卡在 in_progress"的 Plan。Run 完成时所有未完成的 in_progress → done（如果 run complete）或 failed（如果 run failed/aborted），pending → skipped。

### D5: 步骤 ID 由模型生成

**选择**：步骤 ID（如 `s1`, `s2`）由模型在 `create_plan` 参数中指定，不由系统生成。

**理由**：模型需要在后续 `plan_step` 调用中引用步骤 ID，如果由系统生成则模型需要从 tool result 中记住，增加出错概率。模型自己命名 ID（如 `s1`, `s2`）更直观。

### D6: Plan 工具仅在 solo 模式注入（Phase 1）

**选择**：Phase 1 只在 solo 模式注入 `create_plan`、`plan_step`、`add_plan_steps` 三个工具。coordinated 模式和 subagent 模式暂不注入。

**理由**：coordinated 模式已有 `dispatch_plan`，两者如何联动需要更多设计。solo 模式是最高频的使用场景，先落地最核心的价值。

### D7: execution_plan part 不增量

**选择**：`execution_plan` part 的变更走 `plan.step_update` 事件全量替换 steps 数组，不走 PartDelta 增量协议。

**理由**：步骤数量少（< 10），全量替换简单可靠。增量协议需要定义"新增步骤"和"改状态"两种 delta，增加复杂度但收益有限。

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| 模型在简单场景也调 `create_plan`，浪费 tool call | Prompt 指导明确"1-2步不需要 create_plan"；`minSteps: 2` 参数约束 |
| 模型忘记调 `plan_step` 更新进度 | Run 结束时终态清理兜底；Prompt 指导"开始步骤前调 plan_step" |
| 模型不按 Plan 顺序执行 | `plan_step` 不限制 stepId 顺序，允许跳步；自动推进前一步 |
| plan_registry 内存泄漏 | Run 结束时清理；崩溃重启时无残留（parts 里有完整状态） |
| `plan_step` 占用 parallel tool call 位置 | `plan_step` 参数极简（2 个短字符串），token 开销 < 50；可和实际工具并行调用 |
| Plan 和 dispatch_plan 概念混淆 | 命名明确区分（execution_plan vs dispatch_plan）；Prompt 分别指导适用场景 |

## Migration Plan

无需迁移。新 MessagePart 类型和事件是增量添加，旧消息不受影响。前端 reducer 对未知 part type 走 `default: return null`，不会报错。

## Open Questions

- coordinated 模式下 Plan 与 dispatch_plan 的联动策略（Phase 2 再定）
- 是否需要限制每条消息最多 1 个 execution_plan part（当前允许多个，但实际场景中不太需要多个 Plan）
