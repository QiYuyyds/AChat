# Add DAG-Centric Task Detail

## Why

群聊 DAG 编排确定执行后，子任务的执行过程（thinking、tool_use、text 等消息）大量涌入聊天流，既打断了主对话的连贯性，也让 DAG 图沦为静态装饰。用户需要的是「DAG 图即执行视图」——图上看进度，点节点看细节，聊天流保持干净。

## What Changes

- DAG 图节点在只读模式下可点击，点击后右侧弹出该任务的详情面板（TaskDetailPanel）
- 详情面板展示该子任务 child run 的消息 parts 列表（thinking / tool_use / text）+ TurnTimeline 指标
- 执行阶段的子任务消息从聊天流中隐藏（`hidden=true`），保留 Orchestrator 自身消息和最终聚合消息
- 保留含用户交互（`ask_user` / `pending_questions` / `pending_bash_commands` 等）的子任务消息不隐藏，确保用户可正常审批
- DAG 图卡片在只读模式下获得更大空间（因为子任务消息不再占聊天流空间）
- Store 新增 `selectedTaskId` 状态，控制右侧详情面板的开合

## Capabilities

### New Capabilities
- `dag-task-detail`: DAG 图节点点击 → 右侧任务详情面板，展示子任务执行过程的完整消息流与 turn 指标

### Modified Capabilities
- `frontend`: 子任务消息在 DAG 执行阶段从聊天流隐藏（`hidden=true`），MessageList 渲染时跳过；DAG 图只读模式支持节点点击交互

## Impact

- **Store** (`src/stores/app-store.ts`): 新增 `selectedTaskId` 状态 + `setSelectedTaskId` action；`message.start` 处理时对 dispatch child run 的消息标记 `hidden=true`
- **MessageList** (`src/components/message-list.tsx`): 渲染时跳过 `hidden=true` 的消息，wave segment 不再产生
- **DispatchDAGGraph** (`src/components/dispatch-dag-graph.tsx`): 只读模式下节点可点击，点击触发 `onNodeClick` 回调
- **新组件** `TaskDetailPanel`: 右侧面板，复用 `PartList` / `TurnTimeline` / `AgentAvatar`
- **page.tsx**: 条件渲染 `TaskDetailPanel`
- **DispatchPlanCard**: 只读卡 DAG 区域高度自适应（子任务消息隐藏后卡片有更多空间）
