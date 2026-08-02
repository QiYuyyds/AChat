## Why

当前 `dispatch_plan` 生成的 DAG 在前端以 flat 列表渲染（`PlanTaskList`），依赖关系仅以文字 "→ t1, t2" 展示。当 DAG 有 3+ 节点和多层依赖时，用户难以理解任务结构。同时，用户在审批阶段只能 approve/reject 二选一，或用自然语言 revise 让 LLM 重新规划——无法直接编辑 DAG 结构。需要一个可视化 DAG 图来**展示**任务结构和实时状态，并在审批阶段允许用户**编辑** DAG 后执行。

## What Changes

- 新增 `DispatchDAGGraph` 前端组件，用 React Flow + dagre 将 `DispatchState.plan` 渲染为可视化 DAG 图（节点 = task，边 = dependsOn），替代现有 `PlanTaskList` flat 列表
- DAG 图在只读模式下实时反映节点状态（pending/running/complete/failed/skipped/merge_conflict）和 worktree/retry badge
- DAG 图在审批模式（`reviewStatus='pending'`）下支持编辑：添加/删除节点、拖拽连线创建依赖、删除边、编辑任务描述/换 Agent
- 后端 `PendingDispatchPlansStore.approve()` 新增可选 `modified_plan` 参数，支持用户编辑后的 DAG 经 `validate_dag` 重新校验后执行
- 后端 API `POST /pending-dispatch-plans/{plan_id}` 的 `action: "approve"` 新增可选 `plan` 字段
- 前端新增 `approvePendingDispatchPlanWithPlan()` API 函数
- 前端新增 `validateDagFrontend()` 镜像函数，编辑时实时校验 DAG 合法性（重复 id / 自依赖 / 缺失引用 / 环检测）

## Capabilities

### New Capabilities

无。本变更不引入新的 capability spec，所有行为变更落在现有 `orchestrator` 和 `frontend` capability 上。

### Modified Capabilities

- `orchestrator`: `PendingDispatchPlansStore.approve()` 支持接收用户编辑后的 modified plan，经 `validate_dag` 重新校验后交给 `execute_dag` 执行
- `frontend`: `DispatchPlanCard` 用可视化 DAG 图替代 flat 列表展示；审批模式下 DAG 图可编辑，approve 时带上编辑后的 plan

## Impact

- **新增依赖**：`@xyflow/react`（React Flow 核心 DAG 图渲染+交互）、`@dagrejs/dagre`（自动拓扑布局）
- **后端改动**：`backend/app/services/pending_dispatch_plans.py`（`approve()` 加参数）、`backend/app/api/pending.py`（解析 `plan` 字段）—— 约 25 行
- **前端改动**：新建 `src/components/dispatch-dag-graph.tsx`（~200 行）、新建 `src/lib/dag-validate.ts`（~40 行）、改造 `src/components/dispatch-plan-card.tsx`（~30 行改动）、`src/lib/api.ts` 新增函数（~15 行）—— 约 285 行
- **不改**：`DispatchPlanItem` schema、`StreamEvent` 协议、`validate_dag`/`topological_waves`/`execute_dag` 逻辑、DB schema、事件类型
