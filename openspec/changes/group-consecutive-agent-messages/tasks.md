## 1. 分组逻辑

- [x] 1.1 在 `message-list.tsx` 中添加 `isGroupedWithPrev(prev, curr)` 纯函数，判断连续两条消息是否属于同一组（同 `role === 'agent'` + 同 `agentId` + 同 `runId` + `runId !== null`）
- [x] 1.2 在 `messages.map` 遍历中，对每条消息计算 `grouped` 布尔值，传给 `MessageItem` 作为新 prop

## 2. MessageItem 条件渲染

- [x] 2.1 在 `message-item.tsx` 的 `MessageItemImpl` 中接收 `grouped: boolean` prop
- [x] 2.2 当 `grouped` 为 true 时，隐藏头像区块（不渲染 `AgentInfoPopover` / `Avatar`）
- [x] 2.3 当 `grouped` 为 true 时，隐藏名称、时间戳、streaming spinner、pin/bookmark 图标行
- [x] 2.4 当 `grouped` 为 true 时，保留 token badge 的渲染逻辑不变
- [x] 2.5 当 `grouped` 为 true 时，保留操作工具栏（hover 可见）和 PartList / DispatchPlanCard / TurnTimeline 的渲染不变

## 3. 间距调整

- [x] 3.1 移除 `message-list.tsx` 中外层容器的 `space-y-4` 类
- [x] 3.2 为每条消息的外层容器动态设置 margin：首条 `mt-0`，grouped 消息 `mt-1`（4px），非 grouped 消息 `mt-4`（16px）
- [x] 3.3 验证 `message-item.tsx` 中 `grouped` 消息的气泡左对齐：用与头像等宽的左 padding/margin 占位（44px = 32px avatar + 12px gap），确保气泡不左移错位

## 4. 验证

- [ ] 4.1 单聊场景：agent ReAct 多轮回复，确认首条有头像/名称，后续隐藏，间距收紧
- [ ] 4.2 群聊场景：两个 agent 交替发言，确认换 agent 时重新显示头像/名称
- [ ] 4.3 user → agent → user → agent 交替，确认每段 agent 回复首条有头像
- [ ] 4.4 流式过程：新 `message.start` 触发的新消息正确分组，首条 spinner 正常显示
- [ ] 4.5 per-message token badge 在 grouped 消息上正常显示和 hover
- [ ] 4.6 TurnTimeline 仍在 run 最后一条消息上正常渲染
- [ ] 4.7 撤回/编辑后消息列表重排，分组自动正确重算
- [x] 4.8 `pnpm typecheck` 通过
- [x] 4.9 `pnpm lint` 通过
