# Design: DAG-Centric Task Detail

## Context

当前群聊 DAG 编排执行后，子任务消息通过 `buildSegments` 按 wave 分列涌入聊天流。用户在聊天流里同时看到 Orchestrator 消息、DAG 卡片、以及每个子任务的完整消息流（thinking / tool_use / text），信息过载。

现有架构已有以下基础：
- `message.hidden` 字位（clone-subagent 机制使用过），MessageList 渲染时跳过 hidden 消息
- `DispatchState.childRunIds[taskId] → childRunId` 映射，可定位子任务消息
- `runsByConv[convId][childRunId].turnMetrics` 提供子任务 turn 级指标
- 右侧面板槽位模式（`FileExplorerPanel` / `ArtifactPreviewPanel` 条件渲染）
- `PartList` / `TurnTimeline` 组件可复用

## Goals / Non-Goals

**Goals:**
- 子任务执行消息从聊天流隐藏，DAG 图成为唯一的执行进度视图
- 点击 DAG 节点 → 右侧面板展示该任务的完整执行细节（消息 parts + turn 指标）
- 保留含用户交互的消息（`ask_user` / pending 审批类）在聊天流中可见
- 非 DAG 模式（solo / 普通群聊无 dispatch）不受影响

**Non-Goals:**
- 多任务详情同时展示（多 tab）——先做单选切换
- 详情面板内的产物预览内联——复用现有 ArtifactPreviewPanel 即可
- 编辑模式下隐藏子任务消息——编辑模式本就没有子任务在执行

## Decisions

### D1: 消息隐藏策略 — Store 层标记 `hidden=true`

**选择**: 在 `message.start` SSE 事件处理时，判断 `event.runId` 是否属于某个 `DispatchState` 的 `childRunIds`，是则标记 `hidden=true`。

**替代方案**: 渲染时过滤（MessageList 中跳过 childRun 消息）。

**理由**: Store 层标记与 clone-subagent 的 `hidden` 机制一致；渲染层不需要额外判断；详情面板仍可通过 `Object.values(s.messages)` 取到 hidden 消息来展示。

### D2: 用户交互消息保留可见

**选择**: 不在 `message.start` 时判断消息内容（此时消息 parts 为空，无法预知是否含 `ask_user`）。而是在 `PartList` 渲染时，如果一条 hidden 消息后续追加了 `tool_use` part 且工具名为 `ask_user`，则将 `hidden` 回设为 `false`。

**理由**: `message.start` 时 parts 尚未流入，无法判断。`ask_user` 工具调用会在 `tool_use` part 追加时通过 SSE `message.part` 事件到达，此时可翻转为可见。其他 pending 审批类（bash / write / mcp）同理——这些工具调用会触发对应的 `*.pending` SSE 事件，可在此时翻转。

**简化**: 首期仅处理 `ask_user`（最常见），其他 pending 类工具消息可通过用户点击 DAG 节点在详情面板中查看。后续按需扩展。

### D3: 右侧详情面板 — 新组件 `TaskDetailPanel`

**选择**: 新建 `TaskDetailPanel` 组件，挂载在 `page.tsx` 的 `FileExplorerPanel` / `ArtifactPreviewPanel` 同级，通过 `selectedTaskId` 状态控制开合。

**布局**:
```
┌─────────┬────────────────────┬──────────────┐
│ Sidebar │   ChatPanel        │ TaskDetail   │
│         │   (DAG 卡片在内)    │ Panel        │
│         │                    │ (w-96)       │
│         │                    │              │
│         │   点击节点 →        │ Header:      │
│         │   setSelectedTaskId │  agent+status│
│         │                    │ TurnTimeline │
│         │                    │ PartList     │
│         │                    │   (scroll)   │
└─────────┴────────────────────┴──────────────┘
```

**数据获取**: `selectedTaskId` → `dispatch.childRunIds[taskId]` → `childRunId` → `messages.filter(m => m.runId === childRunId)` + `runsByConv[convId][childRunId].turnMetrics`。

### D4: DAG 节点点击交互

**选择**: `DispatchDAGGraph` 在 `editable=false` 时，节点点击通过 `onNodeClick` React Flow 回调触发 `onTaskSelect?: (taskId: string) => void` 新 props。`DispatchPlanReadOnlyCard` 传入 `onTaskSelect={setSelectedTaskId}`。

选中节点视觉反馈：边框高亮（`ring-2 ring-primary`）。

### D5: DAG 图卡片高度自适应

**选择**: 只读模式下 `DispatchDAGGraph` 的容器高度从固定 `h-[480px]` 改为 `min-h-[400px]`，子任务消息隐藏后卡片有更多空间。当 `plan.length <= 3` 时 `h-[400px]`，否则 `h-[520px]`。

## Risks / Trade-offs

- **[消息 hidden 后无法在聊天流搜索到]** → 详情面板内的消息支持搜索（后续迭代）；当前 `GlobalSearch` 搜索的是 message content，hidden 消息仍可通过 store 访问。
- **[ask_user 消息翻转 visible 时机依赖 part 流入]** → `ask_user` 工具调用一定会产生 `tool_use` part，且 `message.part` 事件可靠到达。如果 part 事件丢失（网络问题），消息保持 hidden，但用户可通过点击 DAG 节点在详情面板中看到。
- **[DAG 图隐藏子任务消息后，Orchestrator 总结消息与 DAG 卡片之间可能有大段空白]** → 这是预期行为：空白代表"执行过程在图中"。DAG 图的实时状态更新填补了这段信息空白。
