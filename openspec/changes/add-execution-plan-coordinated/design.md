# Design: add-execution-plan-coordinated

## Context

Phase 1 已在 solo 模式落地 `create_plan` / `plan_step` / `add_plan_steps` 三个工具。Coordinated 模式的协调者目前使用 `dispatch_plan`（声明式 DAG 调度）和 `task_dispatch`（即时单任务派发），但没有 `create_plan`——用户在群聊中看不到协调者的总体工作计划和进度。

两套 Plan 体系割裂：
- `execution_plan`：solo 模式，展示性，步骤粒度粗
- `dispatch_plan`：coordinated 模式，调度性，步骤粒度细（每个子任务一个 dispatch item）

## Goals / Non-Goals

**Goals:**

- Coordinated 模式下协调者可使用 `create_plan` 展示总体工作计划
- `dispatch_plan` 执行子任务时，对应的 plan step 自动更新状态
- 协调者也可以自己执行部分 plan step（不派发，直接用标准工具）
- 两套 Plan 共存互补：`create_plan` 做进度展示，`dispatch_plan` 做子 Agent 调度

**Non-Goals:**

- 不做 `create_plan` 步骤与 `dispatch_plan` 任务的强制 1:1 映射（一个 plan step 可能对应多个 dispatch task，也可能不对应任何 dispatch task）
- 不做 subagent 模式的 plan 工具注入（subagent 由协调者或 solo Agent 派发，自己不需要展示计划给用户——消息是 hidden 的）
- 不改变 `dispatch_plan` 本身的执行逻辑

**Future Work:**

- **Plan 可编辑**：允许协调者在 dispatch 执行中修改 plan step（增删改），目前仅支持 `add_plan_steps` 追加
- **Subagent 可见 plan**：让子 Agent 知道自己在整体计划中的位置，需要在 `spawn_subagent_loop` 的 override_prompt 里注入 plan 上下文

## Decisions

### D1: Plan step 与 dispatch task 的映射方式——显式注册

**选择**：显式注册。协调者在 `dispatch_plan` 调用中可选地指定 `planStepId` 字段，将 dispatch task 与某个 plan step 关联。

**备选**：
- A) 自动推断——根据 task 描述与 plan step 标题的语义相似度匹配 → 不可靠，语义鸿沟太大
- B) 顺序映射——第 N 个 dispatch task 对应第 N 个 plan step → 不成立，两者粒度和顺序都不一致

**理由**：显式注册最可靠。LLM 在生成 dispatch_plan 时已经知道每个 task 的目的，让它顺手指定 planStepId 是零负担的。不指定时无映射，plan step 不受 dispatch 事件影响（协调者自己执行）。

### D2: 映射存储——plan_dispatch_mapping 注册表

**选择**：新增 in-memory `plan_dispatch_mapping` 注册表，结构：

```python
# key: (plan_id, step_id) -> value: list of dispatch task IDs
mapping: dict[tuple[str, str], list[str]]

# 反向索引：dispatch task ID -> (plan_id, step_id)
reverse: dict[str, tuple[str, str]]
```

在 `dispatch_plan` handler 执行时写入映射，在 `consume_stream` 处理 `dispatch.end` 时查找并更新 plan step 状态。

**理由**：和 `plan_registry` 一样的轻量模式，run 结束时随 `plan_registry` 一起清理。

### D3: dispatch.end 联动 plan step 的触发位置

**选择**：在 `consume_stream` 中处理 `dispatch.end` 事件时，查找 `plan_dispatch_mapping`，如果该 dispatch task 关联了某个 plan step，则更新 plan step 状态并发射 `plan.step_update` 事件。

**流程**：
```
dispatch.end(taskId='t1', status='complete')
  -> 查 plan_dispatch_mapping: taskId='t1' -> (planId='p1', stepId='s2')
  -> 检查 planId='p1' 的 stepId='s2' 关联的所有 dispatch tasks
  -> 如果所有关联 tasks 都 complete -> 标记 stepId='s2' 为 done
  -> 如果任一 failed -> 标记 stepId='s2' 为 failed
  -> 发射 plan.step_update 事件
```

**理由**：`consume_stream` 已经是事件路由的核心位置，dispatch.end 在这里已有处理逻辑，新增 plan step 联动是自然扩展。

### D4: 协调者 prompt 指导

**选择**：在 `_COORDINATED_PROMPT_SUFFIX` 中新增执行计划段落，明确指导：
1. 先调 `create_plan` 展示总体计划
2. 在 `dispatch_plan` 中为每个 task 指定 `planStepId` 关联到对应的 plan step
3. 自己执行的步骤用 `plan_step` 手动标记
4. 不需要为每个 dispatch task 都创建 plan step——plan step 是粗粒度的进度展示，dispatch task 是细粒度的调度

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| 协调者忘记在 dispatch_plan 中指定 planStepId | 不指定时无联动，plan step 不会被自动更新——协调者仍可用 plan_step 手动更新；prompt 指导明确说明 |
| 一个 plan step 对应多个 dispatch tasks，部分失败 | 所有关联 tasks 完成 + 无 failed -> done；任一 failed -> failed；有 skipped -> skipped |
| 映射注册表与 plan_registry 生命周期管理 | 两者在同一 run 结束时一起清理 |
| 协调者同时有 create_plan 和 dispatch_plan，概念混淆 | prompt 明确区分：create_plan 展示进度，dispatch_plan 调度执行 |

## Open Questions

- `planStepId` 字段是否应该加到 `task_dispatch` 的参数中？（当前设计只加到 `dispatch_plan`，因为 `task_dispatch` 是即时单任务，协调者可以在调 `task_dispatch` 前后手动调 `plan_step`）
