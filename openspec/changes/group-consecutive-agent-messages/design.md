## Context

ReAct 循环中，agent 每轮 LLM 调用由 adapter emit 一个 `message.start` → `message.end`，后端据此在 DB 中新建一行 Message。前端 `MessageList` 对每条 Message 渲染独立的 `MessageItem`，包含完整头像 + 名称 + 时间 + token badge。

单次 agent 回复通常有 2-5 轮 ReAct（thinking → tool → text），每轮一条 Message。当前 UI 对每条都显示头像和名称，视觉上像多人在刷屏。

现有数据模型已具备分组所需的全部字段：`MessageRow.runId`（标记同一 run）、`MessageRow.agentId`（标记同一 agent）、`MessageRow.role`（区分 user/agent）。

## Goals / Non-Goals

**Goals:**
- 同一 run + 同一 agent 的连续 agent 消息在视觉上合并：后续消息隐藏头像和名称
- 同组消息间距收紧（4px），组间保持原间距（16px）
- 保留每条消息的 per-message token badge
- 保留 TurnTimeline（run 最后一条消息上）
- 群聊场景正确处理：换 agent 或换 run 时重新显示头像和名称

**Non-Goals:**
- 不改后端 event 协议（`message.start` / `message.end` 契约不变）
- 不改 DB schema（`runId` / `agentId` 已存在）
- 不合并 Message 行（每轮仍是独立 Message，只是前端视觉合并）
- 不改 per-message token 统计逻辑

## Decisions

### D1: 分组逻辑放在 MessageList 层，不在 MessageItem 层

`MessageList` 遍历消息列表时判断每条消息是否「可分组到前一条」，将 `grouped: boolean` 传给 `MessageItem`。

**理由**：分组需要访问前一条消息的 `runId` / `agentId` / `role`，`MessageItem` 是 `memo` 组件、只接收单条 `message` prop，不适合在这里做前后对比。`MessageList` 已持有完整列表，是自然的分组位置。

**替代方案**：在 store 的 `useMessagesForConversation` selector 里预计算分组标记——但这会让 selector 输出不再是纯 `MessageRow[]`，破坏现有 `setMessagesForConversation` 等消费方。不划算。

### D2: 分组条件

```typescript
function isGroupedWithPrev(prev: MessageRow, curr: MessageRow): boolean {
  return (
    prev.role === 'agent' &&
    curr.role === 'agent' &&
    prev.agentId === curr.agentId &&
    prev.runId === curr.runId &&
    prev.runId !== null
  )
}
```

- `role === 'agent'`：user 消息和 system 消息永远不分组（user 消息始终独立显示头像「我」）
- `agentId` 相同：群聊里不同 agent 的消息自然断开
- `runId` 相同：同一 run 的多轮 ReAct 消息才合并；不同 run（如 Orchestrator 重新分派）断开
- `runId !== null`：防御旧数据 / 异常情况

### D3: grouped 时隐藏什么、保留什么

| 元素 | grouped 时 |
|---|---|
| 头像 | 隐藏（不渲染，不占位） |
| 名称 | 隐藏 |
| 时间戳 | 隐藏（同组时间接近，首条已有） |
| streaming spinner | 隐藏（首条的 spinner 足够） |
| token badge | **保留** |
| pin/bookmark 图标 | 隐藏（同组内不需要重复指示） |
| 气泡内容（PartList） | 保留 |
| DispatchPlanCard | 保留（如果有） |
| TurnTimeline | 保留（只在 run 最后一条上，与分组无关） |
| 操作工具栏 | 保留（hover 时可见） |

### D4: 间距实现

当前 `MessageList` 用 `space-y-4`（16px）统一间距。改为：

- 在 `MessageList` 的 `messages.map` 中，对每条消息根据 `grouped` 状态动态设置外层容器的 `marginTop`
- grouped 消息：`mt-1`（4px）
- 非 grouped 消息：`mt-4`（16px），首条消息除外（`mt-0`）
- 移除 `space-y-4`，改为 per-item margin

### D5: grouped 消息的气泡宽度对齐

当前 `MessageItem` 的布局是 `flex gap-3`（头像 + 内容区）。grouped 时头像消失，内容区会左移。为保持气泡左对齐不错位，grouped 时用与头像等宽的 `paddingLeft` 或 `margin-left` 占位（`size-8` + `gap-3` = 32px + 12px = 44px）。

## Risks / Trade-offs

- **[风险] grouped 消息 hover 工具栏的引用/pin 按钮位置变化** → 工具栏仍在气泡下方，hover 可见。位置左移不影响功能，只是视觉偏左。可接受。
- **[风险] 流式过程中新消息插入，分组状态实时变化** → `MessageList` 每次 render 重新计算分组，流式时新 `message.start` 触发 store 更新 → re-render → 自动正确分组。无额外处理。
- **[风险] 撤回/编辑后消息列表变化** → 撤回删除消息后列表重排，分组自动重算。无边界问题。
- **[取舍] 完全隐藏头像 vs 竖线占位** → 用户选择完全隐藏，更简洁。代价是视觉上「连续性」感知稍弱，但 token badge 的存在足以暗示同一 agent 的连续操作。
