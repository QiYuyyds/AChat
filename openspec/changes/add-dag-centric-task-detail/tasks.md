## 1. Store 层改动

- [x] 1.1 在 `AppState` 中新增 `selectedTaskId: string | null` 状态和 `setSelectedTaskId` action
- [x] 1.2 在 `message.start` SSE 事件处理中，判断 `event.runId` 是否属于某个 `DispatchState` 的 `childRunIds`（且 `reviewStatus === 'approved'`），是则标记 `hidden=true`
- [x] 1.3 在 `message.part` SSE 事件处理中，当 hidden 消息追加了 `tool_use` part 且工具名为 `ask_user` 时，将 `hidden` 翻转为 `false`
- [x] 1.4 新增 `useSelectedTaskDetail` selector hook：根据 `selectedTaskId` + 当前会话的 `dispatchesByRunId` 查出 `childRunId`、`messages`（该 childRun 的）、`turnMetrics`、`dispatch` 引用

## 2. DAG 图节点点击交互

- [x] 2.1 `DispatchDAGGraph` 新增 `onTaskSelect?: (taskId: string) => void` 和 `selectedTaskId?: string | null` props
- [x] 2.2 `TaskNode` 组件根据 `selectedTaskId` 匹配自身 `task.id`，匹配时添加 `ring-2 ring-primary` 高亮样式
- [x] 2.3 `DAGGraphInner` 在 `editable=false` 时注册 `onNodeClick` 回调，调用 `onTaskSelect?.(node.id)`
- [x] 2.4 `DispatchPlanReadOnlyCard` 传入 `selectedTaskId` 和 `setSelectedTaskId` 给 `DispatchDAGGraph`

## 3. TaskDetailPanel 组件

- [x] 3.1 创建 `src/components/task-detail-panel.tsx`，布局为右侧 `aside`（`w-96 shrink-0 border-l`），结构与 `FileExplorerPanel` 对齐
- [x] 3.2 Header 区域：Agent 头像 + 名称 + 任务 ID + StatusIcon + 任务描述（`task.task`）
- [x] 3.3 TurnTimeline 区域：复用 `TurnTimeline` 组件，传入 `turnMetrics`
- [x] 3.4 消息列表区域：`ScrollArea` 内渲染该 childRun 的所有消息的 `PartList`（复用现有 `PartList` 组件）
- [x] 3.5 关闭按钮：点击后调用 `setSelectedTaskId(null)`
- [x] 3.6 空状态：`selectedTaskId` 存在但找不到对应 childRun 时显示提示

## 4. 页面集成

- [x] 4.1 在 `page.tsx` 中条件渲染 `TaskDetailPanel`（与 `FileExplorerPanel` / `ArtifactPreviewPanel` 同级）
- [x] 4.2 `TaskDetailPanel` 与 `FileExplorerPanel` / `ArtifactPreviewPanel` 互斥：打开 TaskDetail 时关闭 FileExplorer / ArtifactPreview（或允许并排，由 `flex` 布局自然处理）
- [x] 4.3 `MessageList` 的 `buildSegments` 输入消息过滤掉 `hidden=true` 的消息（`messages.filter(m => !m.hidden)`）

## 5. DAG 图卡片高度优化

- [x] 5.1 `DispatchDAGGraph` 只读模式下容器高度自适应：`plan.length <= 3` 时 `h-[400px]`，否则 `h-[520px]`；编辑模式保持 `h-[480px]`

## 6. 验证与测试

- [ ] 6.1 手动测试：群聊 DAG 编排审批通过后，聊天流中不出现子任务消息，DAG 图实时更新节点状态
- [ ] 6.2 手动测试：点击 DAG 节点，右侧弹出详情面板，展示该任务的消息 parts 和 turn 指标
- [ ] 6.3 手动测试：子任务执行 `ask_user` 时，该消息在聊天流中变为可见，用户可正常交互
- [ ] 6.4 手动测试：切换不同节点，详情面板内容切换；关闭面板后 DAG 节点高亮消失
- [ ] 6.5 手动测试：solo 模式 / 普通群聊（无 DAG dispatch）消息流不受影响
- [x] 6.6 跑 `pnpm typecheck` 和 `pnpm lint` 确认无新增错误
