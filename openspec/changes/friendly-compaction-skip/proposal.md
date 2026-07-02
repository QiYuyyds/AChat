# Friendly notice when compaction has nothing to do

## Why

误触「压缩上下文」（新会话或消息太少时点 `/compact` 或 UsageBadge 按钮）时，用户界面**没有任何反馈**，只有浏览器控制台一条红色报错。

根因是「良性无事可做」被当成了「错误」：

- 后端 `compact_conversation` 对「消息太少 / 待压内容过少」`raise ValueError`（`context_compaction_service.py:137/141/152`），API 层统一翻成 **HTTP 400**（`conversations.py:286-288`）。
- 前端两个触发入口的 catch 都只做 `console.error`——`message-input.tsx:614-615` 和 `usage-badge.tsx:51-52`——对用户完全静默。
- 项目没有 toast 系统，友好提示无处可去。

「当前对话还太短，暂时不需要压缩」不是失败，而是一个良性结果。把它当 400 抛出，正是「后台报错」体感的来源。

## What Changes

- **后端区分「良性跳过」与「真失败」**：`compact_conversation` 对良性条件不再 `raise`，而是返回一个带 `skipped=True` + 友好文案的结果；只有真正的异常（会话不存在、摘要生成失败）仍走错误路径。
  - 良性：`没有足够的历史消息可压缩`、`待压缩内容过少，压缩收益不明显`、`当前会话没有配置模型的 agent`（CLI-only 聊天永远压不了，属于「这里做不了」而非崩溃）。
  - 真失败：`会话不存在`、`摘要生成失败：模型返回为空`。
- **友好提示 = 一条只广播、不落库的系统消息**：良性跳过时生成一条 `role="system"` 消息（如「当前对话还太短，暂时不需要压缩上下文。」），经 `event_bus` 广播并随响应返回给前端上屏，**但不写入 `Message` 表**——误触提示是即时性的，不应污染会话历史（刷新即消失，符合预期）。
- **API 返回 200**：`/conversations/{id}/compact` 对良性跳过返回 `200 { skipped: true, reason, message }`，不再是 400。
- **前端不再把误触当错误**：`CompactConversationResult` 增补可选 `skipped` / `reason`；两个 handler 拿到结果照常 `upsertMessage(result.message)` 显示那条系统消息，`skipped` 时**不**调 `setCtxOverride`（没省任何 token）。原有 `console.error` 只在真失败时触发。

## Impact

- Affected specs: `conversation-context`
- Affected code:
  - `backend/app/services/context_compaction_service.py`（引入良性跳过信号 `CompactionSkipped`；良性分支生成不落库的系统消息 + 广播）
  - `backend/app/api/conversations.py`（`except CompactionSkipped` → 200 `{skipped,reason,message}`；真失败仍 400/500）
  - `src/lib/api.ts`（`CompactConversationResult` 加可选 `skipped` / `reason`，其余字段变可选）
  - `src/components/message-input.tsx`、`src/components/usage-badge.tsx`（`skipped` 时不写 ctx 覆盖值、不进 catch）
- 不新增依赖；不改 DB schema；**新增一个「只广播不落库的系统消息」用法**——是现有 `MessageAddedEvent` 广播路径的一个变体（不配对 DB 写入），需在 spec 里明确其临时语义。
