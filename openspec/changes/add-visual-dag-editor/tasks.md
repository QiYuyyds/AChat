## 1. 依赖安装与基础设施

- [x] 1.1 安装 `@xyflow/react` 和 `@dagrejs/dagre` 依赖（`pnpm add`）
- [x] 1.2 验证 `next/dynamic` 懒加载 `DispatchDAGGraph` 组件可行（避免首屏加载 React Flow）
- [x] 1.3 在 `src/lib/dag-validate.ts` 中实现 `validateDagFrontend(plan: DispatchPlanItem[]): string[]` 镜像函数（重复 id / 自依赖 / 缺失引用 / 环检测），注释标注镜像来源 `backend/app/services/dag_executor.py:validate_dag`

## 2. 后端改动（~25 行）

- [x] 2.1 修改 `backend/app/services/pending_dispatch_plans.py`：`PendingDispatchPlansStore.approve()` 新增可选参数 `modified_plan: list[DispatchPlanItem] | None = None`，存在时替换 `pending_plan.plan` 后再走 validator
- [x] 2.2 修改 `backend/app/api/pending.py`：`resolve_pending_dispatch_plan` 路由在 `action == "approve"` 时解析可选 `plan` 字段，用 `DispatchPlanItem.model_validate` 逐项解析，传入 `approve(modified_plan=...)`
- [x] 2.3 后端跑 `ruff check .` 确认无 lint 错误（仅预存 B008/F821，非本次引入）

## 3. 前端 DAG 图组件（只读模式 — Phase A）

- [x] 3.1 创建 `src/components/dispatch-dag-graph.tsx`：实现 `planToNodes()` 和 `planToEdges()` 转换函数，将 `DispatchState.plan` + `taskStatus` 映射为 React Flow 的 `Node[]` + `Edge[]`
- [x] 3.2 实现 `layoutWithDagre(nodes, edges)` 自动布局函数（`rankdir: 'TB'`, `nodesep: 60`, `ranksep: 80`，节点尺寸 260×120）
- [x] 3.3 实现 `TaskNode` Custom Node 组件：渲染 AgentAvatar + task ID + task description (line-clamp-2) + StatusIcon + WorktreeBadge + RetryBadge，根据 `DispatchTaskStatus` 应用不同边框/背景样式
- [x] 3.4 实现 `DispatchDAGGraph` 主组件：props 为 `dispatch`, `editable`, `onPlanChange?`, `agents`；只读模式下渲染 React Flow + Background + Controls，edges 在 `running` 时 `animated: true`
- [x] 3.5 实现小 DAG 降级逻辑：`plan.length <= 2` 且无 `dependsOn` 时渲染 `PlanTaskList`（复用现有组件），否则渲染 DAG 图
- [x] 3.6 用 `next/dynamic` 懒加载 `DispatchDAGGraph`，避免未使用 DAG 功能的页面加载 React Flow

## 4. 前端 DAG 编辑模式（Phase B）

- [x] 4.1 扩展 `DispatchDAGGraph`：`editable=true` 时启用 React Flow 交互模式（`nodesDraggable`, `edgesOnConnect` 等）
- [x] 4.2 实现添加节点：画布空白双击 → Popover 表单（Task ID, Agent dropdown 从 `agents` store 取, Task description textarea, Depends on checkboxes）→ 提交后 append 到 `editedPlan` + 调 `onPlanChange`
- [x] 4.3 实现删除节点：右键 ContextMenu → "删除" → 从 `editedPlan` 移除 + 清理其他节点 `dependsOn` 中的引用 + 调 `onPlanChange`
- [x] 4.4 实现创建依赖：从节点底部 handle 拖到另一节点顶部 handle → `onConnect` 回调将 `source` 添加到 target 的 `dependsOn` + 调 `onPlanChange`
- [x] 4.5 实现删除依赖：edge 上显示 ✕ 按钮 → 点击后从 target 的 `dependsOn` 移除 source + 调 `onPlanChange`
- [x] 4.6 实现编辑节点：双击节点 → Popover 编辑面板（预填值，可改 task description / agentId）→ 提交后更新 `editedPlan` + 调 `onPlanChange`
- [x] 4.7 集成 `validateDagFrontend`：每次 `editedPlan` 变化时调用，有错误时在 DAG 图下方显示错误提示

## 5. 前端 DispatchPlanCard 改造

- [x] 5.1 修改 `src/components/dispatch-plan-card.tsx`：`DispatchPlanReadOnlyCard` 中的 `PlanTaskList` 替换为 `DispatchDAGGraph`（`editable=false`）
- [x] 5.2 修改 `DispatchPlanReviewCard`：添加 `editedPlan` useState，将 `PlanTaskList` 替换为 `DispatchDAGGraph`（`editable=true`, `onPlanChange={setEditedPlan}`）
- [x] 5.3 `DispatchPlanReviewCard` 的 "执行计划" 按钮：`editedPlan` 与原 `plan` 有差异时调 `approvePendingDispatchPlanWithPlan`，无差异时调 `approvePendingDispatchPlan`；`validateDagFrontend` 有错误时禁用按钮
- [x] 5.4 懒加载 `DispatchDAGGraph`：使用 `next/dynamic` 避免 `DispatchPlanCard` 静态导入 React Flow

## 6. 前端 API 新增

- [x] 6.1 在 `src/lib/api.ts` 中新增 `approvePendingDispatchPlanWithPlan(conversationId, planId, plan)` 函数，POST 请求 body 为 `{ action: "approve", plan }`
- [x] 6.2 在 `src/shared/types.ts` 中确认 `DispatchPlanItem` 类型导出路径，确保 `dispatch-dag-graph.tsx` 可正确 import

## 7. 验证与测试

- [ ] 7.1 手动测试：创建一个 coordinated 模式群聊会话，开启 `plan_approval_enabled`，让 Orchestrator 生成 DAG，确认 DAG 图正确渲染
- [ ] 7.2 手动测试：在审批模式下编辑 DAG（增删节点/改依赖/换 Agent），确认前端实时校验生效，提交后后端执行编辑后的 DAG
- [ ] 7.3 手动测试：编辑 DAG 制造环形依赖，确认按钮禁用 + 错误提示，修复后确认恢复
- [ ] 7.4 手动测试：关闭 `plan_approval_enabled`，确认只读 DAG 图在 `dispatch.plan` 事件时正确渲染 + 实时状态更新
- [x] 7.5 跑 `pnpm typecheck` 和 `pnpm lint` 确认无错误（lint 0 错误；typecheck 仅 1 个预存错误 `api.ts:277 res.json<T>()` 非本次引入）
- [x] 7.6 跑 `ruff check .` 和 `pytest` 确认后端无回归（28 测试通过；2 个预存失败 `test_list_pending_writes_empty` + `test_handler_agent_not_in_conversation` 均非本次引入）
