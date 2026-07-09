## 1. Wave 计算与数据层

- [x] 1.1 在 `message-list.tsx`（或新文件 `wave-utils.ts`）中实现 `computeWaves(plan: DispatchPlanItem[]): Record<taskId, number>`，按 `dependsOn` 拓扑分层
- [x] 1.2 实现 `useChildRunWaveMap(conversationId)` hook：遍历 `dispatchesByRunId`，对每个有 `plan` 的 dispatch 调 `computeWaves`，通过 `childRunIds` 反映射，返回 `Record<childRunId, { wave, taskId, orchestratorRunId, agentId }>`
- [x] 1.3 实现 `buildSegments(messages, childRunWaveMap)`：将 `MessageRow[]` 分段为 `Segment[]`（`single` 或 `wave`），wave segment 内按 taskId 分列

## 2. 列头组件

- [x] 2.1 新增 `WaveColumnHeader` 组件：显示 agent 头像 + 名字 + task id，紧凑样式
- [x] 2.2 在 wave segment 的每列顶部渲染 `WaveColumnHeader`

## 3. MessageList 多列布局

- [x] 3.1 在 `MessageList` 中调用 `useChildRunWaveMap` 和 `buildSegments`，用 segments 替代直接 `messages.map`
- [x] 3.2 `single` segment：保持现有单列渲染（含 `group-consecutive-agent-messages` 分组逻辑）
- [x] 3.3 `wave` segment：用 `flex gap-3 max-md:flex-col` 容器，每列 `flex-1 min-w-0`
- [x] 3.4 列内消息：全部以 `grouped={true}` 渲染（头像已由列头显示），间距用 `mt-0.5`
- [x] 3.5 segment 之间用 `mt-4` 间距分隔

## 4. 适配与边界处理

- [x] 4.1 窄屏降级：`max-md:flex-col` 让多列在 < 768px 时纵向堆叠，每列仍显示列头
- [x] 4.2 流式新消息：新 `message.start` 的消息按 `runId` 自动归入正确的列或 single segment
- [x] 4.3 非 dispatch 消息（user / 单聊 agent / Orchestrator 自身）正确归入 single segment
- [x] 4.4 撤回/编辑后消息列表重排，segments 自动正确重算
- [x] 4.5 空 wave 列（task 已开始但还没产出消息）渲染列头 + 空状态占位

## 5. 验证

- [ ] 5.1 两 task 并行：确认两列并排，各列有列头，列内消息紧凑
- [ ] 5.2 三 task 并行：确认三列等宽并排
- [ ] 5.3 串行 wave：确认 wave 0 在上、wave 1 在下，纵向排列
- [ ] 5.4 user 消息 + dispatch 消息 + child 消息混合：确认分段正确
- [ ] 5.5 窄屏：确认多列降级为纵向堆叠
- [ ] 5.6 token badge 在列内每条消息上正常显示
- [ ] 5.7 TurnTimeline 在列内最后一条消息上正常渲染
- [x] 5.8 `pnpm typecheck` 通过
- [x] 5.9 `pnpm lint` 通过
