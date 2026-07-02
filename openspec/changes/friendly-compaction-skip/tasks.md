# Tasks

## 后端

- [x] 在 `context_compaction_service.py` 定义 `CompactionSkipped(Exception)`（携带 `reason: str` + `message: MessageRecord`）。
- [x] 抽一个 helper 构造「只广播不落库」的系统消息（`_broadcast_ephemeral_notice`）：生成 `new_message_id()` / `role="system"` / `status="complete"`，`event_bus.publish(MessageAddedEvent(...))`，**不** `db.add`，返回 `MessageRecord`。
- [x] 把 `compact_conversation` 的良性分支从 `raise ValueError` 改为 `raise _skip(...)`（构造文案 → 广播 → CompactionSkipped）。
  - [x] 消息太少 → 文案「当前对话还太短，暂时不需要压缩上下文。」
  - [x] 待压内容过少 → 文案「待压缩的内容太少，压缩收益不明显，暂不压缩。」
  - [x] 无模型 agent（try/except 包住 `_pick_summary_model`）→ 文案「当前会话没有可用于生成摘要的模型 agent，无法压缩上下文。」
- [x] 保留真失败：`会话不存在`、`摘要生成失败：模型返回为空` 仍 `raise ValueError`。

## API

- [x] `conversations.py` compact 端点：`except CompactionSkipped as skip` → `JSONResponse(200, {skipped:true, reason, message})`；`ValueError → 400`、`Exception → 500` 维持。

## 前端

- [x] `src/lib/api.ts`：`CompactConversationResult` 的 `summary`/`ctxBefore`/`ctxAfter` 改可选，新增 `skipped?: boolean`、`reason?: string`。
- [x] `src/components/usage-badge.tsx` `handleCompact`：`skipped` 时 `upsertMessage(message)` 但不 `setCtxOverride`。
- [x] `src/components/message-input.tsx` `executeCompactCommand`：本就只 `upsertMessage(result.message)`，`skipped` 时天然正常，无需改。

## 验证

- [x] 新会话（无消息）点 `/compact`：返回 200 + `skipped:true` + system 消息、无 error —— `test_compact_deferred` 已按新契约更新并通过。
- [x] `ruff check` 通过；`api.ts`/`usage-badge.tsx`/`message-input.tsx` eslint + typecheck 零报错；后端模块导入正常。
- [x] 现有 `test_api_conversations.py`（21 项）全绿，无回归。
- [ ] （手动 UI）刷新页面确认提示消息消失（未落库）。
- [ ] （手动 UI）正常长对话压缩不回归（成功消息 + ctx badge 乐观刷新）。
- [ ] （手动 UI）CLI-only 会话点压缩出现「没有模型 agent」文案，非红错。
