# Proposal: Parallel Wave Columns

## Why

Orchestrator 同波次（wave）的并行子任务目前按消息 `createdAt` 时间序交错出现在同一条聊天时间线里，用户看到的是多个 agent 的消息来回穿插，难以跟踪每个 agent 各自的执行进度。引入 worktree 隔离后，并行任务物理隔离已就绪，前端也应提供对应的视觉隔离——同一 wave 的并行 agent 各占一列并排展示，让用户一目了然地看到每个 agent 的独立工作流。

## What Changes

- `MessageList` 新增 wave 分组逻辑：通过 `DispatchState.plan` 的 `dependsOn` 关系做拓扑分层，计算每个 task 的 wave 层级
- 同一 wave 内的并行子任务消息（通过 `childRunIds[taskId]` → `runId` 关联到消息）用 flex 横向并排渲染，每个 agent 一列
- 不同 wave 之间纵向排列，保持时间顺序
- 非 Orchestrator 的普通消息（user 消息、单聊 agent 消息、Orchestrator 自身消息）保持原有纵向单列布局不变
- 列内消息沿用 `group-consecutive-agent-messages` 的分组规则（同 run 同 agent 连续消息合并头像）
- 列宽自适应，2 列时各 50%，3+ 列时等分；窄屏降级为单列（纵向堆叠）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: `MessageList` 新增并行 wave 多列布局——同一 wave 的子 agent 消息并排显示，不同 wave 纵向排列；窄屏降级为单列

## Impact

- **前端代码**：`src/components/message-list.tsx`（wave 分组 + 多列布局）、可能新增 `src/components/wave-columns.tsx` 组件
- **后端**：无改动
- **Event 协议**：无改动（`DispatchState` 已有 `plan` + `childRunIds` + `dependsOn` 足以计算 wave）
- **DB schema**：无改动
- **依赖**：`group-consecutive-agent-messages` change（列内分组复用其 `grouped` prop 逻辑）
