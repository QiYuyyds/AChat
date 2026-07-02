# Surface post-compaction ctx in the usage badge

## Why

点「压缩上下文」后，右上角 UsageBadge 的「当前 ctx」不刷新，要等下一次真实对话才变小。

根因：「当前 ctx」= `total.lastInputTokens`，它派生自「最近一次有 usage 的 run 的 input prompt token 数」——一个**回顾值**（上次真实 LLM 调用发出去的 prompt 大小）。压缩流程 `compact_conversation` 只写 ContextSummary + 一条系统消息，**不跑新的 LLM turn**，所以不产生新 run/usage，`lastInputTokens` 保持不变。压缩真正算出的「压缩后 ctx」(`ctx_after`) 只活在系统消息的文字里，从未作为结构化数据回传给前端。

## What Changes

- 后端 `compact_conversation` 的返回结果结构化携带 `ctxBefore` / `ctxAfter`（值已在函数内算好，只是没暴露）；`/conversations/{id}/compact` 响应新增这两个字段。
- 前端 `CompactConversationResult` 增补 `ctxBefore` / `ctxAfter`。
- app-store 增加 per-conversation 的「压缩后 ctx 覆盖值」`ctxOverrideByConv[convId] = { tokens, at }`，`at` 用压缩系统消息的 `createdAt`（与 DB 时钟一致）。
- `useConversationUsageTotal` 在派生 `lastInputTokens` 后，若存在 `ctxOverride` 且其 `at` 比「最新有 usage 的 run/message 的时间戳」更新，则用 `override.tokens` 覆盖 `lastInputTokens`。
- `UsageBadge.handleCompact` 在 `upsertMessage` 后写入覆盖值。
- 放宽压缩门槛：`KEEP_RECENT_MESSAGES 10→6`、`MIN_COMPACTABLE 4→2`，并新增 `MIN_COMPACT_TOKENS=800` 的 token 底线闸门。改为「按待压内容大小」而非「按消息条数」判定资格——少于十条也能压（内容够大时），纯短对话仍拒压以免浪费一次 LLM 调用。

覆盖值**无需显式清除**：下一次真实 run 的时间戳晚于 `override.at`，比较时自然接管。语义保持「回顾值」——压缩后即时预告「下次将发送的 ctx」，一旦真实对话发生就被实际测量值取代。

## Impact

- Affected specs: `conversation-context`
- Affected code:
  - `backend/app/services/context_compaction_service.py`（`CompactResult` 加两字段）
  - `backend/app/api/conversations.py`（compact 响应加两字段）
  - `src/lib/api.ts`（`CompactConversationResult` 加两字段）
  - `src/stores/app-store.ts`（`ctxOverrideByConv` state + action + `useConversationUsageTotal` 覆盖逻辑）
  - `src/components/usage-badge.tsx`（`handleCompact` 写覆盖值）
- 不碰 StreamEvent 契约、不碰 DB schema、不新增依赖。
