# Proposal: Group Consecutive Agent Messages

## Why

ReAct 循环中 agent 每轮 LLM 调用都会产生一条独立的 Message（各有 `message.start` / `message.end`）。前端渲染时每条 Message 都重复显示头像和名称，导致一次 agent 回复看起来像多人在刷屏，视觉噪音大。用户希望同一 run 内同一 agent 的连续消息在视觉上「合并」，但仍保留每条消息的 token 用量展示。

## What Changes

- 前端 `MessageList` 对消息列表做分组：连续的、同一 `runId` + 同一 `agentId` 的 agent 消息归为一组
- 同组非首条消息**完全隐藏**头像和名称行，只保留气泡内容和 token badge
- 同组消息之间间距收紧（4px），组与组之间保持原有间距（16px）
- 每条消息的 per-message token badge 保留不变
- TurnTimeline 仍只在 run 最后一条消息上展示
- 群聊场景（不同 agent 交替发言）自然正确：换 agent 或换 run 时重新显示头像和名称

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: 新增消息分组渲染规则——同 run 同 agent 的连续 agent 消息在视觉上合并显示（隐藏后续头像与名称、收紧间距），同时保留 per-message token 展示

## Impact

- **前端代码**：`src/components/message-list.tsx`（分组逻辑 + 间距）、`src/components/message-item.tsx`（条件隐藏头像/名称）
- **后端**：无改动
- **Event 协议**：无改动（`message.start` / `message.end` 契约不变）
- **DB schema**：无改动（`runId` / `agentId` 字段已存在）
- **Spec 文档**：`openspec/specs/frontend/spec.md` 新增分组渲染需求
